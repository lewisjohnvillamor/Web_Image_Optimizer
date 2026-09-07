"""SVG tracing: the tracer is deterministic geometry, so what needs testing is
the gate around it - what gets refused, what gets kept, and that a kept SVG
actually matches its source."""
import os

import numpy as np
import pytest
from PIL import Image

from image_optimizer import formats as fmt
from image_optimizer import vectorize as V
from image_optimizer.analysis import analyze
from image_optimizer.engine import OptimizeSettings, optimize_file

needs_tracer = pytest.mark.skipif(not V.available(),
                                  reason='vtracer / resvg-py not installed')


# --------------------------------------------------------------------------
# Pure-python parts: run everywhere
# --------------------------------------------------------------------------

def test_availability_hint_names_the_missing_pieces(monkeypatch):
    monkeypatch.setattr(V, 'tracer_available', lambda: False)
    monkeypatch.setattr(V, 'renderer_available', lambda: True)
    assert 'vtracer' in V.availability_hint()
    monkeypatch.setattr(V, 'renderer_available', lambda: False)
    hint = V.availability_hint()
    assert 'vtracer' in hint and 'resvg-py' in hint and 'pip install' in hint


def test_minify_strips_declaration_comments_and_whitespace():
    raw = ('<?xml version="1.0"?>\n<!-- Generator: x -->\n'
           '<svg xmlns="http://www.w3.org/2000/svg">\n  <path d="M0 0"/>\n</svg>\n')
    out = V.minify(raw)
    assert out.startswith('<svg')
    assert '<?xml' not in out and '<!--' not in out
    assert '>\n' not in out
    assert V.count_paths(out) == 1


def test_photos_and_screenshots_are_refused_before_any_tracing(photo, text_screenshot,
                                                                monkeypatch):
    def boom(*a, **k):
        raise AssertionError('tracer must not run for non-graphics')
    monkeypatch.setattr(V, 'trace', boom)
    for img in (photo, text_screenshot):
        result = V.vectorize(img, analyze(img), raster_size=1000)
        assert not result.accepted
        assert 'not flat artwork' in result.reason


def test_unavailable_tracer_is_a_clean_refusal(flat_graphic, monkeypatch):
    monkeypatch.setattr(V, 'tracer_available', lambda: False)
    result = V.vectorize(flat_graphic, analyze(flat_graphic), raster_size=1000)
    assert not result.accepted and 'vtracer' in result.reason


def test_dominant_colour_count_ignores_antialiasing(flat_graphic):
    """A four-colour logo with soft edges must not report dozens of colours."""
    count = V._dominant_color_count(flat_graphic.convert('RGB'))
    assert 4 <= count <= 6


def test_dominant_colour_count_ignores_jpeg_noise(flat_graphic, tmp_path):
    path = tmp_path / 'noisy.jpg'
    bg = Image.new('RGBA', flat_graphic.size, (255, 255, 255, 255))
    Image.alpha_composite(bg, flat_graphic).convert('RGB').save(path, quality=50)
    with Image.open(path) as noisy:
        noisy.load()
        assert analyze(noisy).unique_colors > 100          # the raw count is inflated
        assert V._dominant_color_count(noisy.convert('RGB')) <= 8   # this is not


def test_prepare_thresholds_alpha_to_hard_edges(flat_graphic):
    prepared, _ = V.prepare_for_trace(flat_graphic, analyze(flat_graphic))
    alpha = np.asarray(prepared.getchannel('A'))
    assert set(np.unique(alpha)) <= {0, 255}


def test_svg_spec_resolves_but_is_never_a_raster_candidate():
    assert fmt.resolve('svg').mime == 'image/svg+xml'
    assert 'svg' not in fmt.available_formats()
    assert 'svg' not in fmt.auto_candidates()


# --------------------------------------------------------------------------
# With the real tracer
# --------------------------------------------------------------------------

@needs_tracer
def test_a_clean_logo_is_traced_and_verifies(flat_graphic):
    result = V.vectorize(flat_graphic, analyze(flat_graphic), raster_size=4000)
    assert result.accepted, result.reason
    assert result.svg.startswith(b'<svg')
    assert result.score >= V.DEFAULT_MIN_SCORE
    assert 1 <= result.paths <= 40     # a rect and an ellipse trace to two paths
    assert result.colors <= 8


