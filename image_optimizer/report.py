"""Reports and ready-to-paste HTML markup.

Optimised files on disk are only half the job - the bytes only reach a
visitor if the page actually references them. So the tool emits the
``<picture>`` markup for every image it produced, wired to the responsive
variants it just wrote.
"""
from __future__ import annotations

import csv
import html
import json
import os
from typing import Iterable, List, Optional

from .batch import BatchSummary
from .engine import FileResult, Variant


def format_bytes(size: Optional[float]) -> str:
    """Human-readable byte count (KiB/MiB/...)."""
    if size is None or size < 0:
        return 'N/A'
    if size == 0:
        return '0 B'
    labels = (' B', ' KiB', ' MiB', ' GiB', ' TiB')
    n = 0
    value = float(size)
    while value >= 1024 and n < len(labels) - 1:
        value /= 1024.0
        n += 1
    return f'{value:.1f}{labels[n]}'


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f'{seconds:.1f}s'
    minutes, secs = divmod(int(seconds), 60)
    return f'{minutes}m {secs}s'


# --------------------------------------------------------------------------
# Machine-readable output
# --------------------------------------------------------------------------

def summary_dict(summary: BatchSummary) -> dict:
    return {
        'input_root': summary.input_root,
        'output_root': summary.output_root,
        'settings': summary.settings.to_dict() if summary.settings else None,
        'elapsed_seconds': round(summary.elapsed, 3),
        'cancelled': summary.cancelled,
        'totals': {
            'files': len(summary.results),
            'optimized': len(summary.succeeded),
            'skipped': len(summary.skipped),
            'failed': len(summary.failed),
            'original_bytes': summary.original_bytes,
            'new_bytes': summary.new_bytes,
            'saved_bytes': summary.saved_bytes,
            'saved_ratio': round(summary.saved_ratio, 4),
            'bytes_written_including_variants': summary.variant_bytes,
        },
        'files': [r.to_dict() for r in summary.results],
    }


def write_json(summary: BatchSummary, path: str) -> str:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(summary_dict(summary), fh, indent=2)
    return path


CSV_COLUMNS = ('source', 'output', 'format', 'quality', 'lossless', 'ssim',
               'content_kind', 'original_bytes', 'new_bytes', 'saved_bytes',
               'saved_percent', 'status', 'note')


def write_csv(summary: BatchSummary, path: str) -> str:
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for result in summary.results:
            primary = result.primary
            writer.writerow([
                result.source,
                primary.path if primary else '',
                primary.format_key if primary else '',
                primary.quality if primary else '',
                primary.lossless if primary else '',
                primary.score if primary and primary.score is not None else '',
                result.stats.kind if result.stats else '',
                result.original_size,
                result.new_size,
                result.saved_bytes,
                round(result.saved_ratio * 100, 2),
                'failed' if not result.ok else ('skipped' if result.skipped else 'ok'),
                result.error or result.note or '',
            ])
    return path


# --------------------------------------------------------------------------
# HTML markup
# --------------------------------------------------------------------------

def _web_path(path: str, output_root: str, base_url: str) -> str:
    rel = os.path.relpath(path, output_root).replace(os.sep, '/')
    base = base_url.rstrip('/')
    return f'{base}/{rel}' if base else rel


def picture_markup(result: FileResult, output_root: str, base_url: str = '',
                   sizes: str = '100vw', lazy: bool = True) -> str:
    """A ``<picture>`` element wired to this file's variants."""
    if not result.variants:
        return ''

    by_format: dict[str, List[Variant]] = {}
    for variant in result.variants:
        by_format.setdefault(variant.format_key, []).append(variant)

    lines = ['<picture>']
    fallback: Optional[Variant] = None
    for format_key, variants in by_format.items():
        variants = sorted(variants, key=lambda v: v.width)
        fallback = fallback or variants[-1]
        if len(variants) > 1:
            srcset = ', '.join(
                f'{_web_path(v.path, output_root, base_url)} {v.width}w' for v in variants)
            lines.append(f'  <source type="{variants[0].mime}" '
                         f'srcset="{srcset}" sizes="{sizes}">')
        else:
            lines.append(f'  <source type="{variants[0].mime}" '
                         f'srcset="{_web_path(variants[0].path, output_root, base_url)}">')

    alt = html.escape(result.alt_text or '', quote=True)
    attrs = [f'src="{_web_path(fallback.path, output_root, base_url)}"',
             f'width="{fallback.width}"', f'height="{fallback.height}"',
             f'alt="{alt}"']
    if lazy:
        attrs.extend(['loading="lazy"', 'decoding="async"'])
    lines.append('  <img ' + ' '.join(attrs) + '>')
    lines.append('</picture>')
    return '\n'.join(lines)


