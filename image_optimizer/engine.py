"""Single-image optimisation: decode, correct, encode, verify.

The design rule here is *never ship a worse file than you started with*.
Every path ends in a measured comparison against the source bytes, and the
original is copied through when nothing we produced is actually smaller.
"""
from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from . import formats as fmt
from .analysis import GRAPHIC, TEXT_SCREENSHOT, ImageStats, Recommendation, analyze, recommend
from .quality import (comparison_plane, decode_bytes, resolve_target,
                      search_quality, visual_score)

MODE_SMART = 'smart'
MODE_FIXED = 'fixed'
MODE_LOSSLESS = 'lossless'


@dataclass
class OptimizeSettings:
    """Everything the encoder needs. Serialisable straight to JSON."""
    output_format: str = 'auto'          # webp | avif | jpeg | png | auto
    mode: str = MODE_SMART               # smart | fixed | lossless
    target: str = 'balanced'             # perceptual target for smart mode
    quality: int = 80                    # used by fixed mode
    effort: int = 4                      # 0-6, encoder CPU budget
    auto_settings: bool = True           # let content analysis pick per image
    strip_metadata: bool = True
    convert_to_srgb: bool = True
    max_width: Optional[int] = None
    max_height: Optional[int] = None
    widths: Tuple[int, ...] = ()         # responsive variants
    never_larger: bool = True
    skip_existing: bool = False
    recursive: bool = True
    keep_animation: bool = True
    jpeg_background: str = '#ffffff'     # flatten colour when dropping alpha
    vectorize: bool = False              # also trace flat graphics to SVG
    vector_min_score: float = 0.95       # SSIM the trace must reach to be kept

    def to_dict(self) -> Dict[str, object]:
        d = dict(self.__dict__)
        d['widths'] = list(self.widths)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> 'OptimizeSettings':
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        if 'widths' in known and known['widths'] is not None:
            known['widths'] = tuple(int(w) for w in known['widths'])
        return cls(**known)


@dataclass
class Variant:
    path: str
    width: int
    height: int
    size: int
    format_key: str
    quality: int
    lossless: bool
    score: Optional[float] = None      # SSIM vs source, None when not measured

    @property
    def mime(self) -> str:
        return fmt.resolve(self.format_key).mime

    def to_dict(self) -> Dict[str, object]:
        d = dict(self.__dict__)
        d['mime'] = self.mime
        return d


@dataclass
class FileResult:
    source: str
    ok: bool = False
    skipped: bool = False
    copied: bool = False               # original passed through unchanged
    error: Optional[str] = None
    note: Optional[str] = None
    original_size: int = 0
    elapsed: float = 0.0
    stats: Optional[ImageStats] = None
    decision: Optional[str] = None     # why these settings were chosen
    variants: List[Variant] = field(default_factory=list)
    alt_text: Optional[str] = None
    seo_filename: Optional[str] = None
    vector_note: Optional[str] = None  # why an SVG was or was not produced

    @property
    def primary(self) -> Optional[Variant]:
        # The full-size raster. Never the SVG, which is an extra alongside it.
        for variant in self.variants:
            if variant.format_key != 'svg':
                return variant
        return None

    @property
    def vector(self) -> Optional[Variant]:
        return next((v for v in self.variants if v.format_key == 'svg'), None)

    @property
    def new_size(self) -> int:
        return self.primary.size if self.primary else 0

    @property
    def saved_bytes(self) -> int:
        return self.original_size - self.new_size if self.primary else 0

    @property
    def saved_ratio(self) -> float:
        if not self.original_size or not self.primary:
            return 0.0
        return self.saved_bytes / self.original_size

    def to_dict(self) -> Dict[str, object]:
        return {
            'source': self.source,
            'ok': self.ok,
            'skipped': self.skipped,
            'copied': self.copied,
            'error': self.error,
            'note': self.note,
            'original_size': self.original_size,
            'new_size': self.new_size,
            'saved_bytes': self.saved_bytes,
            'saved_ratio': round(self.saved_ratio, 4),
            'elapsed': round(self.elapsed, 3),
            'decision': self.decision,
            'analysis': self.stats.to_dict() if self.stats else None,
            'variants': [v.to_dict() for v in self.variants],
            'alt_text': self.alt_text,
            'seo_filename': self.seo_filename,
            'vector_note': self.vector_note,
        }


