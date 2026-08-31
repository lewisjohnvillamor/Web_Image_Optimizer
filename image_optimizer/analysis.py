"""Content analysis: work out *what kind of image* this is before encoding it.

A photograph and a flat UI screenshot want completely different encoder
settings. Blanket "quality 80 for everything" wrecks screenshots (ringing
around text) and wastes bytes on photos. This module looks at the pixels and
returns a recommendation the encoder can act on.

Everything runs on a small thumbnail, so cost is ~1 ms per image.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional

import numpy as np
from PIL import Image

ANALYSIS_THUMB = 256

PHOTO = 'photo'
ILLUSTRATION = 'illustration'
GRAPHIC = 'graphic'          # logos, flat UI, charts
TEXT_SCREENSHOT = 'screenshot'   # UI captures with a lot of small text

KIND_LABELS = {
    PHOTO: 'Photo',
    ILLUSTRATION: 'Illustration',
    GRAPHIC: 'Flat graphic / logo',
    TEXT_SCREENSHOT: 'Screenshot with text',
}


@dataclass
class ImageStats:
    width: int
    height: int
    kind: str
    has_alpha: bool
    binary_alpha: bool          # alpha is only 0 or 255 -> cheap to encode
    is_grayscale: bool
    unique_colors: int
    flat_ratio: float           # fraction of neighbouring pixels that match exactly
    edge_density: float         # fraction of pixels on a strong edge
    high_frequency: float       # mean gradient magnitude, normalised 0-1
    mean_saturation: float
    is_animated: bool
    frame_count: int

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def to_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d['kind_label'] = self.kind_label
        return d


@dataclass
class Recommendation:
    """What the analyser thinks the encoder should do."""
    format_key: str
    lossless: bool
    quality: int
    near_lossless: bool
    reason: str


def _thumbnail_array(img: Image.Image) -> np.ndarray:
    small = img.convert('RGB')
    small.thumbnail((ANALYSIS_THUMB, ANALYSIS_THUMB), Image.Resampling.BILINEAR)
    return np.asarray(small, dtype=np.float32)


def analyze(img: Image.Image) -> ImageStats:
    """Classify an opened Pillow image. Never raises on odd inputs."""
    width, height = img.size
    frame_count = getattr(img, 'n_frames', 1)
    is_animated = bool(getattr(img, 'is_animated', False))

    has_alpha = img.mode in ('RGBA', 'LA', 'PA') or 'transparency' in img.info
    binary_alpha = True
    if has_alpha:
        try:
            alpha = np.asarray(img.convert('RGBA').getchannel('A'))
            binary_alpha = bool(np.isin(alpha, (0, 255)).all())
            if alpha.min() == 255:
                has_alpha = False       # fully opaque despite the alpha channel
        except Exception:
            binary_alpha = False

    arr = _thumbnail_array(img)
    if arr.size == 0 or arr.shape[0] < 2 or arr.shape[1] < 2:
        return ImageStats(width, height, GRAPHIC, has_alpha, binary_alpha, False,
                          1, 1.0, 0.0, 0.0, 0.0, is_animated, frame_count)

    gray = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)

    # --- colour spread -------------------------------------------------
    quantised = (arr // 8).astype(np.uint8)
    packed = (quantised[..., 0].astype(np.uint32) << 16 |
              quantised[..., 1].astype(np.uint32) << 8 |
              quantised[..., 2].astype(np.uint32))
    unique_colors = int(np.unique(packed).size)

    maxc = arr.max(axis=2)
    minc = arr.min(axis=2)
    with np.errstate(divide='ignore', invalid='ignore'):
        sat = np.where(maxc > 0, (maxc - minc) / np.maximum(maxc, 1e-6), 0.0)
    mean_saturation = float(sat.mean())
    is_grayscale = float(np.abs(maxc - minc).mean()) < 2.0

    # --- structure -----------------------------------------------------
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    flat_ratio = float(((dx < 1.0).mean() + (dy < 1.0).mean()) / 2.0)
    edge_density = float(((dx > 40).mean() + (dy > 40).mean()) / 2.0)
    high_frequency = float(min(1.0, (dx.mean() + dy.mean()) / 2.0 / 32.0))

    kind = _classify(unique_colors, flat_ratio, edge_density, high_frequency,
                     mean_saturation, is_grayscale)

    return ImageStats(width, height, kind, has_alpha, binary_alpha, is_grayscale,
                      unique_colors, flat_ratio, edge_density, high_frequency,
                      mean_saturation, is_animated, frame_count)


def _classify(unique_colors: int, flat_ratio: float, edge_density: float,
              high_frequency: float, saturation: float, grayscale: bool) -> str:
    # Large flat regions + hard edges = something drawn, not photographed.
    drawn = flat_ratio > 0.55 or unique_colors < 900

    if drawn and edge_density > 0.045:
        # Lots of tiny hard edges on flat ground: UI text.
        return TEXT_SCREENSHOT
    if drawn and (unique_colors < 400 or flat_ratio > 0.75):
        return GRAPHIC
    if drawn:
        return ILLUSTRATION
    if high_frequency < 0.05 and unique_colors < 3000:
        return ILLUSTRATION
    return PHOTO


def recommend(stats: ImageStats, preferred_format: str = 'auto',
              target: str = 'balanced') -> Recommendation:
    """Turn stats into concrete encoder settings.

    ``target`` is one of ``maximum``, ``high``, ``balanced``, ``small``.
    """
    base_quality = {'maximum': 92, 'high': 85, 'balanced': 78, 'small': 66}.get(target, 78)

    if stats.kind == GRAPHIC and stats.unique_colors < 256:
        return Recommendation(
            format_key=preferred_format, lossless=True, quality=100,
            near_lossless=False,
            reason=(f'Flat graphic with ~{stats.unique_colors} colours - lossless '
                    'encodes smaller than lossy here and stays crisp.'))

    if stats.kind == TEXT_SCREENSHOT:
        return Recommendation(
            format_key=preferred_format, lossless=False,
            quality=min(96, base_quality + 14), near_lossless=True,
            reason=('Screenshot with small text - raised quality and near-lossless '
                    'to stop ringing around glyphs.'))

    if stats.kind == GRAPHIC:
        return Recommendation(
            format_key=preferred_format, lossless=False,
            quality=min(95, base_quality + 10), near_lossless=True,
            reason='Flat graphic - hard edges need extra quality headroom.')

    if stats.kind == ILLUSTRATION:
        return Recommendation(
            format_key=preferred_format, lossless=False,
            quality=min(93, base_quality + 6), near_lossless=False,
            reason='Illustration - moderate detail, slightly above photo quality.')

    adjust = 0
    if stats.high_frequency > 0.35:
        adjust = -4        # busy photos hide artefacts; spend fewer bytes
    elif stats.high_frequency < 0.12:
        adjust = +4        # smooth gradients show banding
    return Recommendation(
        format_key=preferred_format, lossless=False,
        quality=max(40, min(95, base_quality + adjust)), near_lossless=False,
        reason=f'Photographic content (detail {stats.high_frequency:.2f}) - lossy encode.')