def write_markup(summary: BatchSummary, path: str, base_url: str = '',
                 sizes: str = '100vw') -> str:
    """Write one ``<picture>`` block per optimised image."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    blocks = []
    for result in summary.succeeded:
        markup = picture_markup(result, summary.output_root, base_url, sizes)
        if markup:
            name = os.path.relpath(result.source, summary.input_root)
            blocks.append(f'<!-- {html.escape(name)} -->\n{markup}')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n\n'.join(blocks) + '\n')
    return path


# --------------------------------------------------------------------------
# Human-readable report
# --------------------------------------------------------------------------

_REPORT_CSS = """
:root { color-scheme: light dark; --fg:#111; --muted:#666; --bg:#fff;
        --card:#f6f7f9; --line:#e3e5e9; --good:#1a7f4b; --bad:#b23c17; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e8ea; --muted:#9a9aa2; --bg:#141416; --card:#1e1e22;
          --line:#2c2c32; --good:#4ec98a; --bad:#ef8b63; } }
* { box-sizing:border-box; }
body { margin:0; padding:32px; background:var(--bg); color:var(--fg);
       font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
h1 { font-size:22px; margin:0 0 4px; }
.sub { color:var(--muted); margin-bottom:24px; }
.cards { display:flex; flex-wrap:wrap; gap:12px; margin-bottom:28px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:14px 18px; min-width:150px; }
.card .v { font-size:22px; font-weight:600; }
.card .k { color:var(--muted); font-size:12px; text-transform:uppercase;
           letter-spacing:.04em; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
        vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
.good { color:var(--good); } .bad { color:var(--bad); }
.bar { height:6px; background:var(--line); border-radius:3px; overflow:hidden;
       min-width:70px; }
.bar > i { display:block; height:100%; background:var(--good); }
code { background:var(--card); padding:1px 5px; border-radius:4px; font-size:12px; }
.reason { color:var(--muted); font-size:12px; }
"""


def write_html_report(summary: BatchSummary, path: str) -> str:
    """A standalone HTML summary a human can actually read."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    rows = []
    for result in sorted(summary.results, key=lambda r: -r.saved_bytes):
        name = html.escape(os.path.relpath(result.source, summary.input_root))
        if not result.ok:
            rows.append(f'<tr><td>{name}</td><td colspan="6" class="bad">'
                        f'{html.escape(result.error or "failed")}</td></tr>')
            continue
        primary = result.primary
        pct = result.saved_ratio * 100
        cls = 'good' if pct >= 0 else 'bad'
        kind = html.escape(result.stats.kind_label if result.stats else '')
        setting = (f'{primary.format_key} '
                   f'{"lossless" if primary.lossless else f"q{primary.quality}"}'
                   ) if primary else ''
        ssim = f'{primary.score:.4f}' if primary and primary.score is not None else '&mdash;'
        variants = len(result.variants)
        rows.append(
            f'<tr><td>{name}<div class="reason">{html.escape(result.decision or "")}</div></td>'
            f'<td>{kind}</td><td><code>{html.escape(setting)}</code></td>'
            f'<td class="num">{ssim}</td>'
            f'<td class="num">{format_bytes(result.original_size)}</td>'
            f'<td class="num">{format_bytes(result.new_size)}</td>'
            f'<td class="num {cls}">{pct:+.1f}%<div class="bar">'
            f'<i style="width:{max(0, min(100, pct)):.0f}%"></i></div>'
            f'<div class="reason">{variants} file(s)</div></td></tr>')

    cards = [
        ('Images optimised', str(len(summary.succeeded))),
        ('Original weight', format_bytes(summary.original_bytes)),
        ('New weight', format_bytes(summary.new_bytes)),
        ('Saved', f'{summary.saved_ratio * 100:.1f}%'),
        ('Bytes saved', format_bytes(summary.saved_bytes)),
        ('Time', format_duration(summary.elapsed)),
    ]
    if summary.failed:
        cards.append(('Failed', str(len(summary.failed))))

    card_html = ''.join(
        f'<div class="card"><div class="k">{html.escape(k)}</div>'
        f'<div class="v">{html.escape(v)}</div></div>' for k, v in cards)

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Image optimisation report</title><style>{_REPORT_CSS}</style></head>
<body>
<h1>Image optimisation report</h1>
<div class="sub">{html.escape(summary.input_root)} &rarr; {html.escape(summary.output_root)}</div>
<div class="cards">{card_html}</div>
<table><thead><tr><th>File</th><th>Content</th><th>Encoded as</th>
<th>SSIM</th><th>Before</th><th>After</th><th>Change</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>
"""
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(doc)
    return path
