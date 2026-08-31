"""The perceptual metric is the foundation of smart mode - test it hard."""
import numpy as np
import pytest
from io import BytesIO
from PIL import Image

from image_optimizer.quality import (COMPARE_MAX_DIM, QUALITY_TARGETS, _box_filter,
                                     comparison_plane, decode_bytes, resolve_target,
                                     search_quality, ssim, visual_score)


def test_ssim_of_identical_planes_is_one():
    plane = np.random.default_rng(0).normal(128, 30, (120, 160)).astype(np.float32)
    assert ssim(plane, plane) == pytest.approx(1.0, abs=1e-6)


def test_ssim_decreases_as_noise_increases():
    rng = np.random.default_rng(1)
    plane = rng.normal(128, 30, (120, 160)).astype(np.float32)
    scores = [ssim(plane, plane + rng.normal(0, sigma, plane.shape).astype(np.float32))
              for sigma in (2, 8, 24)]
    assert scores == sorted(scores, reverse=True)


def test_ssim_stays_high_for_a_near_perfect_encode():
    """Regression guard: float32 integral images used to collapse the variance
    term, scoring a 51 dB PSNR encode at 0.95 instead of 0.99+."""
    img = Image.effect_mandelbrot((256, 256), (-2, -1.5, 1, 1.5), 40).convert('RGB')
    buf = BytesIO()
    img.save(buf, 'WEBP', quality=98, method=4)
    reference = np.asarray(img.convert('L'), dtype=np.float32)
    decoded = np.asarray(decode_bytes(buf.getvalue()).convert('L'), dtype=np.float32)
    psnr = 10 * np.log10(255 ** 2 / max(((reference - decoded) ** 2).mean(), 1e-9))
    assert psnr > 35
    assert ssim(reference, decoded) > 0.97


def test_box_filter_matches_a_brute_force_window():
    arr = np.random.default_rng(2).normal(50, 10, (18, 23)).astype(np.float32)
    filtered = _box_filter(arr, 3)
    padded = np.pad(arr, 3, mode='reflect')
    brute = np.array([[padded[i:i + 7, j:j + 7].mean() for j in range(23)]
                      for i in range(18)])
    assert np.abs(filtered - brute).max() < 1e-3


def test_ssim_rejects_mismatched_shapes():
    with pytest.raises(ValueError):
        ssim(np.zeros((10, 10), np.float32), np.zeros((10, 11), np.float32))


def test_ssim_handles_planes_smaller_than_the_window():
    tiny = np.full((3, 3), 100.0, dtype=np.float32)
    assert ssim(tiny, tiny) == pytest.approx(1.0, abs=1e-6)


def test_comparison_plane_is_capped(photo):
    big = photo.resize((2000, 1500))
    assert max(comparison_plane(big).shape) <= COMPARE_MAX_DIM


def test_visual_score_resizes_a_mismatched_candidate(photo):
    reference = comparison_plane(photo)
    assert visual_score(reference, photo.resize((320, 240))) > 0.5


def test_alpha_is_composited_not_dropped():
    opaque = Image.new('RGBA', (64, 64), (255, 0, 0, 255))
    transparent = Image.new('RGBA', (64, 64), (255, 0, 0, 0))
    assert visual_score(comparison_plane(opaque), transparent) < 0.99


def test_search_finds_a_cheaper_quality_than_the_ceiling(photo):
    reference = comparison_plane(photo)

    def encode(quality):
        buf = BytesIO()
        photo.save(buf, 'WEBP', quality=quality, method=2)
        return buf.getvalue()

    result = search_quality(encode, decode_bytes, reference, 0.97, low=40, high=96)
    assert result.hit_target
    assert result.score >= 0.97
    assert result.quality < 96
    assert result.size < len(encode(96))


def test_search_respects_a_stricter_target(photo):
    reference = comparison_plane(photo)

    def encode(quality):
        buf = BytesIO()
        photo.save(buf, 'WEBP', quality=quality, method=2)
        return buf.getvalue()

    lenient = search_quality(encode, decode_bytes, reference, 0.97, low=40, high=96)
    strict = search_quality(encode, decode_bytes, reference, 0.995, low=40, high=96)
    assert strict.quality >= lenient.quality
    assert strict.size >= lenient.size


def test_search_reports_a_miss_instead_of_pretending():
    """When even max quality misses the target, say so rather than looping."""
    noise = Image.fromarray(
        np.random.default_rng(3).integers(0, 256, (200, 200, 3), dtype=np.uint8))
    reference = comparison_plane(noise)

    def encode(quality):
        buf = BytesIO()
        noise.save(buf, 'WEBP', quality=quality, method=0)
        return buf.getvalue()

    result = search_quality(encode, decode_bytes, reference, 0.9999, low=40, high=90)
    assert not result.hit_target
    assert result.quality == 90
    assert result.attempts == 1


def test_targets_are_ordered_and_resolvable():
    assert (QUALITY_TARGETS['maximum'] > QUALITY_TARGETS['high']
            > QUALITY_TARGETS['balanced'] > QUALITY_TARGETS['small'])
    assert resolve_target('nonsense') == QUALITY_TARGETS['balanced']


def test_unreachable_target_falls_back_instead_of_maxing_out():
    """Grainy images cannot hit a high target; spending quality 96 on them
    would be a big file that still misses. Fall back to the recommendation."""
    noise = Image.fromarray(
        np.random.default_rng(4).integers(0, 256, (200, 200, 3), dtype=np.uint8))
    reference = comparison_plane(noise)

    def encode(quality):
        buf = BytesIO()
        noise.save(buf, 'WEBP', quality=quality, method=0)
        return buf.getvalue()

    result = search_quality(encode, decode_bytes, reference, 0.9999,
                            low=40, high=95, fallback_quality=70)
    assert not result.hit_target
    assert result.quality == 70
    assert result.size < len(encode(95))
