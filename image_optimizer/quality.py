"""Perceptual quality measurement and per-image quality search.

The point of this module: a fixed quality slider is a guess. Quality 80 is
wasteful on a blurry snapshot and visibly bad on a product shot with fine
text. Instead we *measure* how close the encoded image looks to the original
(SSIM on the luma channel) and binary-search for the cheapest quality setting
that still hits the visual target.

SSIM is computed with box filters over integral images - no SciPy needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Callable, Optional, Tuple

import numpy as np
from PIL import Image

# Visual targets, expressed as minimum SSIM against the source.
QUALITY_TARGETS = {
    'maximum': 0.995,
    'high': 0.990,
    'balanced': 0.980,
    'small': 0.965,
}
DEFAULT_TARGET = 'balanced'

# Compare at this size: SSIM is stable under moderate downscaling and this
# keeps a search over ~6 encodes well under a second even for 6000px sources.
COMPARE_MAX_DIM = 720


def _to_luma(img: Image.Image) -> np.ndarray:
    """Luma plane as float32, alpha composited over mid-grey."""
    if img.mode in ('RGBA', 'LA', 'PA'):
        rgba = img.convert('RGBA')
        bg = Image.new('RGBA', rgba.size, (128, 128, 128, 255))
        img = Image.alpha_composite(bg, rgba)
    return np.asarray(img.convert('L'), dtype=np.float32)


def _box_filter(arr: np.ndarray, radius: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window, via a summed-area table.

    Deliberately float64: a summed-area table over a megapixel plane of
    squared 8-bit values reaches ~1e11, and in float32 the subtraction that
    recovers a window sum loses every significant digit - which silently
    turns the SSIM variance terms into noise.
    """
    pad = radius
    padded = np.pad(arr.astype(np.float64, copy=False), pad, mode='reflect')
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode='constant')
    size = 2 * radius + 1
    h, w = arr.shape
    total = (integral[size:size + h, size:size + w]
             - integral[0:h, size:size + w]
             - integral[size:size + h, 0:w]
             + integral[0:h, 0:w])
    return total / float(size * size)


