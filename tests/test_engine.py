import os

import pytest
from PIL import Image

from image_optimizer import formats as fmt
from image_optimizer.analysis import analyze
from image_optimizer.engine import (MODE_FIXED, MODE_LOSSLESS, MODE_SMART,
                                    OptimizeSettings, existing_is_current,
                                    optimize_file, output_path_for, prepare_image,
                                    resize_to_width)


def _run(tmp_path, img, name='in.png', **kwargs):
    src = tmp_path / 'src'
    src.mkdir(exist_ok=True)
    out = tmp_path / 'out'
    path = src / name
    img.save(path)
    result = optimize_file(str(path), str(src), str(out), OptimizeSettings(**kwargs))
    return result, out


def test_a_photo_gets_much_smaller(tmp_path, photo):
    src = tmp_path / 'src'
    src.mkdir()
    path = src / 'photo.jpg'
    photo.save(path, quality=95)
    result = optimize_file(str(path), str(src), str(tmp_path / 'out'),
                           OptimizeSettings())
    assert result.ok and not result.error
    assert result.new_size < result.original_size
    assert os.path.exists(result.primary.path)


def test_smart_mode_records_the_measured_score(tmp_path, photo):
    result, _ = _run(tmp_path, photo, 'p.png', output_format='webp', mode=MODE_SMART)
    assert result.primary.score is not None
    assert 0.5 < result.primary.score <= 1.0


def test_flat_graphics_come_out_lossless(tmp_path, flat_graphic):
    result, _ = _run(tmp_path, flat_graphic, 'logo.png', output_format='webp')
    assert result.primary.lossless


def test_screenshots_beat_a_naive_fixed_quality(tmp_path, text_screenshot):
    """The headline claim: content-aware beats quality-80-for-everything, on
    both size and fidelity, for UI screenshots."""
    smart, _ = _run(tmp_path, text_screenshot, 'a.png', output_format='webp')
    naive, _ = _run(tmp_path, text_screenshot, 'b.png', output_format='webp',
                    mode=MODE_FIXED, quality=80, auto_settings=False)
    assert smart.new_size < naive.new_size
    assert smart.primary.lossless


def test_alpha_survives_a_round_trip(tmp_path, flat_graphic):
    result, _ = _run(tmp_path, flat_graphic, 'logo.png', output_format='webp')
    with Image.open(result.primary.path) as out:
        assert out.convert('RGBA').getchannel('A').getextrema()[0] == 0


def test_jpeg_output_flattens_alpha_instead_of_failing(tmp_path, flat_graphic):
    # never_larger would otherwise pass the tiny source PNG straight through,
    # and it is the flattening path we are testing here.
    result, _ = _run(tmp_path, flat_graphic, 'logo.png', output_format='jpeg',
                     never_larger=False)
    assert result.ok, result.error
    assert result.primary.path.endswith('.jpg')
    with Image.open(result.primary.path) as out:
        assert out.mode == 'RGB'


def test_lossless_mode_is_pixel_identical(tmp_path, flat_graphic):
    result, _ = _run(tmp_path, flat_graphic, 'logo.png', output_format='webp',
                     mode=MODE_LOSSLESS, auto_settings=False)
    with Image.open(result.primary.path) as out:
        assert out.convert('RGBA').tobytes() == flat_graphic.convert('RGBA').tobytes()


def test_exif_orientation_is_applied(tmp_path, photo):
    """A sideways phone photo must come out upright."""
    src = tmp_path / 'src'
    src.mkdir()
    path = src / 'rotated.jpg'
    exif = Image.Exif()
    exif[274] = 6                     # Orientation: rotate 90 CW
    photo.save(path, exif=exif)
    result = optimize_file(str(path), str(src), str(tmp_path / 'out'),
                           OptimizeSettings(output_format='webp'))
    with Image.open(result.primary.path) as out:
        assert out.size == (photo.size[1], photo.size[0])


def test_never_larger_copies_the_original_through(tmp_path):
    """A tiny already-optimised file must not be replaced by a bigger one."""
    src = tmp_path / 'src'
    src.mkdir()
    path = src / 'tiny.png'
    Image.new('RGB', (4, 4), 'white').save(path, optimize=True)
    result = optimize_file(str(path), str(src), str(tmp_path / 'out'),
                           OptimizeSettings(output_format='avif', mode=MODE_FIXED,
                                            quality=100, auto_settings=False))
    if result.copied:
        assert result.new_size == result.original_size
    else:
        assert result.new_size < result.original_size