# --------------------------------------------------------------------------
# Decoding / correction
# --------------------------------------------------------------------------

_SRGB_PROFILE = None


def _srgb_profile():
    global _SRGB_PROFILE
    if _SRGB_PROFILE is None:
        _SRGB_PROFILE = ImageCms.createProfile('sRGB')
    return _SRGB_PROFILE


def prepare_image(img: Image.Image, settings: OptimizeSettings) -> Tuple[Image.Image, Optional[bytes]]:
    """Apply orientation and colour corrections before encoding.

    Returns the corrected image and the ICC profile that should be attached
    to the output (``None`` once we have converted the pixels to sRGB).
    """
    icc = img.info.get('icc_profile')

    # Rotate per EXIF. Skipping this is why "my photos came out sideways".
    try:
        img = ImageOps.exif_transpose(img) or img
    except Exception:
        pass

    if icc and settings.convert_to_srgb:
        try:
            src_profile = ImageCms.ImageCmsProfile(BytesIO(icc))
            if ImageCms.getProfileName(src_profile).strip().lower().startswith('srgb'):
                return img, (None if settings.strip_metadata else icc)
            target_mode = 'RGBA' if img.mode in ('RGBA', 'LA', 'PA') else 'RGB'
            img = ImageCms.profileToProfile(
                img, src_profile, _srgb_profile(),
                outputMode=target_mode, renderingIntent=0)
            return img, None       # pixels are sRGB now; no profile needed
        except Exception:
            pass                   # unreadable profile - fall through

    return img, (None if settings.strip_metadata else icc)


def _flatten_alpha(img: Image.Image, background: str) -> Image.Image:
    if img.mode not in ('RGBA', 'LA', 'PA') and 'transparency' not in img.info:
        return img.convert('RGB') if img.mode != 'RGB' else img
    rgba = img.convert('RGBA')
    bg = Image.new('RGBA', rgba.size, background)
    return Image.alpha_composite(bg, rgba).convert('RGB')


def _fit(img: Image.Image, max_w: Optional[int], max_h: Optional[int]) -> Image.Image:
    """Downscale to fit a box. Never upscales."""
    if not max_w and not max_h:
        return img
    w, h = img.size
    scale = min((max_w / w) if max_w else 1.0, (max_h / h) if max_h else 1.0, 1.0)
    if scale >= 1.0:
        return img
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                      Image.Resampling.LANCZOS)


def resize_to_width(img: Image.Image, width: int) -> Image.Image:
    if width >= img.size[0]:
        return img
    height = max(1, round(img.size[1] * width / img.size[0]))
    return img.resize((width, height), Image.Resampling.LANCZOS)


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------

def _save_kwargs(spec: fmt.FormatSpec, quality: int, lossless: bool, effort: int,
                 has_alpha: bool, sharp_edges: bool) -> Dict[str, object]:
    opts: Dict[str, object] = dict(spec.base_options)
    opts['format'] = spec.pil_format

    if spec.key == 'webp':
        opts['method'] = max(0, min(6, effort))
        if lossless:
            # In lossless mode WebP reuses `quality` as a compression-effort
            # dial, and `exact` stops it mangling fully transparent pixels.
            opts.update(lossless=True, quality=100, exact=has_alpha)
        else:
            opts.update(lossless=False, quality=quality)
            if has_alpha:
                opts['alpha_quality'] = 100
    elif spec.key == 'avif':
        # AVIF speed is inverted relative to our effort dial: 0 = slowest.
        opts['speed'] = max(0, min(10, 10 - effort))
        opts['quality'] = 100 if lossless else quality
        if lossless or sharp_edges or quality >= 90:
            opts['subsampling'] = '4:4:4'
    elif spec.key == 'jpeg':
        opts['quality'] = max(1, min(100, quality))
        opts['subsampling'] = 0 if (sharp_edges or quality >= 90) else 2
    elif spec.key == 'png':
        opts['compress_level'] = 9 if effort >= 4 else 6

    return opts


