import os
import threading

from image_optimizer.batch import BatchProgress, default_workers, discover, run_batch
from image_optimizer.engine import OptimizeSettings


def test_discover_finds_images_recursively(sample_tree):
    found = discover(str(sample_tree), recursive=True)
    assert len(found) == 4
    assert any('nested' in p for p in found)


def test_discover_can_stay_shallow(sample_tree):
    assert len(discover(str(sample_tree), recursive=False)) == 3


def test_discover_ignores_non_images(sample_tree):
    (sample_tree / 'notes.txt').write_text('hello')
    (sample_tree / 'archive.zip').write_bytes(b'PK\x03\x04')
    assert len(discover(str(sample_tree))) == 4


def test_discover_on_a_missing_folder_returns_empty(tmp_path):
    assert discover(str(tmp_path / 'nope')) == []


def test_discover_is_sorted_for_stable_runs(sample_tree):
    assert discover(str(sample_tree)) == sorted(discover(str(sample_tree)))


def test_run_batch_processes_everything(sample_tree, tmp_path):
    summary = run_batch(str(sample_tree), str(tmp_path / 'out'),
                        OptimizeSettings(output_format='webp'), workers=2)
    assert len(summary.succeeded) == 4
    assert not summary.failed
    assert summary.saved_bytes > 0
    assert 0 < summary.saved_ratio < 1


def test_progress_is_reported_for_every_file(sample_tree, tmp_path):
    seen = []
    run_batch(str(sample_tree), str(tmp_path / 'out'),
              OptimizeSettings(output_format='webp'), workers=2,
              progress=seen.append)
    assert len(seen) == 4
    assert [p.done for p in seen] == [1, 2, 3, 4]
    assert seen[-1].fraction == 1.0


def test_eta_appears_once_there_is_something_to_extrapolate_from():
    assert BatchProgress(1, 10, 'x', None, 5.0).eta_seconds is None
    eta = BatchProgress(2, 10, 'x', None, 4.0).eta_seconds
    assert eta == 16.0
    assert BatchProgress(10, 10, 'x', None, 4.0).eta_seconds is None


def test_a_broken_file_does_not_stop_the_batch(sample_tree, tmp_path):
    (sample_tree / 'broken.png').write_bytes(b'not an image at all')
    summary = run_batch(str(sample_tree), str(tmp_path / 'out'),
                        OptimizeSettings(output_format='webp'), workers=2)
    assert len(summary.failed) == 1
    assert len(summary.succeeded) == 4


def test_cancelling_stops_the_run(sample_tree, tmp_path):
    cancel = threading.Event()
    cancel.set()
    summary = run_batch(str(sample_tree), str(tmp_path / 'out'),
                        OptimizeSettings(output_format='webp'), workers=1,
                        cancel=cancel)
    assert summary.cancelled
    assert all(r.skipped for r in summary.results)


def test_an_empty_folder_is_not_an_error(tmp_path):
    (tmp_path / 'empty').mkdir()
    summary = run_batch(str(tmp_path / 'empty'), str(tmp_path / 'out'),
                        OptimizeSettings())
    assert summary.results == [] and not summary.cancelled


def test_results_are_sorted_regardless_of_completion_order(sample_tree, tmp_path):
    summary = run_batch(str(sample_tree), str(tmp_path / 'out'),
                        OptimizeSettings(output_format='webp'), workers=4)
    assert [r.source for r in summary.results] == sorted(r.source for r in summary.results)


def test_variant_bytes_counts_every_file_written(sample_tree, tmp_path):
    summary = run_batch(str(sample_tree), str(tmp_path / 'out'),
                        OptimizeSettings(output_format='webp', widths=(200,)),
                        workers=2)
    assert summary.variant_bytes >= summary.new_bytes


def test_default_workers_is_sane():
    assert 1 <= default_workers() <= 8