@needs_tracer
def test_the_kept_svg_really_matches_when_re_rendered(flat_graphic):
    result = V.vectorize(flat_graphic, analyze(flat_graphic), raster_size=4000)
    rendered = V.render(result.svg.decode(), *flat_graphic.size)
    assert V.fidelity(flat_graphic, rendered) >= V.DEFAULT_MIN_SCORE


@needs_tracer
def test_a_strict_score_refuses_with_the_score_in_the_reason(flat_graphic):
    result = V.vectorize(flat_graphic, analyze(flat_graphic), raster_size=4000,
                         min_score=0.9999)
    assert not result.accepted
    assert 'SSIM' in result.reason and 'fidelity setting' in result.reason


@needs_tracer
def test_a_path_explosion_is_refused(flat_graphic):
    result = V.vectorize(flat_graphic, analyze(flat_graphic), raster_size=4000,
                         max_paths=1)
    assert not result.accepted and 'paths' in result.reason


@needs_tracer
def test_an_absurdly_heavy_svg_is_refused(flat_graphic, monkeypatch):
    monkeypatch.setattr(V, 'SIZE_RATIO_FLOOR', 0)     # make the ratio bite
    result = V.vectorize(flat_graphic, analyze(flat_graphic), raster_size=10,
                         max_size_ratio=1.0)
    assert not result.accepted and 'too heavy' in result.reason


def test_a_small_svg_is_kept_even_when_the_raster_is_tinier(flat_graphic):
    """Bytes are not the point of a vector; the ratio guard must not fire on
    a few-KB SVG just because lossless WebP of a flat shape is microscopic."""
    result = V.vectorize(flat_graphic, analyze(flat_graphic), raster_size=400)
    assert result.accepted, result.reason


@needs_tracer
def test_a_large_logo_is_traced_at_the_working_size(flat_graphic):
    big = flat_graphic.resize((3600, 2700), Image.Resampling.NEAREST)
    result = V.vectorize(big, analyze(big), raster_size=50000)
    assert result.accepted, result.reason


# --------------------------------------------------------------------------
# Through the engine
# --------------------------------------------------------------------------

@needs_tracer
def test_engine_writes_an_svg_beside_the_raster(tmp_path, flat_graphic):
    src = tmp_path / 'src'; src.mkdir()
    flat_graphic.save(src / 'logo.png')
    result = optimize_file(str(src / 'logo.png'), str(src), str(tmp_path / 'out'),
                           OptimizeSettings(output_format='webp', vectorize=True))
    assert result.ok, result.error
    assert result.primary.format_key == 'webp'           # raster stays primary
    assert result.vector is not None
    assert result.vector.path.endswith('logo.svg')
    assert os.path.exists(result.vector.path)
    assert result.vector_note.startswith('SVG:')
    assert result.to_dict()['vector_note']


@needs_tracer
def test_engine_does_not_trace_a_photo(tmp_path, photo):
    src = tmp_path / 'src'; src.mkdir()
    photo.save(src / 'p.png')
    result = optimize_file(str(src / 'p.png'), str(src), str(tmp_path / 'out'),
                           OptimizeSettings(output_format='webp', vectorize=True))
    assert result.ok and result.vector is None
    assert result.vector_note.startswith('no SVG')
    assert not (tmp_path / 'out' / 'p.svg').exists()


def test_engine_ignores_svg_when_the_option_is_off(tmp_path, flat_graphic):
    src = tmp_path / 'src'; src.mkdir()
    flat_graphic.save(src / 'logo.png')
    result = optimize_file(str(src / 'logo.png'), str(src), str(tmp_path / 'out'),
                           OptimizeSettings(output_format='webp'))
    assert result.vector is None and result.vector_note is None


@needs_tracer
def test_markup_puts_the_svg_source_first(tmp_path, flat_graphic):
    from image_optimizer.batch import run_batch
    from image_optimizer.report import picture_markup
    src = tmp_path / 'src'; src.mkdir()
    flat_graphic.save(src / 'logo.png')
    summary = run_batch(str(src), str(tmp_path / 'out'),
                        OptimizeSettings(output_format='webp', vectorize=True,
                                         widths=(200,)), workers=1)
    markup = picture_markup(summary.succeeded[0], summary.output_root, base_url='/i')
    lines = markup.splitlines()
    assert lines[1].strip().startswith('<source type="image/svg+xml" srcset="/i/logo.svg">')
    assert 'src="/i/logo.webp"' in markup                # fallback stays raster
    assert 'logo.svg 200w' not in markup                # one SVG serves every width


