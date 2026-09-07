"""Side-by-side preview of a traced SVG against its source.

The automated gate decides whether an SVG is *written*; this is where a
person decides whether it is *right*. Everything here is plain PIL so it can
be tested without a display - the GUI window is a thin layer that shows the
images this module produces.

The right-hand pane is the SVG rendered by resvg *at the zoom level*, not a
scaled-up bitmap. That is the whole point of the comparison: at 1x the two
look identical, at 4x the raster goes soft and the vector stays crisp.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from PIL import Image

from . import vectorize as vec
from .analysis import ImageStats, analyze
from .engine import OptimizeSettings, prepare_image

ZOOM_LEVELS = (1, 2, 3, 4, 6, 8)
CHECKER = ((200, 200, 200), (240, 240, 240))


@dataclass
class PreviewStats:
    score: Optional[float]
    color_error: Optional[float]
    paths: int
    colors: int
    svg_bytes: int
    raster_bytes: int
    accepted: bool
    reason: str


def _checkerboard(size: Tuple[int, int], cell: int = 12) -> Image.Image:
    """Transparent regions need to read as transparent, not as white."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    mask = ((yy // cell) + (xx // cell)) % 2
    a, b = (np.array(c, np.uint8) for c in CHECKER)
    arr = np.where(mask[..., None] == 0, a, b).astype(np.uint8)
    return Image.fromarray(arr, 'RGB').convert('RGBA')


def composite(img: Image.Image) -> Image.Image:
    return Image.alpha_composite(_checkerboard(img.size), img.convert('RGBA')).convert('RGB')


