"""Raster -> SVG for logos and flat graphics, with a fidelity gate.

Tracing is deterministic geometry, not a learned task: find colour regions,
fit curves to their boundaries. On clean flat art that is already excellent
and no model improves on it. What decides whether the feature is any good is
everything *around* the tracer:

* pre-cleaning, so a JPEG-artefacted logo becomes 4 paths instead of 96;
* re-rendering the SVG and scoring it against the source with SSIM, so a
  trace that garbled a shape is rejected rather than shipped;
* refusing photos and gradients outright (the classifier already knows).

Everything here is free and local: ``vtracer`` traces, ``resvg`` renders for
scoring. Both are optional imports; without them the feature reports itself
unavailable and the rest of the tool is unaffected.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from .analysis import GRAPHIC, ImageStats
from .quality import _to_luma, ssim

# Content kinds that are allowed to become SVG. Screenshots trace into
# thousands of glyph outlines and photos into megabytes of noise, so both are
# refused before any work happens.
VECTORIZABLE_KINDS = (GRAPHIC,)

# Traced output is only worth keeping when it actually looks like the source.
# Clean artwork scores 0.98+. A JPEG-degraded logo tops out around 0.95 no
# matter how the tracer is tuned - much of the gap is the compression noise
# a good trace correctly refuses to reproduce - while a trace that has
# actually garbled a shape falls well below 0.90. So 0.95 keeps usable traces
# of imperfect sources and still rejects broken ones.
DEFAULT_MIN_SCORE = 0.95
# ...and when it is not a path explosion. A logo is a few dozen paths; hundreds
# means the tracer is drawing noise.
DEFAULT_MAX_PATHS = 300
# Also refuse an SVG that is wildly larger than the raster we already have -
# scalable is nice, 6x the bytes is not. The ratio only applies above an
# absolute floor: a lossless WebP of a two-colour shape can be 500 bytes, and
# no vector competes with that on bytes - a 4 KB SVG that renders sharp at
# every size is still the better asset.
DEFAULT_MAX_SIZE_RATIO = 4.0
SIZE_RATIO_FLOOR = 32 * 1024
# Share of opaque pixels allowed to come out a clearly different colour.
# Antialiased edges account for well under 1%; a recoloured wordmark is 3%+.
DEFAULT_MAX_COLOR_ERROR = 0.015

# Working size for the trace. Tracing a 4000px logo gains nothing over 1200px
# and costs seconds; scoring happens at this size too.
TRACE_MAX_DIM = 1200


@dataclass
class VectorResult:
    accepted: bool
    reason: str
    svg: Optional[bytes] = None
    score: Optional[float] = None
    paths: int = 0
    colors: int = 0

    @property
    def size(self) -> int:
        return len(self.svg) if self.svg else 0


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------

def tracer_available() -> bool:
    try:
        import vtracer  # noqa: F401
        return True
    except ImportError:
        return False


def renderer_available() -> bool:
    try:
        import resvg_py  # noqa: F401
        return True
    except ImportError:
        return False


def available() -> bool:
    return tracer_available() and renderer_available()


def availability_hint() -> Optional[str]:
    """Why SVG output cannot run, or None if it can."""
    missing = []
    if not tracer_available():
        missing.append('vtracer')
    if not renderer_available():
        missing.append('resvg-py')
    if not missing:
        return None
    return (f'SVG output needs {" and ".join(missing)}. '
            f'Install with `pip install {" ".join(missing)}`.')


# --------------------------------------------------------------------------
# Pipeline stages
# --------------------------------------------------------------------------

def prepare_for_trace(img: Image.Image, stats: ImageStats) -> Tuple[Image.Image, int]:
    """Clean the raster so the tracer sees shapes, not compression noise.

    Returns the prepared RGBA image and the palette size it was reduced to.
    Quantising to a small palette is the single biggest quality lever: JPEG
    ringing around a two-colour logo becomes dozens of near-colours, each of
    which the tracer would faithfully outline.
    """
    work = img.convert('RGBA')
    if max(work.size) > TRACE_MAX_DIM:
        work = work.copy()
        work.thumbnail((TRACE_MAX_DIM, TRACE_MAX_DIM), Image.Resampling.LANCZOS)

    alpha = work.getchannel('A')
    palette = dominant_colors(work)
    colors = len(palette)
    # Snap every opaque pixel to its nearest real colour. Letting median-cut
    # pick N colours itself lets a small-but-real colour (white lettering on a
    # big blue field) get merged into a neighbour.
    quantised = _snap_to_palette(work.convert('RGB'), palette)
    # Hard-threshold alpha: semi-transparent antialias fringes would otherwise
    # become their own translucent paths around every edge.
    alpha = alpha.point(lambda a: 255 if a >= 128 else 0)
    quantised.putalpha(alpha)
    return quantised, colors


def dominant_colors(rgba: Image.Image, probe: int = 32, min_share: float = 0.005,
                    merge_distance: float = 40.0) -> List[Tuple[int, int, int]]:
    """The colours that actually make up this artwork, most common first.

    The analyser's raw colour count is useless here: JPEG ringing around a
    four-colour logo produces hundreds of near-colours, and tracing at that
    palette size outlines the noise as dozens of paths.

    So: quantise the *opaque* pixels to a generous probe palette, merge every
    bin into the nearest larger bin when the two colours are close (ringing
    and antialiasing sit right next to the colour they came from), and keep
    the merged clusters that cover a meaningful share of pixels. Transparent
    pixels are excluded so they cannot claim a palette slot.
    """
    rgba = rgba.convert('RGBA')
    alpha = np.asarray(rgba.getchannel('A'))
    opaque = alpha >= 128
    pixels = np.asarray(rgba.convert('RGB'))[opaque]
    if pixels.size == 0:
        return [(0, 0, 0)]
    # A flat strip is enough for quantize(); shape does not matter.
    strip = Image.fromarray(pixels.reshape(1, -1, 3), 'RGB')
    probed = strip.quantize(colors=probe, method=Image.Quantize.MEDIANCUT,
                            dither=Image.Dither.NONE)
    counts = probed.histogram()[:probe]
    palette = probed.getpalette()[:probe * 3]
    entries = [(counts[i], np.array(palette[i * 3:i * 3 + 3], dtype=np.float32))
               for i in range(probe) if counts[i] > 0]
    entries.sort(key=lambda e: -e[0])

    clusters: list = []     # [count, colour]
    for count, colour in entries:
        for cluster in clusters:
            if np.linalg.norm(cluster[1] - colour) <= merge_distance:
                cluster[0] += count
                break
        else:
            clusters.append([count, colour])

    total = float(sum(c for c, _ in entries)) or 1.0
    kept = [tuple(int(v) for v in colour) for count, colour in clusters
            if count / total >= min_share]
    return kept[:24] or [tuple(int(v) for v in clusters[0][1])]


def _dominant_color_count(rgb: Image.Image) -> int:
    """Kept for callers that only want the number."""
    return max(4, min(24, len(dominant_colors(rgb))))


def _snap_to_palette(rgb: Image.Image, palette: List[Tuple[int, int, int]]) -> Image.Image:
    # int32, not int16: squared channel differences reach 65025, which
    # overflows int16 and silently scrambles which colour is "nearest".
    arr = np.asarray(rgb, dtype=np.int32)
    pal = np.array(palette, dtype=np.int32)                       # (k, 3)
    dist = ((arr[:, :, None, :] - pal[None, None, :, :]) ** 2).sum(axis=3)
    nearest = dist.argmin(axis=2)
    return Image.fromarray(pal[nearest].astype(np.uint8), 'RGB')


def color_error(original: Image.Image, rendered: Image.Image,
                threshold: int = 60) -> float:
    """Fraction of opaque source pixels whose colour the render got wrong.

    SSIM runs on luma and averages over the frame, so it barely notices when
    a small region - lettering, a badge - comes out the wrong colour. This
    catches exactly that: any pixel whose RGB moved by more than ``threshold``
    counts, and more than a percent or two of them means a region was
    recoloured rather than an edge antialiased.
    """
    a = original.convert('RGBA')
    b = rendered.convert('RGBA')
    if b.size != a.size:
        b = b.resize(a.size, Image.Resampling.BILINEAR)
    opaque = np.asarray(a.getchannel('A')) >= 128
    if not opaque.any():
        return 0.0
    diff = np.abs(np.asarray(a.convert('RGB'), np.int16)
                  - np.asarray(b.convert('RGB'), np.int16)).max(axis=2)
    return float((diff[opaque] > threshold).mean())


def _speckle_for(size: Tuple[int, int]) -> int:
    """Minimum blob area (px) to keep, scaled to the working size."""
    area = size[0] * size[1]
    return max(8, min(64, int(area / 40000)))


def trace(img: Image.Image, stats: ImageStats) -> Tuple[str, int]:
    """Trace a prepared RGBA image. Returns SVG text and the palette size."""
    import vtracer

    prepared, colors = prepare_for_trace(img, stats)
    svg = vtracer.convert_pixels_to_svg(
        list(prepared.getdata()), prepared.size,
        colormode='color',
        hierarchical='stacked',
        mode='spline',
        filter_speckle=_speckle_for(prepared.size),
        color_precision=6,
        layer_difference=16,
        corner_threshold=60,
        length_threshold=4.0,
        max_iterations=10,
        splice_threshold=45,
        path_precision=2,
    )
    return svg, colors


def render(svg: str, width: int, height: int) -> Image.Image:
    """Rasterise an SVG to RGBA at the given size."""
    import resvg_py
    png = resvg_py.svg_to_bytes(svg_string=svg, width=width, height=height)
    out = Image.open(BytesIO(bytes(png)))
    out.load()
    return out.convert('RGBA')


def fidelity(original: Image.Image, rendered: Image.Image) -> float:
    """SSIM between source and re-rendered trace, alpha composited on grey."""
    if rendered.size != original.size:
        rendered = rendered.resize(original.size, Image.Resampling.BILINEAR)
    return ssim(_to_luma(original.convert('RGBA')), _to_luma(rendered))


_XML_DECL = re.compile(r'<\?xml[^>]*\?>\s*')
_COMMENT = re.compile(r'<!--.*?-->\s*', re.S)
_WHITESPACE = re.compile(r'>\s+<')


def minify(svg: str) -> str:
    """Cheap, dependency-free shrink; `svgo` does better when it is installed."""
    svg = _XML_DECL.sub('', svg)
    svg = _COMMENT.sub('', svg)
    svg = _WHITESPACE.sub('><', svg)
    return svg.strip()


def optimise_with_svgo(svg: str) -> Optional[str]:
    """Run svgo if it is on PATH. Never required; returns None otherwise."""
    exe = shutil.which('svgo')
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, '--multipass', '-i', '-', '-o', '-'],
                              input=svg, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and proc.stdout.strip().startswith('<svg'):
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def count_paths(svg: str) -> int:
    return svg.count('<path')


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def vectorize(img: Image.Image, stats: ImageStats,
              raster_size: int,
              min_score: float = DEFAULT_MIN_SCORE,
              max_paths: int = DEFAULT_MAX_PATHS,
              max_size_ratio: float = DEFAULT_MAX_SIZE_RATIO,
              max_color_error: float = DEFAULT_MAX_COLOR_ERROR) -> VectorResult:
    """Trace, verify, and decide. Never raises; refusals carry a reason.

    ``raster_size`` is the byte size of the best raster we already produced,
    used to refuse SVGs that are scalable but absurdly heavy.
    """
    if stats.kind not in VECTORIZABLE_KINDS:
        return VectorResult(False, f'not flat artwork ({stats.kind_label}) - '
                                   'tracing would produce noise, not shapes')
    if stats.is_animated:
        return VectorResult(False, 'animated images are not traced')
    hint = availability_hint()
    if hint:
        return VectorResult(False, hint)

    try:
        svg, colors = trace(img, stats)
    except Exception as exc:
        return VectorResult(False, f'tracer failed: {type(exc).__name__}: {exc}')

    paths = count_paths(svg)
    if paths == 0:
        return VectorResult(False, 'tracer produced no paths', paths=0, colors=colors)
    if paths > max_paths:
        return VectorResult(False, f'{paths} paths - the tracer is outlining noise, '
                                   f'not artwork (limit {max_paths})',
                            paths=paths, colors=colors)

    # Score at the working size so a 4000px source does not need a 4000px render.
    reference = img.convert('RGBA')
    if max(reference.size) > TRACE_MAX_DIM:
        reference = reference.copy()
        reference.thumbnail((TRACE_MAX_DIM, TRACE_MAX_DIM), Image.Resampling.LANCZOS)
    try:
        rendered = render(svg, reference.size[0], reference.size[1])
        score = fidelity(reference, rendered)
    except Exception as exc:
        return VectorResult(False, f'could not render SVG for verification: '
                                   f'{type(exc).__name__}: {exc}', paths=paths, colors=colors)

    wrong = color_error(reference, rendered)
    if wrong > max_color_error:
        return VectorResult(False, f'{wrong * 100:.1f}% of pixels came out the wrong '
                                   f'colour - a region was recoloured, not just '
                                   f'antialiased', score=round(score, 4),
                            paths=paths, colors=colors)

    if score < min_score:
        return VectorResult(False, f'trace does not match the source closely enough '
                                   f'(SSIM {score:.3f} < {min_score}; lower the SVG '
                                   f'fidelity setting to accept it anyway)',
                            score=round(score, 4), paths=paths, colors=colors)

    optimised = optimise_with_svgo(svg) or minify(svg)
    payload = optimised.encode('utf-8')

    if (raster_size and len(payload) > SIZE_RATIO_FLOOR
            and len(payload) > raster_size * max_size_ratio):
        return VectorResult(False, f'SVG is {len(payload) / raster_size:.1f}x the '
                                   f'raster - too heavy to be worth serving',
                            score=round(score, 4), paths=paths, colors=colors)

    return VectorResult(True, f'{paths} paths, {colors} colours, SSIM {score:.3f}',
                        svg=payload, score=round(score, 4), paths=paths, colors=colors)
