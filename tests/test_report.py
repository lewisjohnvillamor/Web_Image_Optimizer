import csv
import json
import os

from image_optimizer import report
from image_optimizer.batch import run_batch
from image_optimizer.engine import OptimizeSettings


def _summary(sample_tree, tmp_path, **kwargs):
    return run_batch(str(sample_tree), str(tmp_path / 'out'),
                     OptimizeSettings(output_format='webp', **kwargs), workers=2)


def test_format_bytes_scales_units():
    assert report.format_bytes(0) == '0 B'
    assert report.format_bytes(1536) == '1.5 KiB'
    assert report.format_bytes(1024 ** 2) == '1.0 MiB'
    assert report.format_bytes(None) == 'N/A'
    assert report.format_bytes(-1) == 'N/A'


def test_format_duration_switches_to_minutes():
    assert report.format_duration(3.2) == '3.2s'
    assert report.format_duration(125) == '2m 5s'


def test_json_report_is_valid_and_complete(sample_tree, tmp_path):
    summary = _summary(sample_tree, tmp_path)
    path = report.write_json(summary, str(tmp_path / 'r.json'))
    data = json.loads(open(path).read())
    assert data['totals']['optimized'] == 4
    assert data['totals']['saved_bytes'] > 0
    assert len(data['files']) == 4
    assert data['files'][0]['analysis']['kind_label']
    assert data['settings']['output_format'] == 'webp'


def test_csv_report_has_a_row_per_file(sample_tree, tmp_path):
    summary = _summary(sample_tree, tmp_path)
    path = report.write_csv(summary, str(tmp_path / 'r.csv'))
    rows = list(csv.DictReader(open(path)))
    assert len(rows) == 4
    assert set(rows[0]) == set(report.CSV_COLUMNS)
    assert all(row['status'] == 'ok' for row in rows)


def test_html_report_renders_the_headline_numbers(sample_tree, tmp_path):
    summary = _summary(sample_tree, tmp_path)
    path = report.write_html_report(summary, str(tmp_path / 'r.html'))
    html = open(path).read()
    assert '<!doctype html>' in html
    assert 'Image optimisation report' in html
    assert 'prefers-color-scheme' in html          # readable in dark mode
    assert html.count('<tr>') >= 4


def test_html_report_escapes_hostile_filenames(sample_tree, tmp_path):
    summary = _summary(sample_tree, tmp_path)
    summary.results[0].decision = '<script>alert(1)</script>'
    html = open(report.write_html_report(summary, str(tmp_path / 'r.html'))).read()
    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


def test_picture_markup_wires_up_the_responsive_set(sample_tree, tmp_path):
    summary = _summary(sample_tree, tmp_path, widths=(320, 160))
    result = next(r for r in summary.succeeded if r.source.endswith('photo.jpg'))
    markup = report.picture_markup(result, summary.output_root, base_url='/img',
                                   sizes='(max-width: 600px) 100vw, 600px')
    assert markup.startswith('<picture>')
    assert 'type="image/webp"' in markup
    assert '320w' in markup and '160w' in markup
    assert 'sizes="(max-width: 600px) 100vw, 600px"' in markup
    assert 'loading="lazy"' in markup
    assert 'width=' in markup and 'height=' in markup   # no layout shift
    assert '/img/' in markup


def test_picture_markup_includes_alt_text_and_escapes_it(sample_tree, tmp_path):
    summary = _summary(sample_tree, tmp_path)
    result = summary.succeeded[0]
    result.alt_text = 'A "quoted" <tag> caption'
    markup = report.picture_markup(result, summary.output_root)
    assert 'alt="A &quot;quoted&quot; &lt;tag&gt; caption"' in markup


def test_markup_file_covers_every_image(sample_tree, tmp_path):
    summary = _summary(sample_tree, tmp_path)
    path = report.write_markup(summary, str(tmp_path / 'snippets.html'))
    content = open(path).read()
    assert content.count('<picture>') == 4


def test_markup_uses_forward_slashes_for_nested_paths(sample_tree, tmp_path):
    summary = _summary(sample_tree, tmp_path)
    result = next(r for r in summary.succeeded if 'nested' in r.source)
    markup = report.picture_markup(result, summary.output_root, base_url='/a')
    assert '/a/nested/inner.webp' in markup
    assert '\\' not in markup
