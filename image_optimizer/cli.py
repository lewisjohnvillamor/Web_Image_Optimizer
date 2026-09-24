"""Command-line interface.

A desktop GUI cannot run in CI. Same engine, same presets, scriptable:

    python -m image_optimizer ./src ./dist --preset "Web (recommended)"
    python -m image_optimizer ./src ./dist --widths 1600,800,400 --markup
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from typing import List, Optional, Sequence

from . import formats as fmt
from . import report
from .ai import AISettings, AIUnavailable, MODEL_CHOICES, apply_to_results, describe_batch
from .batch import default_workers, resolve_input, run_batch
from .config import BUILTIN_PRESETS, AppConfig, load as load_config
from .engine import MODE_FIXED, MODE_LOSSLESS, MODE_SMART, OptimizeSettings
from .quality import QUALITY_TARGETS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='image-optimizer',
        description='Optimise images for the web: per-image perceptual quality, '
                    'modern formats, responsive variants and AI alt text.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Presets: ' + ', '.join(f'"{n}"' for n in BUILTIN_PRESETS))

    parser.add_argument('input', nargs='?',
                        help='folder of source images, or a single image file')
    parser.add_argument('output', nargs='?', help='destination folder')

    parser.add_argument('--preset', help='start from a named preset')
    parser.add_argument('--list-presets', action='store_true',
                        help='show presets and exit')
    parser.add_argument('--list-formats', action='store_true',
                        help='show writable output formats and exit')

    enc = parser.add_argument_group('encoding')
    enc.add_argument('-f', '--format', dest='output_format',
                     choices=['auto', 'webp', 'avif', 'jpeg', 'png'],
                     help="output format ('auto' keeps the smallest of AVIF/WebP)")
    enc.add_argument('-m', '--mode', choices=[MODE_SMART, MODE_FIXED, MODE_LOSSLESS],
                     help='smart = search for the cheapest quality that still '
                          'hits the visual target')
    enc.add_argument('-t', '--target', choices=sorted(QUALITY_TARGETS),
                     help='visual target for smart mode')
    enc.add_argument('-q', '--quality', type=int, help='quality for fixed mode')
    enc.add_argument('-e', '--effort', type=int, choices=range(0, 7),
                     metavar='0-6', help='encoder CPU budget (default 4)')
    enc.add_argument('--no-auto', dest='auto_settings', action='store_false',
                     default=None, help='disable per-image content analysis')
    enc.add_argument('--keep-metadata', dest='strip_metadata', action='store_false',
                     default=None, help='keep EXIF and colour profiles')
    enc.add_argument('--allow-larger', dest='never_larger', action='store_false',
                     default=None,
                     help='write output even when it is bigger than the source')
    enc.add_argument('--svg', dest='vectorize', action='store_true', default=None,
                     help='also trace logos and flat graphics to SVG (needs '
                          'vtracer and resvg-py); kept only if it verifies '
                          'against the source')
    enc.add_argument('--svg-min-score', dest='vector_min_score', type=float,
                     default=None, metavar='0-1',
                     help='SSIM a trace must reach to be kept (default 0.95)')

    size = parser.add_argument_group('sizing')
    size.add_argument('--max-width', type=int)
    size.add_argument('--max-height', type=int)
    size.add_argument('--widths', help='responsive widths, e.g. 1600,800,400')

    run = parser.add_argument_group('run')
    run.add_argument('-j', '--workers', type=int, default=None)
    run.add_argument('--no-recursive', dest='recursive', action='store_false',
                     default=None, help='do not descend into subfolders')
    run.add_argument('--exclude', action='append', metavar='GLOB', default=None,
                     help='skip paths matching this glob, relative to the input '
                          'folder (e.g. "icons/*", "*.gif"). Repeatable.')
    run.add_argument('--skip-existing', action='store_true', default=None,
                     help='leave up-to-date outputs alone (incremental builds)')
    run.add_argument('--dry-run', action='store_true',
                     help='measure what the run would save and report it '
                          'without writing anything')
    run.add_argument('--list', dest='list_only', action='store_true',
                     help='just list the files that would be processed, and exit')
    run.add_argument('-v', '--verbose', action='store_true')
    run.add_argument('--quiet', action='store_true')

    out = parser.add_argument_group('reports')
    out.add_argument('--json', metavar='PATH', help='write a JSON report')
    out.add_argument('--csv', metavar='PATH', help='write a CSV report')
    out.add_argument('--html', metavar='PATH', help='write an HTML report')
    out.add_argument('--markup', nargs='?', const='', metavar='PATH',
                     help='write <picture> snippets (default: snippets.html '
                          'in the output folder)')
    out.add_argument('--base-url', default='', help='URL prefix used in markup')
    out.add_argument('--sizes', default='100vw', help='sizes attribute for markup')

    ai = parser.add_argument_group('AI metadata (optional, sends images to the API)')
    ai.add_argument('--alt-text', action='store_true',
                    help='generate WCAG alt text with Claude')
    ai.add_argument('--ai-model', default=None,
                    choices=[m for m, _ in MODEL_CHOICES])
    ai.add_argument('--ai-context', default='',
                    help='what the site is about, to steer the wording')
    ai.add_argument('--ai-concurrency', type=int, default=4)
    return parser


def _apply_overrides(config: AppConfig, args: argparse.Namespace) -> OptimizeSettings:
    settings = config.settings
    simple = ('output_format', 'mode', 'target', 'quality', 'effort',
              'auto_settings', 'strip_metadata', 'never_larger', 'max_width',
              'max_height', 'recursive', 'skip_existing', 'vectorize',
              'vector_min_score')
    for name in simple:
        value = getattr(args, name, None)
        if value is not None:
            setattr(settings, name, value)
    if args.widths:
        settings.widths = tuple(sorted(
            {int(w) for w in args.widths.replace(' ', '').split(',') if w},
            reverse=True))
    return settings


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_presets:
        for name, preset in BUILTIN_PRESETS.items():
            print(f'{name}\n    {preset["description"]}')
        return 0

    if args.list_formats:
        usable = fmt.available_formats()
        for key in ('webp', 'avif', 'jpeg', 'png'):
            mark = 'yes' if key in usable else 'no '
            note = '' if key in usable else f'  ({fmt.missing_format_hint(key)})'
            print(f'{key:5s} {mark}{note}')
        return 0

    if not args.input or not args.output:
        parser.error('an input (folder or image) and an output folder are required')
    if not os.path.exists(args.input):
        parser.error(f'input does not exist: {args.input}')

    config = load_config()
    if args.preset and not config.apply_preset(args.preset):
        parser.error(f'unknown preset: {args.preset}')
    settings = _apply_overrides(config, args)
    if args.exclude:
        settings.exclude = tuple(args.exclude)

    if settings.vectorize:
        from .vectorize import availability_hint as svg_hint
        hint = svg_hint()
        if hint:
            print(f'--svg: {hint}', file=sys.stderr)
            return 2

    # A single image resolves to its own folder as the root, so output paths
    # and reports behave exactly as they do for a folder run.
    input_root, files = resolve_input(args.input, settings.recursive,
                                      exclude=settings.exclude)
    if not files:
        where = ('that file' if os.path.isfile(args.input)
                 else f'{args.input}')
        note = ' (everything was excluded?)' if settings.exclude else ''
        print(f'No supported images found in {where}{note}', file=sys.stderr)
        return 1

    if args.list_only:
        print(f'{len(files)} image(s) would be processed:')
        for path in files:
            print('  ' + os.path.relpath(path, input_root))
        return 0

    settings.dry_run = args.dry_run

    workers = args.workers or default_workers()
    if not args.quiet:
        verb = 'Measuring' if settings.dry_run else 'Optimising'
        print(f'{verb} {len(files)} image(s) with {workers} worker(s)...')

    def on_progress(progress):
        if args.quiet:
            return
        result = progress.result
        name = os.path.relpath(result.source, input_root)
        if not result.ok:
            print(f'  [{progress.done}/{progress.total}] {name}: '
                  f'FAILED - {result.error}', file=sys.stderr)
        elif args.verbose:
            primary = result.primary
            detail = ''
            if primary:
                detail = (f' {primary.format_key} '
                          f'{"lossless" if primary.lossless else f"q{primary.quality}"}')
                if primary.score is not None:
                    detail += f' ssim {primary.score:.4f}'
            if result.vector:
                detail += f' + svg {report.format_bytes(result.vector.size)}'
            print(f'  [{progress.done}/{progress.total}] {name}: '
                  f'{report.format_bytes(result.original_size)} -> '
                  f'{report.format_bytes(result.new_size)} '
                  f'({-result.saved_ratio * 100:+.1f}%){detail}')
        else:
            print(f'  [{progress.done}/{progress.total}] {name}', end='\r', flush=True)

    cancel = threading.Event()
    try:
        summary = run_batch(input_root, args.output, settings, files=files,
                            workers=workers, progress=on_progress, cancel=cancel)
    except KeyboardInterrupt:
        cancel.set()
        print('\nCancelled.', file=sys.stderr)
        return 130

    if args.alt_text and not settings.dry_run:
        _run_alt_text(summary, args)
    elif args.alt_text:
        print('Alt text skipped: a dry run does not send images to the API.',
              file=sys.stderr)

    if not args.quiet:
        print('\r' + ' ' * 60, end='\r')
        skipped = len(summary.skipped)
        skipped_note = f' ({skipped} already up to date)' if skipped else ''
        verb = 'Measured' if settings.dry_run else 'Optimised'
        print(f'{verb} {len(summary.succeeded)} image(s){skipped_note} in '
              f'{report.format_duration(summary.elapsed)}')
        print(f'  {report.format_bytes(summary.original_bytes)} -> '
              f'{report.format_bytes(summary.new_bytes)} '
              f'({summary.saved_ratio * 100:.1f}% smaller, '
              f'{report.format_bytes(summary.saved_bytes)} saved)')
        if settings.widths:
            verb = 'across' if settings.dry_run else 'written across'
            print(f'  {report.format_bytes(summary.variant_bytes)} {verb} '
                  f'all responsive variants')
        if settings.vectorize:
            traced = sum(1 for r in summary.succeeded if r.vector)
            noun = 'traceable to SVG' if settings.dry_run else 'also written as SVG'
            print(f'  {traced} image(s) {noun}')
        if settings.dry_run:
            print('  (dry run - nothing was written)')
        if summary.failed:
            print(f'  {len(summary.failed)} failed', file=sys.stderr)

    for path, writer in ((args.json, report.write_json), (args.csv, report.write_csv),
                         (args.html, report.write_html_report)):
        if path:
            print(f'Wrote {writer(summary, path)}')
    if args.markup is not None and settings.dry_run:
        print('Markup skipped: a dry run writes no images for it to reference.',
              file=sys.stderr)
    elif args.markup is not None:
        path = args.markup or os.path.join(args.output, 'snippets.html')
        print(f'Wrote {report.write_markup(summary, path, args.base_url, args.sizes)}')

    return 1 if summary.failed else 0


def _run_alt_text(summary, args: argparse.Namespace) -> None:
    ai_settings = AISettings(enabled=True, context=args.ai_context,
                             concurrency=args.ai_concurrency)
    if args.ai_model:
        ai_settings.model = args.ai_model
    targets = [r.source for r in summary.succeeded]
    if not args.quiet:
        print(f'\nGenerating alt text for {len(targets)} image(s) '
              f'with {ai_settings.model}...')
    try:
        metadata = describe_batch(targets, ai_settings)
    except AIUnavailable as exc:
        print(f'Alt text skipped: {exc}', file=sys.stderr)
        return
    apply_to_results(summary.results, metadata)
    failures = [m for m in metadata.values() if m.error]
    if not args.quiet:
        described = len(metadata) - len(failures)
        tokens = sum(m.input_tokens + m.output_tokens for m in metadata.values())
        print(f'  described {described}/{len(targets)} ({tokens:,} tokens)')
    for meta in failures:
        print(f'  {os.path.basename(meta.source)}: {meta.error}', file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
