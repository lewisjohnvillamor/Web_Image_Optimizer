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