class TracePreview:
    """Holds a source image and a candidate SVG, and renders both on demand."""

    def __init__(self, source_path: str, svg: Optional[bytes] = None,
                 raster_bytes: int = 0, settings: Optional[OptimizeSettings] = None):
        self.source_path = source_path
        self.settings = settings or OptimizeSettings()
        with Image.open(source_path) as opened:
            opened.load()
            self.stats: ImageStats = analyze(opened)
            corrected, _ = prepare_image(opened, self.settings)
            self.source: Image.Image = corrected.convert('RGBA')
        self.raster_bytes = raster_bytes
        self.svg: Optional[bytes] = svg
        self.result: Optional[vec.VectorResult] = None
        if svg:
            self._score_existing()

    # -- tracing -----------------------------------------------------------
    def _score_existing(self) -> None:
        """Numbers for an SVG that was already written by the run."""
        try:
            rendered = vec.render(self.svg.decode('utf-8'), *self.source.size)
            self.result = vec.VectorResult(
                True, 'written by the run', svg=self.svg,
                score=round(vec.fidelity(self.source, rendered), 4),
                paths=vec.count_paths(self.svg.decode('utf-8')),
                colors=len(vec.dominant_colors(self.source)))
        except Exception as exc:
            self.result = vec.VectorResult(False, f'could not render: {exc}')

    def retrace(self, min_score: float) -> vec.VectorResult:
        """Trace again at a chosen fidelity gate. Nothing is written."""
        self.result = vec.vectorize(self.source, self.stats, raster_size=self.raster_bytes,
                                    min_score=min_score)
        if self.result.accepted:
            self.svg = self.result.svg
        return self.result

    def trace_ignoring_score(self) -> vec.VectorResult:
        """Produce a candidate even when it fails the gate, so it can be seen."""
        return self.retrace(min_score=0.0)

    @property
    def has_candidate(self) -> bool:
        return bool(self.svg)

    # -- rendering ---------------------------------------------------------
    _ROOT_TAG = None   # compiled lazily

    def _svg_region(self, box: Tuple[float, float, float, float],
                    size: Tuple[int, int]) -> Image.Image:
        """Render just ``box`` (source-pixel coords) of the SVG at ``size``.

        resvg renders whole documents, so we hand it a copy whose root tag
        carries a viewBox for the region and the viewport as its size. Cost
        is then proportional to the viewport, not to zoom^2 - at 8x the full
        page would be 37 megapixels.
        """
        import re
        svg = self.svg.decode('utf-8')
        x0, y0, x1, y1 = box
        m = re.search(r'<svg\b[^>]*>', svg)
        if not m:
            raise ValueError('not an SVG')
        root = m.group(0)
        root = re.sub(r'\s(width|height|viewBox)="[^"]*"', '', root)
        root = root[:-1] + (f' width="{size[0]}" height="{size[1]}" '
                            f'viewBox="{x0:.3f} {y0:.3f} {x1 - x0:.3f} {y1 - y0:.3f}" '
                            f'preserveAspectRatio="none">')
        patched = svg[:m.start()] + root + svg[m.end():]
        return vec.render(patched, size[0], size[1])

    def _raster_region(self, box: Tuple[float, float, float, float],
                       size: Tuple[int, int]) -> Image.Image:
        # Bicubic is what a browser does when it upscales a raster - fair.
        return self.source.resize(size, Image.Resampling.BICUBIC, box=box)

    def region_box(self, zoom: float, centre: Tuple[float, float],
                   size: Tuple[int, int]) -> Tuple[float, float, float, float]:
        """Source-pixel box that fills a ``size`` viewport at ``zoom`` around
        ``centre``, clamped to the image."""
        w, h = self.source.size
        vw, vh = size[0] / zoom, size[1] / zoom
        vw, vh = min(vw, w), min(vh, h)
        x0 = min(max(0.0, centre[0] - vw / 2), w - vw)
        y0 = min(max(0.0, centre[1] - vh / 2), h - vh)
        return (x0, y0, x0 + vw, y0 + vh)

    def viewport(self, zoom: float, centre: Tuple[float, float],
                 size: Tuple[int, int], diff: bool = False
                 ) -> Tuple[Image.Image, Image.Image]:
        """Left and right panes for the window, cropped around ``centre``.

        ``centre`` is in source-pixel coordinates (so the same spot stays
        under the cursor as zoom changes). Panes are ``size`` pixels, unless
        the whole image fits at this zoom, in which case they shrink to it.
        """
        box = self.region_box(zoom, centre, size)
        pane = (max(1, round((box[2] - box[0]) * zoom)),
                max(1, round((box[3] - box[1]) * zoom)))
        left = self._raster_region(box, pane)
        if not self.has_candidate:
            return composite(left), composite(left)
        right = self._svg_region(box, pane)
        if diff:
            right = self._diff(left, right)
        return composite(left), composite(right)

    @staticmethod
    def _diff(a_img: Image.Image, b_img: Image.Image, gain: int = 9) -> Image.Image:
        """Where the SVG differs from the source, amplified. Black = identical."""
        a = np.asarray(composite(a_img), np.int16)
        b = np.asarray(composite(b_img), np.int16)
        d = np.abs(a - b).max(axis=2)
        amp = np.clip(d.astype(np.float32) * gain, 0, 255).astype(np.uint8)
        rgb = np.zeros(amp.shape + (3,), np.uint8)
        rgb[..., 0] = amp
        rgb[..., 1] = (amp * 0.35).astype(np.uint8)
        return Image.fromarray(rgb, 'RGB').convert('RGBA')

    # -- numbers -----------------------------------------------------------
    def stats_summary(self) -> PreviewStats:
        if not self.has_candidate or self.result is None:
            return PreviewStats(None, None, 0, 0, 0, self.raster_bytes, False,
                                self.result.reason if self.result else 'not traced')
        try:
            rendered = vec.render(self.svg.decode('utf-8'), *self.source.size)
            color_err = vec.color_error(self.source, rendered)
        except Exception:
            color_err = None
        return PreviewStats(self.result.score, color_err, self.result.paths,
                            self.result.colors, len(self.svg), self.raster_bytes,
                            self.result.accepted, self.result.reason)

    # -- decisions ---------------------------------------------------------
    def keep(self, dest: str) -> str:
        """Write the current candidate to disk."""
        if not self.has_candidate:
            raise ValueError('nothing to keep')
        os.makedirs(os.path.dirname(dest) or '.', exist_ok=True)
        with open(dest, 'wb') as fh:
            fh.write(self.svg)
        return dest

    @staticmethod
    def discard(dest: str) -> bool:
        """Remove a written SVG. Returns whether a file was removed."""
        try:
            os.remove(dest)
            return True
        except FileNotFoundError:
            return False
