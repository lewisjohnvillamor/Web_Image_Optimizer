"""Web Image Optimizer - measure-then-encode image optimisation for the web.

Public surface:

    from image_optimizer import OptimizeSettings, run_batch, optimize_file
"""
from .analysis import ImageStats, Recommendation, analyze, recommend
from .batch import BatchProgress, BatchSummary, discover, run_batch
from .engine import (MODE_FIXED, MODE_LOSSLESS, MODE_SMART, FileResult,
                     OptimizeSettings, Variant, optimize_file)
from .quality import QUALITY_TARGETS, ssim

__version__ = '2.0.0'
__all__ = [
    'ImageStats', 'Recommendation', 'analyze', 'recommend',
    'BatchProgress', 'BatchSummary', 'discover', 'run_batch',
    'FileResult', 'OptimizeSettings', 'Variant', 'optimize_file',
    'MODE_SMART', 'MODE_FIXED', 'MODE_LOSSLESS',
    'QUALITY_TARGETS', 'ssim', '__version__',
]
