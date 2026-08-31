import json
import os

import pytest

from image_optimizer.cli import build_parser, main


def test_help_lists_the_presets(capsys):
    assert main(['--list-presets']) == 0
    assert 'Web (recommended)' in capsys.readouterr().out


def test_list_formats_reports_availability(capsys):
    assert main(['--list-formats']) == 0
    out = capsys.readouterr().out
    assert 'webp' in out and 'jpeg' in out


def test_missing_folders_are_rejected():
    with pytest.raises(SystemExit):
        main([])


def test_a_nonexistent_input_folder_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        main([str(tmp_path / 'nope'), str(tmp_path / 'out')])


def test_dry_run_writes_nothing(sample_tree, tmp_path, capsys):
    out = tmp_path / 'out'
    assert main([str(sample_tree), str(out), '--dry-run']) == 0
    assert '4 image(s) would be processed' in capsys.readouterr().out
    assert not out.exists()


def test_a_full_run_optimises_and_reports(sample_tree, tmp_path, capsys):
    out = tmp_path / 'out'
    code = main([str(sample_tree), str(out), '-f', 'webp',
                 '--json', str(tmp_path / 'r.json'), '--verbose'])
    assert code == 0
    data = json.loads((tmp_path / 'r.json').read_text())
    assert data['totals']['optimized'] == 4
    assert data['totals']['saved_ratio'] > 0
    assert 'smaller' in capsys.readouterr().out


def test_widths_are_parsed_and_written(sample_tree, tmp_path):
    out = tmp_path / 'out'
    main([str(sample_tree), str(out), '-f', 'webp', '--widths', '320, 160'])
    assert (out / 'photo-320w.webp').exists()
    assert (out / 'photo-160w.webp').exists()


def test_markup_defaults_into_the_output_folder(sample_tree, tmp_path):
    out = tmp_path / 'out'
    main([str(sample_tree), str(out), '-f', 'webp', '--markup',
          '--base-url', '/img'])
    markup = (out / 'snippets.html').read_text()
    assert '<picture>' in markup and '/img/' in markup


def test_an_empty_input_folder_exits_nonzero(tmp_path, capsys):
    (tmp_path / 'empty').mkdir()
    assert main([str(tmp_path / 'empty'), str(tmp_path / 'out')]) == 1
    assert 'No supported images' in capsys.readouterr().err


def test_a_failing_file_makes_the_run_exit_nonzero(sample_tree, tmp_path):
    (sample_tree / 'broken.png').write_bytes(b'nope')
    assert main([str(sample_tree), str(tmp_path / 'out'), '-f', 'webp', '--quiet']) == 1


def test_cli_flags_override_the_preset(sample_tree, tmp_path):
    parser = build_parser()
    args = parser.parse_args([str(sample_tree), str(tmp_path / 'out'),
                              '--preset', 'Thumbnails', '-q', '55', '-m', 'fixed'])
    assert args.quality == 55 and args.mode == 'fixed'


def test_absent_flags_do_not_override_anything():
    args = build_parser().parse_args(['in', 'out'])
    assert args.quality is None and args.mode is None
    assert args.strip_metadata is None and args.recursive is None


def test_skip_existing_is_fast_the_second_time(sample_tree, tmp_path):
    out = tmp_path / 'out'
    main([str(sample_tree), str(out), '-f', 'webp', '--skip-existing', '--quiet'])
    import time
    started = time.time()
    main([str(sample_tree), str(out), '-f', 'webp', '--skip-existing', '--quiet'])
    assert time.time() - started < 1.0      # a stat() per file, not a re-encode


def test_alt_text_is_skipped_cleanly_without_credentials(sample_tree, tmp_path,
                                                         monkeypatch, capsys):
    from image_optimizer import ai
    monkeypatch.setattr(ai, 'sdk_available', lambda: False)
    code = main([str(sample_tree), str(tmp_path / 'out'), '-f', 'webp',
                 '--alt-text', '--quiet'])
    assert code == 0
    assert 'Alt text skipped' in capsys.readouterr().err