def encode(img: Image.Image, spec: fmt.FormatSpec, quality: int, lossless: bool,
           settings: OptimizeSettings, sharp_edges: bool = False,
           icc: Optional[bytes] = None, exif: Optional[bytes] = None,
           animated: bool = False) -> bytes:
    """Encode one image to bytes in the requested format."""
    work = img
    if not spec.supports_alpha:
        work = _flatten_alpha(work, settings.jpeg_background)
    elif work.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P'):
        work = work.convert('RGBA' if 'A' in work.mode else 'RGB')

    has_alpha = work.mode in ('RGBA', 'LA', 'PA') or 'transparency' in work.info
    opts = _save_kwargs(spec, quality, lossless, settings.effort, has_alpha, sharp_edges)

    if spec.key == 'png' and lossless:
        # A flat graphic in 8-bit palette form is a fraction of truecolour PNG
        # and pixel-identical when it fits in 256 colours.
        try:
            if work.mode in ('RGB', 'RGBA'):
                colors = work.getcolors(256)
                if colors:
                    work = work.convert(
                        'P', palette=Image.Palette.ADAPTIVE,
                        colors=len(colors)) if not has_alpha else work.quantize(
                        colors=len(colors), method=Image.Quantize.FASTOCTREE)
        except Exception:
            pass

    if not settings.strip_metadata:
        if icc:
            opts['icc_profile'] = icc
        if exif:
            opts['exif'] = exif
    if animated and spec.supports_animation:
        opts['save_all'] = True

    buf = BytesIO()
    work.save(buf, **opts)
    return buf.getvalue()


def _candidate_formats(settings: OptimizeSettings, stats: ImageStats) -> List[fmt.FormatSpec]:
    usable = fmt.available_formats()
    if settings.output_format == 'auto':
        keys = list(fmt.auto_candidates())
    else:
        key = settings.output_format
        if key not in usable:
            raise ValueError(fmt.missing_format_hint(key))
        keys = [key]
    specs = [fmt.resolve(k) for k in keys]
    if stats.has_alpha:
        specs = [s for s in specs if s.supports_alpha] or specs
    return specs


def _encode_best(img: Image.Image, stats: ImageStats, rec: Recommendation,
                 settings: OptimizeSettings, icc: Optional[bytes],
                 exif: Optional[bytes]) -> Tuple[bytes, fmt.FormatSpec, int, bool, Optional[float]]:
    """Encode with every candidate format/mode and keep the smallest result."""
    sharp = stats.kind in (GRAPHIC, TEXT_SCREENSHOT)
    animated = stats.is_animated and settings.keep_animation
    specs = _candidate_formats(settings, stats)

    reference = None
    if settings.mode == MODE_SMART and not animated:
        reference = comparison_plane(img)
    target_score = resolve_target(settings.target)

    best: Optional[Tuple[bytes, fmt.FormatSpec, int, bool, Optional[float]]] = None

    for spec in specs:
        trials: List[Tuple[int, bool]] = []
        if settings.mode == MODE_LOSSLESS and spec.supports_lossless:
            trials.append((100, True))
        elif animated:
            trials.append((rec.quality if settings.auto_settings else settings.quality, False))
        elif settings.mode == MODE_FIXED:
            trials.append((settings.quality, False))
            if settings.auto_settings and rec.lossless and spec.supports_lossless:
                trials.append((100, True))
        else:  # smart
            if settings.auto_settings and (rec.lossless or rec.near_lossless) \
                    and spec.supports_lossless:
                trials.append((100, True))     # graphics: lossless often wins outright
            trials.append((None, False))       # None => run the perceptual search

        for quality, lossless in trials:
            try:
                if quality is None:
                    low, high = _search_bounds(rec, settings)
                    # Search with a cheap encoder setting - the quality/size
                    # curve barely moves with effort, but the encode time does.
                    # Only the winning quality is re-encoded at full effort.
                    probe = replace(settings, effort=min(settings.effort, 2))
                    result = search_quality(
                        lambda q: encode(img, spec, q, False, probe, sharp, icc, exif, animated),
                        decode_bytes, reference, target_score, low=low, high=high,
                        fallback_quality=rec.quality if settings.auto_settings else None)
                    payload, q_used, score = result.payload, result.quality, result.score
                    if settings.effort > probe.effort:
                        final = encode(img, spec, q_used, False, settings, sharp,
                                       icc, exif, animated)
                        if len(final) <= len(payload):
                            payload = final
                            score = visual_score(reference, decode_bytes(final))
                else:
                    payload = encode(img, spec, quality, lossless, settings, sharp,
                                     icc, exif, animated)
                    q_used, score = quality, None
                    if reference is not None and not lossless:
                        score = visual_score(reference, decode_bytes(payload))
            except Exception:
                continue
            if best is None or len(payload) < len(best[0]):
                best = (payload, spec, q_used, lossless, score)

    if best is None:
        raise RuntimeError('no output format could encode this image')
    return best