def test_allowing_larger_output_disables_the_guard(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    path = src / 'tiny.png'
    Image.new('RGB', (4, 4), 'white').save(path, optimize=True)
    result = optimize_file(str(path), str(src), str(tmp_path / 'out'),
                           OptimizeSettings(output_format='png', never_larger=False,
                                            mode=MODE_LOSSLESS, auto_settings=False))
    assert result.ok and not result.copied


def test_max_width_downscales_but_never_upscales(tmp_path, photo):
    small, _ = _run(tmp_path, photo, 'a.png', max_width=200)
    assert small.primary.width == 200
    big, _ = _run(tmp_path, photo, 'b.png', max_width=99999)
    assert big.primary.width == photo.size[0]


def test_responsive_variants_are_written(tmp_path, photo):
    result, out = _run(tmp_path, photo, 'hero.png', output_format='webp',
                       widths=(480, 240))
    widths = sorted(v.width for v in result.variants)
    assert widths == [240, 480, photo.size[0]]
    for variant in result.variants:
        assert os.path.exists(variant.path)


def test_widths_wider_than_the_source_are_ignored(tmp_path, photo):
    result, _ = _run(tmp_path, photo, 'hero.png', output_format='webp',
                     widths=(99999,))
    assert len(result.variants) == 1


def test_folder_structure_is_preserved(tmp_path, photo):
    src = tmp_path / 'src' / 'a' / 'b'
    src.mkdir(parents=True)
    path = src / 'deep.png'
    photo.save(path)
    out = tmp_path / 'out'
    result = optimize_file(str(path), str(tmp_path / 'src'), str(out),
                           OptimizeSettings(output_format='webp'))
    assert result.primary.path == str(out / 'a' / 'b' / 'deep.webp')


def test_a_corrupt_file_fails_without_taking_down_the_run(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    path = src / 'broken.png'
    path.write_bytes(b'this is definitely not a png')
    result = optimize_file(str(path), str(src), str(tmp_path / 'out'),
                           OptimizeSettings())
    assert not result.ok
    assert 'corrupt' in result.error or 'readable' in result.error


def test_a_missing_file_reports_cleanly(tmp_path):
    result = optimize_file(str(tmp_path / 'nope.png'), str(tmp_path),
                           str(tmp_path / 'out'), OptimizeSettings())
    assert not result.ok and 'cannot read source' in result.error


def test_an_unavailable_format_is_reported_not_crashed(tmp_path, photo, monkeypatch):
    monkeypatch.setattr(fmt, 'available_formats', lambda: ('webp',))
    result, _ = _run(tmp_path, photo, 'p.png', output_format='avif')
    assert not result.ok and 'AVIF' in result.error


def test_metadata_is_stripped_by_default(tmp_path, photo):
    src = tmp_path / 'src'
    src.mkdir()
    path = src / 'meta.jpg'
    exif = Image.Exif()
    exif[271] = 'TestCamera'
    photo.save(path, exif=exif)
    result = optimize_file(str(path), str(src), str(tmp_path / 'out'),
                           OptimizeSettings(output_format='webp'))
    with Image.open(result.primary.path) as out:
        assert not out.info.get('exif')


def test_auto_format_keeps_the_smaller_encode(tmp_path, photo):
    result, _ = _run(tmp_path, photo, 'p.png', output_format='auto')
    assert result.primary.format_key in fmt.auto_candidates()


def test_skip_existing_avoids_re_encoding(tmp_path, photo):
    src = tmp_path / 'src'
    src.mkdir()
    path = src / 'p.png'
    photo.save(path)
    out = tmp_path / 'out'
    settings = OptimizeSettings(output_format='webp', skip_existing=True)
    first = optimize_file(str(path), str(src), str(out), settings)
    assert not first.skipped
    second = optimize_file(str(path), str(src), str(out), settings)
    assert second.skipped and second.ok
    assert existing_is_current(str(path), str(src), str(out), settings)


def test_touching_the_source_invalidates_the_skip(tmp_path, photo):
    src = tmp_path / 'src'
    src.mkdir()
    path = src / 'p.png'
    photo.save(path)
    out = tmp_path / 'out'
    settings = OptimizeSettings(output_format='webp', skip_existing=True)
    result = optimize_file(str(path), str(src), str(out), settings)
    # Simulate an edit landing after the output was written.
    newer = os.path.getmtime(result.primary.path) + 10
    os.utime(path, (newer, newer))
    assert existing_is_current(str(path), str(src), str(out), settings) is None


def test_resize_to_width_keeps_the_aspect_ratio(photo):
    resized = resize_to_width(photo, 320)
    assert resized.size[0] == 320
    assert abs(resized.size[1] / resized.size[0] - photo.size[1] / photo.size[0]) < 0.01


def test_output_path_uses_the_format_extension(tmp_path):
    path = output_path_for('/src/a/b.png', '/src', '/out', fmt.resolve('avif'))
    assert path.endswith(os.path.join('out', 'a', 'b.avif'))


def test_settings_round_trip_through_a_dict():
    original = OptimizeSettings(widths=(800, 400), target='high', max_width=1200)
    restored = OptimizeSettings.from_dict(original.to_dict())
    assert restored.widths == (800, 400)
    assert restored.target == 'high' and restored.max_width == 1200


def test_settings_ignore_unknown_keys():
    assert OptimizeSettings.from_dict({'nonsense': 1, 'quality': 55}).quality == 55


def test_prepare_image_converts_a_non_srgb_profile_to_srgb(tmp_path, photo):
    from PIL import ImageCms
    src_profile = ImageCms.createProfile('LAB')
    icc = ImageCms.ImageCmsProfile(src_profile).tobytes()
    tagged = photo.copy()
    tagged.info['icc_profile'] = icc
    _, out_icc = prepare_image(tagged, OptimizeSettings(convert_to_srgb=True))
    assert out_icc is None
