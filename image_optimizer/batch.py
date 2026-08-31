"""Batch discovery and parallel execution with cancellation.

Encoding is the bottleneck and Pillow drops the GIL inside the codecs, so a
thread pool gets near-linear speedup without paying to pickle decoded images
across processes.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from .engine import FileResult, OptimizeSettings, optimize_file
from .formats import INPUT_EXTENSIONS

ProgressCallback = Callable[['BatchProgress'], None]


@dataclass
class BatchProgress:
    done: int
    total: int
    current: str
    result: Optional[FileResult] = None
    elapsed: float = 0.0

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0

    @property
    def eta_seconds(self) -> Optional[float]:
        if self.done < 2 or self.done >= self.total:
            return None
        return (self.elapsed / self.done) * (self.total - self.done)


@dataclass
class BatchSummary:
    results: List[FileResult] = field(default_factory=list)
    elapsed: float = 0.0
    cancelled: bool = False
    input_root: str = ''
    output_root: str = ''
    settings: Optional[OptimizeSettings] = None

    @property
    def succeeded(self) -> List[FileResult]:
        return [r for r in self.results if r.ok and not r.skipped]

    @property
    def failed(self) -> List[FileResult]:
        return [r for r in self.results if not r.ok]

    @property
    def skipped(self) -> List[FileResult]:
        return [r for r in self.results if r.skipped]

    @property
    def original_bytes(self) -> int:
        return sum(r.original_size for r in self.succeeded)

    @property
    def new_bytes(self) -> int:
        return sum(r.new_size for r in self.succeeded)

    @property
    def saved_bytes(self) -> int:
        return self.original_bytes - self.new_bytes

    @property
    def saved_ratio(self) -> float:
        return self.saved_bytes / self.original_bytes if self.original_bytes else 0.0

    @property
    def variant_bytes(self) -> int:
        """Every byte written, including responsive variants."""
        return sum(v.size for r in self.succeeded for v in r.variants)


def discover(input_root: str, recursive: bool = True,
             extensions: Sequence[str] = INPUT_EXTENSIONS) -> List[str]:
    """Find candidate images under a folder, sorted for stable ordering."""
    exts = tuple(e.lower() for e in extensions)
    found: List[str] = []
    if not os.path.isdir(input_root):
        return found
    if recursive:
        for root, dirs, files in os.walk(input_root):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
            for name in sorted(files):
                if name.lower().endswith(exts):
                    found.append(os.path.join(root, name))
        # os.walk emits a folder's own files before descending, so the raw
        # order is not sorted overall. Sort the whole list for stable runs.
        found.sort()
    else:
        for name in sorted(os.listdir(input_root)):
            path = os.path.join(input_root, name)
            if os.path.isfile(path) and name.lower().endswith(exts):
                found.append(path)
    return found


def default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2)))


def run_batch(input_root: str, output_root: str, settings: OptimizeSettings,
              files: Optional[Iterable[str]] = None,
              workers: Optional[int] = None,
              progress: Optional[ProgressCallback] = None,
              cancel: Optional[threading.Event] = None) -> BatchSummary:
    """Optimise every discovered file, reporting progress as results land."""
    paths = list(files) if files is not None else discover(input_root, settings.recursive)
    summary = BatchSummary(input_root=input_root, output_root=output_root,
                           settings=settings)
    total = len(paths)
    started = time.time()
    if not total:
        summary.elapsed = 0.0
        return summary

    cancel = cancel or threading.Event()
    workers = workers or default_workers()
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_guarded, path, input_root, output_root, settings, cancel): path
                   for path in paths}
        for future in as_completed(futures):
            try:
                result = future.result()
            except CancelledError:
                # Cancelled before it started - nothing was written, so there
                # is nothing to report for it.
                continue
            done += 1
            summary.results.append(result)
            if progress:
                progress(BatchProgress(done, total, futures[future], result,
                                       time.time() - started))
            if cancel.is_set():
                # Stop scheduling anything that has not started yet.
                for pending, _ in futures.items():
                    pending.cancel()

    summary.cancelled = cancel.is_set()
    summary.elapsed = time.time() - started
    summary.results.sort(key=lambda r: r.source)
    return summary


def _guarded(path: str, input_root: str, output_root: str,
             settings: OptimizeSettings, cancel: threading.Event) -> FileResult:
    if cancel.is_set():
        result = FileResult(source=path, ok=True, skipped=True, note='cancelled')
        return result
    return optimize_file(path, input_root, output_root, settings)