def _search_bounds(rec: Recommendation, settings: OptimizeSettings) -> Tuple[int, int]:
    if not settings.auto_settings:
        return 40, 96
    # Content analysis sets the floor so we never sand the detail off a
    # screenshot just because SSIM tolerated it.
    floor = max(30, rec.quality - 30)
    ceiling = min(98, max(rec.quality + 12, floor + 20))
    return floor, ceiling


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def output_path_for(source: str, input_root: str, output_root: str,
                    spec: fmt.FormatSpec, suffix: str = '') -> str:
    rel = os.path.relpath(source, input_root)
    stem = os.path.splitext(rel)[0]
    return os.path.join(output_root, f'{stem}{suffix}{spec.extension}')


def existing_is_current(source: str, input_root: str, output_root: str,
                       settings: OptimizeSettings) -> Optional[str]:
    """Path of an already-current output for this source, if there is one.

    Checked *before* decoding: an incremental build should cost a stat() per
    file, not a full encode that is then thrown away.
    """
    if not settings.skip_existing:
        return None
    try:
        source_mtime = os.path.getmtime(source)
    except OSError:
        return None

    if settings.output_format == 'auto':
        keys = fmt.auto_candidates()
    else:
        keys = (settings.output_format,)

    # Only widths narrower than the source produce a file, so only those count
    # as "missing". Reading the header is cheap - Pillow does not decode here.
    widths = sorted(set(settings.widths), reverse=True)
    if widths:
        try:
            with Image.open(source) as probe:
                source_width = probe.size[0]
            if settings.max_width:
                source_width = min(source_width, settings.max_width)
        except Exception:
            return None
        widths = [w for w in widths if w < source_width]
    suffixes = [''] + [f'-{w}w' for w in widths]

    for key in keys:
        try:
            spec = fmt.resolve(key)
        except ValueError:
            continue
        paths = [output_path_for(source, input_root, output_root, spec, sfx)
                 for sfx in suffixes]
        try:
            if all(os.path.getmtime(p) >= source_mtime for p in paths):
                return paths[0]
        except OSError:
            continue
    return None