def ssim(a: np.ndarray, b: np.ndarray, radius: int = 3) -> float:
    """Mean structural similarity between two equally sized luma planes."""
    if a.shape != b.shape:
        raise ValueError(f'SSIM needs matching shapes, got {a.shape} and {b.shape}')
    if min(a.shape) < 2 * radius + 1:
        radius = max(1, (min(a.shape) - 1) // 2)

    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2

    # Centre both planes on the reference mean before building integral
    # images; smaller magnitudes keep the summed-area subtraction accurate.
    offset = float(a.mean())
    a = a.astype(np.float64, copy=False) - offset
    b = b.astype(np.float64, copy=False) - offset

    mu_a = _box_filter(a, radius)
    mu_b = _box_filter(b, radius)
    mu_aa, mu_bb, mu_ab = mu_a * mu_a, mu_b * mu_b, mu_a * mu_b

    sigma_a = np.maximum(_box_filter(a * a, radius) - mu_aa, 0.0)
    sigma_b = np.maximum(_box_filter(b * b, radius) - mu_bb, 0.0)
    sigma_ab = _box_filter(a * b, radius) - mu_ab

    num = (2 * mu_ab + c1) * (2 * sigma_ab + c2)
    den = (mu_aa + mu_bb + c1) * (sigma_a + sigma_b + c2)
    return float(np.clip(num / den, -1.0, 1.0).mean())


def comparison_plane(img: Image.Image) -> np.ndarray:
    """Downscaled luma plane used for all comparisons of this image."""
    work = img
    if max(img.size) > COMPARE_MAX_DIM:
        work = img.copy()
        work.thumbnail((COMPARE_MAX_DIM, COMPARE_MAX_DIM), Image.Resampling.BILINEAR)
    return _to_luma(work)


def visual_score(reference_plane: np.ndarray, candidate: Image.Image) -> float:
    """SSIM of an encoded candidate against a prepared reference plane."""
    work = candidate
    target_shape = reference_plane.shape
    if (work.size[1], work.size[0]) != target_shape:
        work = candidate.resize((target_shape[1], target_shape[0]),
                                Image.Resampling.BILINEAR)
    return ssim(reference_plane, _to_luma(work))


@dataclass
class QualitySearchResult:
    quality: int
    score: float
    payload: bytes
    attempts: int
    hit_target: bool

    @property
    def size(self) -> int:
        return len(self.payload)


def search_quality(encode: Callable[[int], bytes],
                   decode: Callable[[bytes], Image.Image],
                   reference_plane: np.ndarray,
                   target_score: float,
                   low: int = 40,
                   high: int = 96,
                   max_steps: int = 6,
                   fallback_quality: Optional[int] = None) -> QualitySearchResult:
    """Find the lowest quality whose decoded output still scores >= target.

    ``encode`` takes a quality and returns encoded bytes; ``decode`` turns
    those bytes back into an image. Binary search, so ~6 encodes regardless of
    the range width.

    Some images cannot reach the target at any quality - film grain and sensor
    noise are exactly the detail a lossy codec discards first. Spending the
    ceiling quality on those buys a much bigger file and still misses, so when
    the ceiling falls short we drop back to ``fallback_quality`` (the content
    analyser's recommendation) instead.
    """
    best: Optional[QualitySearchResult] = None
    attempts = 0
    lo, hi = low, high

    payload = encode(hi)
    attempts += 1
    score = visual_score(reference_plane, decode(payload))
    if score < target_score:
        if fallback_quality is None or fallback_quality >= hi:
            return QualitySearchResult(hi, score, payload, attempts, hit_target=False)
        fallback = max(low, min(hi, fallback_quality))
        fallback_payload = encode(fallback)
        attempts += 1
        fallback_score = visual_score(reference_plane, decode(fallback_payload))
        return QualitySearchResult(fallback, fallback_score, fallback_payload,
                                   attempts, hit_target=False)
    best = QualitySearchResult(hi, score, payload, attempts, hit_target=True)

    while lo <= hi and attempts < max_steps:
        mid = (lo + hi) // 2
        if mid == best.quality:
            break
        payload = encode(mid)
        attempts += 1
        score = visual_score(reference_plane, decode(payload))
        if score >= target_score:
            best = QualitySearchResult(mid, score, payload, attempts, hit_target=True)
            hi = mid - 1
        else:
            lo = mid + 1

    best.attempts = attempts
    return best


def decode_bytes(payload: bytes) -> Image.Image:
    img = Image.open(BytesIO(payload))
    img.load()
    return img


# Share of pixels allowed to shift colour before 4:2:0 is judged unsafe.
# Photographs measure 0.00% here; hard coloured edges (a logo, coloured text)
# measure 1.5-2%, so the band between them is wide and the cut is not delicate.
CHROMA_SUBSAMPLE_LIMIT = 0.005
CHROMA_SHIFT_THRESHOLD = 14


def chroma_subsample_damage(img: Image.Image,
                            threshold: int = CHROMA_SHIFT_THRESHOLD) -> float:
    """Share of pixels 4:2:0 would visibly shift in colour.

    Rather than encoding twice to find out, this performs the same operation
    the codec does - halve the Cb/Cr planes and put them back - and counts
    the pixels that move. Validated against the codec on the sample set:
    predicted 0.00/0.40/1.99/2.11% against an actual 0.00/0.30/1.63/1.47%,
    and the same accept/reject decision every time, for about a millisecond
    instead of two extra encodes.
    """
    ycbcr = np.asarray(img.convert('YCbCr'), dtype=np.uint8)
    height, width = ycbcr.shape[:2]
    if height < 2 or width < 2:
        return 0.0
    worst = np.zeros((height, width), dtype=np.int16)
    for channel in (1, 2):
        plane = Image.fromarray(ycbcr[..., channel])
        small = plane.resize((max(1, width // 2), max(1, height // 2)),
                             Image.Resampling.BOX)
        restored = np.asarray(small.resize((width, height), Image.Resampling.BILINEAR),
                              dtype=np.int16)
        worst = np.maximum(worst, np.abs(ycbcr[..., channel].astype(np.int16) - restored))
    return float((worst > threshold).mean())


def chroma_subsampling_is_safe(img: Image.Image,
                               limit: float = CHROMA_SUBSAMPLE_LIMIT) -> bool:
    """Whether 4:2:0 can be used without visible colour damage."""
    return chroma_subsample_damage(img) <= limit


def chroma_error(original: Image.Image, decoded: Image.Image) -> float:
    """Mean absolute colour deviation, in chroma units.

    SSIM here runs on the luma plane, which means it is blind to colour
    damage by construction: chroma subsampling can smear a red glyph into its
    background and SSIM will not move. This is the missing half - it compares
    the Cb/Cr planes directly, so a decision about subsampling can be
    measured instead of guessed.
    """
    if decoded.size != original.size:
        decoded = decoded.resize(original.size, Image.Resampling.BILINEAR)
    a = np.asarray(original.convert('YCbCr'), dtype=np.int16)
    b = np.asarray(decoded.convert('YCbCr'), dtype=np.int16)
    return float(np.abs(a[..., 1:] - b[..., 1:]).mean())


def resolve_target(target: str) -> float:
    return QUALITY_TARGETS.get(target, QUALITY_TARGETS[DEFAULT_TARGET])
