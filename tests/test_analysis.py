import numpy as np
from PIL import Image

from image_optimizer.analysis import (GRAPHIC, ILLUSTRATION, PHOTO, TEXT_SCREENSHOT,
                                      analyze, recommend)


def test_photo_is_classified_as_a_photo(photo):
    assert analyze(photo).kind == PHOTO


def test_flat_graphic_is_classified_as_a_graphic(flat_graphic):
    stats = analyze(flat_graphic)
    assert stats.kind == GRAPHIC
    assert stats.unique_colors < 400


def test_dense_small_text_is_classified_as_a_screenshot(text_screenshot):
    assert analyze(text_screenshot).kind == TEXT_SCREENSHOT


def test_transparency_is_detected(flat_graphic):
    assert analyze(flat_graphic).has_alpha
    assert analyze(flat_graphic).binary_alpha


def test_a_fully_opaque_alpha_channel_is_not_reported_as_transparency():
    opaque = Image.new('RGBA', (64, 64), (10, 20, 30, 255))
    assert not analyze(opaque).has_alpha


def test_grayscale_is_detected():
    gray = Image.fromarray(
        np.random.default_rng(0).integers(0, 256, (80, 80), dtype=np.uint8)).convert('RGB')
    assert analyze(gray).is_grayscale


def test_tiny_images_do_not_crash_the_analyser():
    assert analyze(Image.new('RGB', (1, 1), 'red')).kind


def test_flat_graphics_are_recommended_lossless(flat_graphic):
    rec = recommend(analyze(flat_graphic))
    assert rec.lossless
    assert 'lossless' in rec.reason.lower()


def test_screenshots_get_extra_quality_headroom(photo, text_screenshot):
    shot = recommend(analyze(text_screenshot))
    pic = recommend(analyze(photo))
    assert shot.quality > pic.quality
    assert shot.near_lossless


def test_target_shifts_the_recommended_quality(photo):
    stats = analyze(photo)
    qualities = [recommend(stats, target=t).quality
                 for t in ('small', 'balanced', 'high', 'maximum')]
    assert qualities == sorted(qualities)


def test_every_recommendation_explains_itself(photo, flat_graphic, text_screenshot):
    for img in (photo, flat_graphic, text_screenshot):
        assert len(recommend(analyze(img)).reason) > 20


def test_stats_serialise_for_the_json_report(photo):
    data = analyze(photo).to_dict()
    assert data['kind_label'] and data['width'] == photo.size[0]


def test_a_large_screenshot_is_not_mistaken_for_a_logo():
    """A 1600px UI capture shrinks to a 256px thumbnail whose text has blurred
    into flat colour - it used to be classified as a flat graphic. Measuring
    edges at a larger working size is what keeps it a screenshot."""
    from tests.conftest import make_text_screenshot
    for size in ((1600, 1000), (1200, 800), (600, 400)):
        stats = analyze(make_text_screenshot(*size))
        assert stats.kind == TEXT_SCREENSHOT, size
        assert stats.edge_density > 0.02, size


def test_edge_density_does_not_depend_on_where_content_sits():
    """Measuring the whole frame, not a sampled crop, is what makes this hold -
    a left-aligned table must score the same as a centred one."""
    from PIL import ImageDraw

    def canvas_with_text_at(x):
        canvas = Image.new('RGB', (1400, 900), 'white')
        draw = ImageDraw.Draw(canvas)
        for y in range(60, 840, 14):
            draw.text((x, y), 'tiny dense label text 12345 OK', fill=(10, 10, 10))
        return analyze(canvas).edge_density

    corner, centre = canvas_with_text_at(10), canvas_with_text_at(560)
    assert corner > 0.01 and centre > 0.01
    assert abs(corner - centre) < 0.01


def test_a_photograph_has_almost_no_hard_edges(photo):
    assert analyze(photo).edge_density < 0.01


def test_flat_artwork_stays_below_the_text_threshold(flat_graphic):
    from image_optimizer.analysis import TEXT_EDGE_THRESHOLD
    assert analyze(flat_graphic).edge_density < TEXT_EDGE_THRESHOLD
