"""Lossless preprocessing: it must shrink files without changing a pixel
anyone can see, and do nothing at all when it does not apply."""
import numpy as np
import pytest
from io import BytesIO
from PIL import Image, ImageDraw

from image_optimizer import preprocess
from image_optimizer.engine import MODE_LOSSLESS, OptimizeSettings, optimize_file
from image_optimizer.quality import (CHROMA_SUBSAMPLE_LIMIT, chroma_error,
                                     chroma_subsample_damage,
                                     chroma_subsampling_is_safe)


def _dirty_logo(flat_graphic, seed=3):
    """A logo exported the way design tools do it: whatever colour the
    artwork had before it was erased is still there under alpha 0."""
    arr = np.asarray(flat_graphic.convert('RGBA')).copy()
    clear = arr[..., 3] == 0
    assert clear.any()
    arr[..., :3][clear] = np.random.default_rng(seed).integers(
        0, 256, (int(clear.sum()), 3))
    return Image.fromarray(arr, 'RGBA')


# --------------------------------------------------------------------------
# Transparent-pixel colour
# --------------------------------------------------------------------------

def test_variety_counts_the_hidden_colours(flat_graphic):
    assert preprocess.transparent_rgb_variety(_dirty_logo(flat_graphic)) > 100
    assert preprocess.transparent_rgb_variety(flat_graphic) <= 1


def test_an_opaque_image_has_nothing_hidden(photo):
    assert preprocess.transparent_rgb_variety(photo) == 0


def test_cleaning_flattens_the_hidden_colour(flat_graphic):
    cleaned, changed = preprocess.clean_transparent_rgb(_dirty_logo(flat_graphic))
    assert changed
    assert preprocess.transparent_rgb_variety(cleaned) == 1


def test_cleaning_changes_nothing_visible(flat_graphic):
    """Every pixel a viewer can see must be identical afterwards."""
    dirty = _dirty_logo(flat_graphic)
    cleaned, _ = preprocess.clean_transparent_rgb(dirty)
    before = np.asarray(dirty)
    after = np.asarray(cleaned)
    assert np.array_equal(before[..., 3], after[..., 3])          # alpha intact
    visible = before[..., 3] > 0
    assert np.array_equal(before[..., :3][visible], after[..., :3][visible])


def test_cleaning_is_a_no_op_when_there_is_nothing_to_clean(flat_graphic, photo):
    for img in (flat_graphic, photo):
        out, changed = preprocess.clean_transparent_rgb(img)
        assert not changed and out is img


def test_cleaning_shrinks_the_encoded_file(flat_graphic):
    dirty = _dirty_logo(flat_graphic)
    cleaned, _ = preprocess.clean_transparent_rgb(dirty)

    def size(img):
        buf = BytesIO()
        img.save(buf, 'WEBP', lossless=True, method=4, exact=True)
        return len(buf.getvalue())

    assert size(cleaned) < size(dirty) * 0.5      # measured ~-99% on a real export


# --------------------------------------------------------------------------
# Greyscale
# --------------------------------------------------------------------------

def test_greyscale_is_detected_only_when_there_is_no_colour(photo):
    grey = photo.convert('L').convert('RGB')
    assert preprocess.is_greyscale(grey)
    assert not preprocess.is_greyscale(photo)


def test_as_greyscale_keeps_alpha(flat_graphic):
    out = preprocess.as_greyscale(flat_graphic.convert('L').convert('RGBA'))
    assert out.mode == 'LA'


def test_as_greyscale_preserves_the_pixels(photo):
    grey = photo.convert('L').convert('RGB')
    assert preprocess.as_greyscale(grey).tobytes() == grey.convert('L').tobytes()


def test_greyscale_png_output_is_smaller(tmp_path, photo):
    src = tmp_path / 'src'
    src.mkdir()
    photo.convert('L').convert('RGB').save(src / 'scan.png')
    result = optimize_file(str(src / 'scan.png'), str(src), str(tmp_path / 'out'),
                           OptimizeSettings(output_format='png', mode=MODE_LOSSLESS,
                                            auto_settings=False))
    assert result.ok, result.error
    with Image.open(result.primary.path) as out:
        assert out.mode in ('L', 'LA', 'P')
    assert result.new_size < result.original_size


# --------------------------------------------------------------------------
# Chroma subsampling, decided by measurement
# --------------------------------------------------------------------------

def test_a_photograph_is_safe_to_subsample(photo):
    assert chroma_subsample_damage(photo) < CHROMA_SUBSAMPLE_LIMIT
    assert chroma_subsampling_is_safe(photo)


def test_coloured_text_is_not_safe_to_subsample():
    """The case the measurement exists for: saturated colour on hard edges."""
    img = Image.new('RGB', (600, 240), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    for i, colour in enumerate(((220, 20, 20), (20, 90, 220), (10, 150, 60))):
        for y in range(10 + i * 80, 70 + i * 80, 6):
            draw.line([(20, y), (580, y)], fill=colour, width=2)
    assert chroma_subsample_damage(img) > CHROMA_SUBSAMPLE_LIMIT
    assert not chroma_subsampling_is_safe(img)


def test_a_flat_graphic_with_hard_colour_edges_is_not_safe(flat_graphic):
    bg = Image.new('RGBA', flat_graphic.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(bg, flat_graphic).convert('RGB')
    assert chroma_subsample_damage(flat) > chroma_subsample_damage(
        flat.filter(__import__('PIL.ImageFilter', fromlist=['x']).GaussianBlur(4)))


def test_damage_is_a_share_between_zero_and_one(photo):
    assert 0.0 <= chroma_subsample_damage(photo) <= 1.0


def test_tiny_images_do_not_crash_the_measurement():
    assert chroma_subsample_damage(Image.new('RGB', (1, 1), 'red')) == 0.0


def test_chroma_error_sees_what_ssim_cannot():
    """SSIM runs on luma; two colours of equal luma are identical to it."""
    from image_optimizer.quality import _to_luma, ssim
    # 0.299*244 == 0.587*124 == luma 73: identical brightness, opposite hue.
    a = Image.new('RGB', (64, 64), (244, 0, 0))
    b = Image.new('RGB', (64, 64), (0, 124, 0))
    luma_a = np.asarray(_to_luma(a.convert('RGBA')))
    luma_b = np.asarray(_to_luma(b.convert('RGBA')))
    assert abs(float(luma_a.mean()) - float(luma_b.mean())) < 8
    assert ssim(luma_a.astype(np.float32), luma_b.astype(np.float32)) > 0.9
    assert chroma_error(a, b) > 20                    # the colour move is obvious


def test_chroma_error_is_zero_for_an_identical_image(photo):
    assert chroma_error(photo, photo.copy()) == 0.0