@needs_tracer
def test_cli_svg_flag_writes_svgs(sample_tree, tmp_path):
    from image_optimizer.cli import main
    out = tmp_path / 'out'
    assert main([str(sample_tree), str(out), '-f', 'webp', '--svg', '--quiet']) == 0
    assert (out / 'logo.svg').exists()
    assert not (out / 'photo.svg').exists()


def test_cli_svg_flag_fails_fast_without_the_tracer(sample_tree, tmp_path,
                                                    monkeypatch, capsys):
    from image_optimizer.cli import main
    monkeypatch.setattr(V, 'tracer_available', lambda: False)
    code = main([str(sample_tree), str(tmp_path / 'out'), '--svg', '--quiet'])
    assert code == 2
    assert 'vtracer' in capsys.readouterr().err
    assert not (tmp_path / 'out').exists()


# --------------------------------------------------------------------------
# Regressions from building the README figure
# --------------------------------------------------------------------------

def test_snap_to_palette_picks_the_truly_nearest_colour():
    """Squared channel differences reach 65025; in int16 that overflows and
    the 'nearest' colour comes out scrambled - white lettering was snapping
    to yellow. Every pixel must land on the palette entry closest to it."""
    palette = [(24, 106, 222), (255, 209, 64), (255, 255, 255)]
    img = Image.new('RGB', (3, 1))
    img.putdata([(24, 106, 222), (255, 209, 64), (255, 255, 255)])
    snapped = V._snap_to_palette(img, palette)
    assert list(snapped.getdata()) == palette
    near_white = Image.new('RGB', (1, 1), (250, 250, 245))
    assert V._snap_to_palette(near_white, palette).getpixel((0, 0)) == (255, 255, 255)


def test_dominant_colours_exclude_transparent_pixels():
    """A transparent background must not claim a palette slot, and the RGB
    hiding under alpha=0 (often black) must not appear as a colour."""
    img = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
    from PIL import ImageDraw
    ImageDraw.Draw(img).rectangle([10, 10, 90, 90], fill=(24, 106, 222, 255))
    assert V.dominant_colors(img) == [(24, 106, 222)]


def test_colour_error_catches_a_recoloured_region_that_ssim_misses():
    """SSIM runs on luma over the whole frame, so a small region coming out
    the wrong colour barely moves it. The colour-error check must fire."""
    from PIL import ImageDraw
    base = Image.new('RGBA', (300, 200), (24, 106, 222, 255))
    ImageDraw.Draw(base).rectangle([40, 80, 120, 110], fill=(255, 255, 255, 255))
    wrong = base.copy()
    ImageDraw.Draw(wrong).rectangle([40, 80, 120, 110], fill=(255, 209, 64, 255))
    luma_score = V.fidelity(base, wrong)
    assert luma_score > 0.9                      # SSIM barely notices...
    assert V.color_error(base, wrong) > V.DEFAULT_MAX_COLOR_ERROR   # ...this does


@needs_tracer
def test_traced_svg_uses_only_the_real_colours(flat_graphic):
    """The trace must not invent shades: a two-colour logo yields two fills."""
    import re
    result = V.vectorize(flat_graphic, analyze(flat_graphic), raster_size=4000)
    fills = set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', result.svg.decode()))
    assert len(fills) == 2, fills


@needs_tracer
def test_white_lettering_stays_white(tmp_path):
    """The bug that started this: white text on blue came out yellow."""
    from PIL import ImageDraw
    img = Image.new('RGBA', (600, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([20, 20, 580, 380], 30, fill=(24, 106, 222, 255))
    d.ellipse([60, 60, 220, 220], fill=(255, 209, 64, 255))
    d.rectangle([80, 260, 520, 330], fill=(255, 255, 255, 255))    # "lettering"
    result = V.vectorize(img, analyze(img), raster_size=4000)
    assert result.accepted, result.reason
    rendered = V.render(result.svg.decode(), *img.size)
    assert rendered.getpixel((300, 295))[:3] == (255, 255, 255)
    assert rendered.getpixel((140, 140))[:3] == (255, 209, 64)
    assert V.color_error(img, rendered) < 0.01
