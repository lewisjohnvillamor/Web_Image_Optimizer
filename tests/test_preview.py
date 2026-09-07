"""The preview is where a person judges a trace. Its logic is plain PIL so
it is tested here without a display; the window only shows what it makes."""
import os

import numpy as np
import pytest
from PIL import Image

from image_optimizer import vectorize as V
from image_optimizer.preview import ZOOM_LEVELS, TracePreview, composite

needs_tracer = pytest.mark.skipif(not V.available(),
                                  reason='vtracer / resvg-py not installed')


@pytest.fixture
def logo_path(tmp_path, flat_graphic):
    path = tmp_path / 'logo.png'
    flat_graphic.save(path)
    return str(path)


def test_composite_shows_transparency_as_a_checkerboard():
    img = Image.new('RGBA', (48, 48), (0, 0, 0, 0))
    out = np.asarray(composite(img))
    assert len(np.unique(out.reshape(-1, 3), axis=0)) == 2     # two checker shades
    opaque = Image.new('RGBA', (48, 48), (10, 20, 30, 255))
    assert np.asarray(composite(opaque))[0, 0].tolist() == [10, 20, 30]


def test_region_box_is_clamped_to_the_image(logo_path):
    p = TracePreview(logo_path)
    w, h = p.source.size
    box = p.region_box(zoom=4, centre=(0, 0), size=(400, 300))
    assert box[0] == 0 and box[1] == 0                      # clamped at the corner
    box = p.region_box(zoom=4, centre=(w, h), size=(400, 300))
    assert box[2] == w and box[3] == h
    box = p.region_box(zoom=1, centre=(w / 2, h / 2), size=(4000, 4000))
    assert box == (0, 0, w, h)                              # whole image at 1x


def test_viewport_without_a_candidate_shows_the_raster_twice(logo_path):
    p = TracePreview(logo_path)
    assert not p.has_candidate
    left, right = p.viewport(2, (100, 100), (200, 150))
    assert left.size == right.size == (200, 150)
    assert np.array_equal(np.asarray(left), np.asarray(right))


def test_stats_without_a_candidate_say_so(logo_path):
    st = TracePreview(logo_path).stats_summary()
    assert not st.accepted and st.svg_bytes == 0


def test_discard_reports_whether_a_file_existed(tmp_path):
    target = tmp_path / 'x.svg'
    assert TracePreview.discard(str(target)) is False
    target.write_text('<svg/>')
    assert TracePreview.discard(str(target)) is True
    assert not target.exists()


def test_keep_without_a_candidate_is_an_error(logo_path, tmp_path):
    with pytest.raises(ValueError):
        TracePreview(logo_path).keep(str(tmp_path / 'out.svg'))


@needs_tracer
def test_retrace_produces_a_candidate_and_numbers(logo_path):
    p = TracePreview(logo_path, raster_bytes=500)
    result = p.retrace(0.95)
    assert result.accepted and p.has_candidate
    st = p.stats_summary()
    assert st.accepted and st.score >= 0.95 and st.paths >= 1
    assert st.color_error is not None and st.color_error < 0.02
    assert st.svg_bytes == len(p.svg)


@needs_tracer
def test_a_refused_trace_can_still_be_shown(logo_path):
    p = TracePreview(logo_path)
    assert not p.retrace(0.9999).accepted
    assert not p.has_candidate                     # nothing to show yet...
    assert p.trace_ignoring_score().accepted       # ...unless we ask to see it anyway
    assert p.has_candidate


@needs_tracer
def test_panes_are_the_viewport_size_at_every_zoom(logo_path):
    p = TracePreview(logo_path)
    p.retrace(0.95)
    for zoom in ZOOM_LEVELS:
        left, right = p.viewport(zoom, (200, 150), (240, 180))
        assert left.size == right.size
        assert left.size[0] <= 240 and left.size[1] <= 180


@needs_tracer
def test_svg_pane_is_aligned_with_the_raster_pane(logo_path):
    """The cropped viewBox must show the same region the raster crop shows,
    or the comparison is meaningless."""
    p = TracePreview(logo_path)
    p.retrace(0.95)
    left, right = p.viewport(4, (120, 100), (240, 180))
    diff = np.abs(np.asarray(left, np.int16) - np.asarray(right, np.int16)).mean()
    assert diff < 12                                # antialiasing only


@needs_tracer
def test_difference_view_is_dark_where_the_trace_matches(logo_path):
    p = TracePreview(logo_path)
    p.retrace(0.95)
    _, diff_pane = p.viewport(2, (200, 150), (240, 180), diff=True)
    arr = np.asarray(diff_pane)
    dark = (arr.max(axis=2) < 40).mean()
    assert dark > 0.9                               # almost everything identical


@needs_tracer
def test_svg_region_rendering_ignores_zoom_cost(logo_path):
    import time
    p = TracePreview(logo_path)
    p.retrace(0.95)
    timings = []
    for zoom in (1, 8):
        started = time.time()
        p.viewport(zoom, (200, 150), (240, 180))
        timings.append(time.time() - started)
    assert timings[1] < max(0.5, timings[0] * 5)    # not zoom-squared


@needs_tracer
def test_keep_writes_the_candidate(logo_path, tmp_path):
    p = TracePreview(logo_path)
    p.retrace(0.95)
    dest = p.keep(str(tmp_path / 'nested' / 'logo.svg'))
    assert os.path.getsize(dest) == len(p.svg)
    assert open(dest, 'rb').read().startswith(b'<svg')


@needs_tracer
def test_an_existing_svg_is_scored_on_open(logo_path, tmp_path):
    p = TracePreview(logo_path)
    p.retrace(0.95)
    dest = p.keep(str(tmp_path / 'logo.svg'))
    reopened = TracePreview(logo_path, svg=open(dest, 'rb').read(), raster_bytes=500)
    assert reopened.has_candidate
    assert reopened.stats_summary().score >= 0.95