def optimize_file(source: str, input_root: str, output_root: str,
                  settings: OptimizeSettings) -> FileResult:
    """Optimise one file and write every requested variant to disk."""
    started = time.time()
    result = FileResult(source=source)
    try:
        result.original_size = os.path.getsize(source)
    except OSError as exc:
        result.error = f'cannot read source: {exc}'
        return result

    current = existing_is_current(source, input_root, output_root, settings)
    if current:
        result.ok = True
        result.skipped = True
        result.note = 'output is up to date'
        result.elapsed = time.time() - started
        return result

    try:
        with Image.open(source) as opened:
            opened.load()
            stats = analyze(opened)
            result.stats = stats
            img, icc = prepare_image(opened, settings)
            exif = opened.info.get('exif') if not settings.strip_metadata else None
            img = _fit(img, settings.max_width, settings.max_height)

            rec = recommend(stats, settings.output_format, settings.target)
            result.decision = rec.reason if settings.auto_settings else (
                f'Manual settings: {settings.mode}, quality {settings.quality}.')

            plan: List[Tuple[str, Image.Image]] = [('', img)]
            for width in sorted(set(settings.widths), reverse=True):
                if width < img.size[0]:
                    plan.append((f'-{width}w', resize_to_width(img, width)))

            written: List[Variant] = []
            for suffix, variant_img in plan:
                # Re-analyse each resized variant: resampling a flat graphic
                # or text introduces antialiasing, so the original's
                # "lossless wins here" verdict stops being true at 400px.
                variant_stats = stats if not suffix else analyze(variant_img)
                variant_rec = rec if not suffix else recommend(
                    variant_stats, settings.output_format, settings.target)
                payload, spec, quality, lossless, score = _encode_best(
                    variant_img, variant_stats, variant_rec, settings, icc, exif)
                dest = output_path_for(source, input_root, output_root, spec, suffix)

                # The whole point is smaller files. If we made a bigger one and
                # this is the full-size variant, ship the original instead.
                if (settings.never_larger and not suffix
                        and len(payload) >= result.original_size
                        and not settings.widths):
                    os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
                    fallback = os.path.join(
                        os.path.dirname(dest),
                        os.path.basename(os.path.splitext(source)[0]) +
                        os.path.splitext(source)[1])
                    shutil.copy2(source, fallback)
                    result.copied = True
                    result.ok = True
                    result.note = ('already smaller than anything we could encode - '
                                   'original copied through')
                    written.append(Variant(fallback, img.size[0], img.size[1],
                                           result.original_size,
                                           _source_format_key(source), 0, False, None))
                    break

                os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
                with open(dest, 'wb') as fh:
                    fh.write(payload)
                written.append(Variant(dest, variant_img.size[0], variant_img.size[1],
                                       len(payload), spec.key, quality, lossless,
                                       round(score, 4) if score is not None else None))

            result.variants = written
            result.ok = True

            if settings.vectorize and written and not result.copied:
                _write_vector(result, img, stats, source, input_root, output_root,
                              settings)
    except UnidentifiedImageError:
        result.error = 'not a readable image (corrupt or unsupported)'
    except MemoryError:
        result.error = 'ran out of memory - image is too large for this machine'
    except Exception as exc:
        result.error = f'{type(exc).__name__}: {exc}'

    result.elapsed = time.time() - started
    return result


def _write_vector(result: FileResult, img: Image.Image, stats: ImageStats,
                  source: str, input_root: str, output_root: str,
                  settings: OptimizeSettings) -> None:
    """Trace to SVG alongside the raster, keeping it only if it verifies."""
    from . import vectorize as vec   # optional deps live behind this import

    primary = result.primary
    outcome = vec.vectorize(img, stats, raster_size=primary.size if primary else 0,
                            min_score=settings.vector_min_score)
    if not outcome.accepted:
        result.vector_note = f'no SVG: {outcome.reason}'
        return

    dest = output_path_for(source, input_root, output_root, fmt.SVG_SPEC)
    os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
    with open(dest, 'wb') as fh:
        fh.write(outcome.svg)
    result.variants.append(Variant(dest, img.size[0], img.size[1], outcome.size,
                                   'svg', 100, True, outcome.score))
    result.vector_note = f'SVG: {outcome.reason}'


def _source_format_key(source: str) -> str:
    ext = os.path.splitext(source)[1].lower()
    for key, spec in fmt.FORMATS.items():
        if spec.extension == ext:
            return key
    return {'.jpeg': 'jpeg', '.jpe': 'jpeg', '.jfif': 'jpeg', '.tif': 'png'}.get(ext, 'jpeg')
