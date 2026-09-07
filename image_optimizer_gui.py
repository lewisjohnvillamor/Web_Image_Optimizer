"""Web Image Optimizer - desktop GUI.

Thin presentation layer over the ``image_optimizer`` package: every decision
about formats, quality and analysis lives in the package (and is unit
tested); this file only collects settings, runs the batch on a worker thread
and renders what comes back.
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from typing import List, Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox

from image_optimizer import __version__
from image_optimizer import ai as ai_module
from image_optimizer import config as config_module
from image_optimizer import formats as fmt
from image_optimizer import report
from image_optimizer import vectorize as vector_module
from image_optimizer.batch import (BatchProgress, BatchSummary, default_workers,
                                   discover, run_batch)
from image_optimizer.engine import MODE_FIXED, MODE_LOSSLESS, MODE_SMART, FileResult

log_queue: "queue.Queue[logging.LogRecord]" = queue.Queue()
logger = logging.getLogger('image_optimizer')
logger.setLevel(logging.INFO)

TARGET_LABELS = {
    'maximum': 'Maximum  (visually lossless)',
    'high': 'High  (hero and product imagery)',
    'balanced': 'Balanced  (recommended)',
    'small': 'Smallest  (thumbnails, listings)',
}
LABEL_TO_TARGET = {v: k for k, v in TARGET_LABELS.items()}

MODE_LABELS = {
    MODE_SMART: 'Smart - measure each image, use the cheapest quality that still looks right',
    MODE_FIXED: 'Fixed quality - one quality value for every image',
    MODE_LOSSLESS: 'Lossless - pixel-identical output',
}
LABEL_TO_MODE = {v: k for k, v in MODE_LABELS.items()}


class QueueHandler(logging.Handler):
    """Ship log records to the GUI thread without touching widgets."""

    def __init__(self, target: "queue.Queue[logging.LogRecord]"):
        super().__init__()
        self.log_queue = target

    def emit(self, record: logging.LogRecord) -> None:
        self.log_queue.put(record)


def open_in_file_manager(path: str) -> None:
    try:
        if sys.platform == 'win32':
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])
    except Exception as exc:
        logger.warning(f'Could not open {path}: {exc}')


class ImageOptimizerApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.config_data = config_module.load()
        self.settings = self.config_data.settings
        self.ai_settings = self.config_data.ai

        self.cancel_event: Optional[threading.Event] = None
        self.worker: Optional[threading.Thread] = None
        self.summary: Optional[BatchSummary] = None
        self.result_rows: List[FileResult] = []

        self.title(f'Web Image Optimizer {__version__}')
        self.geometry('1000x820')
        self.minsize(880, 700)
        ctk.set_appearance_mode(self.config_data.appearance)
        ctk.set_default_color_theme('blue')

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_folders()
        self._build_tabs()
        self._build_footer()

        handler = QueueHandler(log_queue)
        handler.setFormatter(logging.Formatter('%(asctime)s  %(message)s',
                                               datefmt='%H:%M:%S'))
        logger.addHandler(handler)
        self.after(100, self._drain_log_queue)

        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self._sync_widget_states()
        self._refresh_ai_hint()
        logger.info(f'Ready. Output formats available: '
                    f'{", ".join(fmt.available_formats())}')

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color='transparent')
        header.grid(row=0, column=0, padx=20, pady=(16, 4), sticky='ew')
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header, text='Web Image Optimizer',
                     font=ctk.CTkFont(size=20, weight='bold')
                     ).grid(row=0, column=0, sticky='w')

        preset_box = ctk.CTkFrame(header, fg_color='transparent')
        preset_box.grid(row=0, column=2, sticky='e')
        ctk.CTkLabel(preset_box, text='Preset:').pack(side='left', padx=(0, 6))
        self.preset_var = ctk.StringVar(value='Web (recommended)')
        self.preset_menu = ctk.CTkOptionMenu(
            preset_box, variable=self.preset_var, width=220,
            values=list(self.config_data.all_presets()),
            command=self._on_preset_selected)
        self.preset_menu.pack(side='left')
        ctk.CTkButton(preset_box, text='Save as...', width=90,
                      command=self._save_preset).pack(side='left', padx=(6, 0))

        self.preset_hint = ctk.CTkLabel(
            header, text='', anchor='w', text_color=('gray40', 'gray65'),
            font=ctk.CTkFont(size=12))
        self.preset_hint.grid(row=1, column=0, columnspan=3, sticky='w', pady=(2, 0))

    def _build_folders(self) -> None:
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=0, padx=20, pady=8, sticky='ew')
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text='Source folder').grid(
            row=0, column=0, padx=(14, 8), pady=(12, 6), sticky='w')
        self.input_path_var = ctk.StringVar(value=self.config_data.last_input)
        ctk.CTkEntry(frame, textvariable=self.input_path_var).grid(
            row=0, column=1, pady=(12, 6), sticky='ew')
        ctk.CTkButton(frame, text='Browse...', width=100, command=self._browse_input
                      ).grid(row=0, column=2, padx=(8, 14), pady=(12, 6))

        ctk.CTkLabel(frame, text='Output folder').grid(
            row=1, column=0, padx=(14, 8), pady=(6, 12), sticky='w')
        self.output_path_var = ctk.StringVar(value=self.config_data.last_output)
        ctk.CTkEntry(frame, textvariable=self.output_path_var).grid(
            row=1, column=1, pady=(6, 12), sticky='ew')
        ctk.CTkButton(frame, text='Browse...', width=100, command=self._browse_output
                      ).grid(row=1, column=2, padx=(8, 14), pady=(6, 12))

        self.scan_label = ctk.CTkLabel(frame, text='', anchor='w',
                                       text_color=('gray40', 'gray65'),
                                       font=ctk.CTkFont(size=12))
        self.scan_label.grid(row=2, column=0, columnspan=3, padx=14,
                             pady=(0, 10), sticky='w')

    def _build_tabs(self) -> None:
        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=2, column=0, padx=20, pady=8, sticky='nsew')
        for name in ('Quality', 'Output', 'AI alt text', 'Results', 'Log'):
            self.tabs.add(name)
        self._build_quality_tab(self.tabs.tab('Quality'))
        self._build_output_tab(self.tabs.tab('Output'))
        self._build_ai_tab(self.tabs.tab('AI alt text'))
        self._build_results_tab(self.tabs.tab('Results'))
        self._build_log_tab(self.tabs.tab('Log'))

    def _build_quality_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        row = 0

        self.mode_var = ctk.StringVar(value=MODE_LABELS[self.settings.mode])
        for mode in (MODE_SMART, MODE_FIXED, MODE_LOSSLESS):
            ctk.CTkRadioButton(tab, text=MODE_LABELS[mode], variable=self.mode_var,
                               value=MODE_LABELS[mode], command=self._sync_widget_states
                               ).grid(row=row, column=0, padx=16, pady=(10 if row == 0 else 4, 4),
                                      sticky='w')
            row += 1

        target_frame = ctk.CTkFrame(tab, fg_color='transparent')
        target_frame.grid(row=row, column=0, padx=16, pady=(12, 4), sticky='ew')
        target_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(target_frame, text='Visual target').grid(row=0, column=0, sticky='w')
        self.target_var = ctk.StringVar(
            value=TARGET_LABELS.get(self.settings.target, TARGET_LABELS['balanced']))
        self.target_menu = ctk.CTkOptionMenu(
            target_frame, variable=self.target_var, width=320,
            values=[TARGET_LABELS[k] for k in ('maximum', 'high', 'balanced', 'small')])
        self.target_menu.grid(row=0, column=1, padx=(12, 0), sticky='w')
        row += 1

        ctk.CTkLabel(tab, anchor='w', justify='left', text_color=('gray40', 'gray65'),
                     font=ctk.CTkFont(size=12),
                     text='Smart mode encodes each image several times and keeps the '
                          'smallest one that still\nmatches the original above the '
                          'target (measured with SSIM). Slower, much smaller files.'
                     ).grid(row=row, column=0, padx=16, pady=(0, 8), sticky='w')
        row += 1

        quality_frame = ctk.CTkFrame(tab, fg_color='transparent')
        quality_frame.grid(row=row, column=0, padx=16, pady=4, sticky='ew')
        quality_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(quality_frame, text='Fixed quality').grid(row=0, column=0, sticky='w')
        self.quality_var = ctk.IntVar(value=self.settings.quality)
        self.quality_slider = ctk.CTkSlider(
            quality_frame, from_=1, to=100, number_of_steps=99,
            variable=self.quality_var, command=self._on_quality_changed)
        self.quality_slider.grid(row=0, column=1, padx=12, sticky='ew')
        self.quality_label = ctk.CTkLabel(quality_frame, width=40,
                                          text=str(self.settings.quality))
        self.quality_label.grid(row=0, column=2)
        row += 1

        self.auto_var = ctk.BooleanVar(value=self.settings.auto_settings)
        ctk.CTkCheckBox(tab, variable=self.auto_var,
                        text='Analyse each image and adapt (photos, flat graphics and '
                             'screenshots get different treatment)'
                        ).grid(row=row, column=0, padx=16, pady=(12, 4), sticky='w')
        row += 1

        effort_frame = ctk.CTkFrame(tab, fg_color='transparent')
        effort_frame.grid(row=row, column=0, padx=16, pady=(8, 12), sticky='ew')
        effort_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(effort_frame, text='Encoder effort').grid(row=0, column=0, sticky='w')
        self.effort_var = ctk.IntVar(value=self.settings.effort)
        ctk.CTkSlider(effort_frame, from_=0, to=6, number_of_steps=6,
                      variable=self.effort_var,
                      command=lambda v: self.effort_label.configure(
                          text=f'{int(v)}  ({"faster" if v < 3 else "smaller"})')
                      ).grid(row=0, column=1, padx=12, sticky='ew')
        self.effort_label = ctk.CTkLabel(effort_frame, width=120,
                                         text=f'{self.settings.effort}')
        self.effort_label.grid(row=0, column=2)

    def _build_output_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        available = fmt.available_formats()

        ctk.CTkLabel(tab, text='Format').grid(row=0, column=0, padx=16, pady=(14, 6),
                                              sticky='w')
        self.format_var = ctk.StringVar(value=self.settings.output_format)
        ctk.CTkOptionMenu(tab, variable=self.format_var, width=200,
                          values=['auto'] + list(available)
                          ).grid(row=0, column=1, pady=(14, 6), sticky='w')
        ctk.CTkLabel(tab, text="'auto' keeps whichever of AVIF/WebP came out smaller",
                     text_color=('gray40', 'gray65'), font=ctk.CTkFont(size=12)
                     ).grid(row=0, column=2, padx=12, sticky='w')

        ctk.CTkLabel(tab, text='Max width / height').grid(row=1, column=0, padx=16,
                                                          pady=6, sticky='w')
        size_frame = ctk.CTkFrame(tab, fg_color='transparent')
        size_frame.grid(row=1, column=1, columnspan=2, pady=6, sticky='w')
        self.max_width_var = ctk.StringVar(
            value=str(self.settings.max_width or ''))
        self.max_height_var = ctk.StringVar(
            value=str(self.settings.max_height or ''))
        ctk.CTkEntry(size_frame, textvariable=self.max_width_var, width=90,
                     placeholder_text='px').pack(side='left')
        ctk.CTkLabel(size_frame, text='  x  ').pack(side='left')
        ctk.CTkEntry(size_frame, textvariable=self.max_height_var, width=90,
                     placeholder_text='px').pack(side='left')
        ctk.CTkLabel(size_frame, text='   blank = keep original size',
                     text_color=('gray40', 'gray65'),
                     font=ctk.CTkFont(size=12)).pack(side='left')

        ctk.CTkLabel(tab, text='Responsive widths').grid(row=2, column=0, padx=16,
                                                         pady=6, sticky='w')
        self.widths_var = ctk.StringVar(
            value=', '.join(str(w) for w in self.settings.widths))
        ctk.CTkEntry(tab, textvariable=self.widths_var,
                     placeholder_text='e.g. 1600, 1200, 800, 400'
                     ).grid(row=2, column=1, columnspan=2, padx=(0, 16), pady=6,
                            sticky='ew')

        ctk.CTkLabel(tab, text='Site URL prefix').grid(row=3, column=0, padx=16,
                                                       pady=6, sticky='w')
        self.base_url_var = ctk.StringVar(value=self.config_data.base_url)
        ctk.CTkEntry(tab, textvariable=self.base_url_var,
                     placeholder_text='/assets/img  - used in the generated <picture> markup'
                     ).grid(row=3, column=1, columnspan=2, padx=(0, 16), pady=6,
                            sticky='ew')

        toggles = ctk.CTkFrame(tab, fg_color='transparent')
        toggles.grid(row=4, column=0, columnspan=3, padx=12, pady=(12, 6), sticky='ew')
        self.recursive_var = ctk.BooleanVar(value=self.settings.recursive)
        self.skip_existing_var = ctk.BooleanVar(value=self.settings.skip_existing)
        self.strip_var = ctk.BooleanVar(value=self.settings.strip_metadata)
        self.srgb_var = ctk.BooleanVar(value=self.settings.convert_to_srgb)
        self.never_larger_var = ctk.BooleanVar(value=self.settings.never_larger)
        self.reports_var = ctk.BooleanVar(value=self.config_data.write_reports)
        for index, (var, text) in enumerate((
                (self.recursive_var, 'Include subfolders (structure is preserved)'),
                (self.skip_existing_var, 'Skip images whose output is already up to date'),
                (self.strip_var, 'Strip EXIF and other metadata'),
                (self.srgb_var, 'Convert to sRGB before stripping profiles (keeps colours accurate)'),
                (self.never_larger_var, 'Never write a file larger than the original'),
                (self.reports_var, 'Write report.html, report.json and snippets.html to the output folder'),
        )):
            ctk.CTkCheckBox(toggles, variable=var, text=text).grid(
                row=index, column=0, padx=4, pady=3, sticky='w')

        svg_frame = ctk.CTkFrame(tab, fg_color='transparent')
        svg_frame.grid(row=5, column=0, columnspan=3, padx=12, pady=(4, 2), sticky='ew')
        self.vectorize_var = ctk.BooleanVar(value=self.settings.vectorize)
        svg_hint = vector_module.availability_hint()
        self.vectorize_check = ctk.CTkCheckBox(
            svg_frame, variable=self.vectorize_var,
            text='Also trace logos and flat graphics to SVG (kept only if the trace '
                 'verifies against the source; the raster stays as fallback)')
        self.vectorize_check.grid(row=0, column=0, padx=4, pady=3, sticky='w')
        if svg_hint:
            self.vectorize_var.set(False)
            self.vectorize_check.configure(state='disabled')
            ctk.CTkLabel(svg_frame, text=svg_hint, anchor='w', justify='left',
                         text_color=('gray40', 'gray65'), font=ctk.CTkFont(size=12)
                         ).grid(row=1, column=0, padx=32, pady=(0, 4), sticky='w')

        worker_frame = ctk.CTkFrame(tab, fg_color='transparent')
        worker_frame.grid(row=6, column=0, columnspan=3, padx=16, pady=(6, 14),
                          sticky='w')
        ctk.CTkLabel(worker_frame, text='Parallel workers').pack(side='left')
        self.workers_var = ctk.IntVar(value=self.config_data.workers or default_workers())
        ctk.CTkSlider(worker_frame, from_=1, to=max(2, default_workers()),
                      number_of_steps=max(1, default_workers() - 1),
                      width=200, variable=self.workers_var,
                      command=lambda v: self.workers_label.configure(text=str(int(v)))
                      ).pack(side='left', padx=12)
        self.workers_label = ctk.CTkLabel(worker_frame, width=30,
                                          text=str(self.workers_var.get()))
        self.workers_label.pack(side='left')

    def _build_ai_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)

        self.ai_enabled_var = ctk.BooleanVar(value=self.ai_settings.enabled)
        ctk.CTkCheckBox(tab, variable=self.ai_enabled_var,
                        command=self._sync_widget_states,
                        text='Generate alt text and captions with Claude',
                        font=ctk.CTkFont(size=14, weight='bold')
                        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(14, 4),
                               sticky='w')

        ctk.CTkLabel(tab, justify='left', anchor='w',
                     text_color=('gray40', 'gray65'), font=ctk.CTkFont(size=12),
                     text='Missing alt text is the most common accessibility failure on the web, and\n'
                          'no compression setting can fix it. When enabled, a downscaled copy of each\n'
                          'image is sent to the Anthropic API and the alt text is written into the\n'
                          'generated <picture> markup and the reports. Off by default.'
                     ).grid(row=1, column=0, columnspan=3, padx=16, pady=(0, 10), sticky='w')

        ctk.CTkLabel(tab, text='Model').grid(row=2, column=0, padx=16, pady=6, sticky='w')
        self.ai_model_var = ctk.StringVar(value=self._model_label(self.ai_settings.model))
        self.ai_model_menu = ctk.CTkOptionMenu(
            tab, variable=self.ai_model_var, width=280,
            values=[f'{label}' for _, label in ai_module.MODEL_CHOICES])
        self.ai_model_menu.grid(row=2, column=1, pady=6, sticky='w')

        ctk.CTkLabel(tab, text='API key').grid(row=3, column=0, padx=16, pady=6, sticky='w')
        self.ai_key_var = ctk.StringVar()
        self.ai_key_entry = ctk.CTkEntry(
            tab, textvariable=self.ai_key_var, show='*',
            placeholder_text='leave blank to use the ANTHROPIC_API_KEY environment variable')
        self.ai_key_entry.grid(row=3, column=1, columnspan=2, padx=(0, 16), pady=6,
                               sticky='ew')

        ctk.CTkLabel(tab, text='Site context').grid(row=4, column=0, padx=16, pady=6,
                                                    sticky='nw')
        self.ai_context_box = ctk.CTkTextbox(tab, height=70, wrap='word')
        self.ai_context_box.grid(row=4, column=1, columnspan=2, padx=(0, 16), pady=6,
                                 sticky='ew')
        if self.ai_settings.context:
            self.ai_context_box.insert('1.0', self.ai_settings.context)

        ctk.CTkLabel(tab, text='What the site sells or covers, so descriptions land in context',
                     text_color=('gray40', 'gray65'), font=ctk.CTkFont(size=12)
                     ).grid(row=5, column=1, columnspan=2, padx=(0, 16), sticky='w')

        self.ai_filenames_var = ctk.BooleanVar(value=self.ai_settings.generate_filenames)
        ctk.CTkCheckBox(tab, variable=self.ai_filenames_var,
                        text='Also suggest SEO filenames (reported, files are not renamed)'
                        ).grid(row=6, column=0, columnspan=3, padx=16, pady=(10, 4),
                               sticky='w')

        self.ai_hint = ctk.CTkLabel(tab, text='', anchor='w', justify='left',
                                    font=ctk.CTkFont(size=12))
        self.ai_hint.grid(row=7, column=0, columnspan=3, padx=16, pady=(10, 14),
                          sticky='w')

    def _build_results_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(tab, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew', padx=6, pady=(6, 0))
        for index, (text, width) in enumerate((('File', 320), ('Content', 130),
                                               ('Encoded as', 130), ('Before', 90),
                                               ('After', 90), ('Saved', 90))):
            ctk.CTkLabel(head, text=text, width=width, anchor='w',
                         font=ctk.CTkFont(size=12, weight='bold'),
                         text_color=('gray40', 'gray65')
                         ).grid(row=0, column=index, padx=4, sticky='w')

        self.results_frame = ctk.CTkScrollableFrame(tab, label_text='')
        self.results_frame.grid(row=1, column=0, sticky='nsew', padx=6, pady=6)
        self.results_frame.grid_columnconfigure(0, weight=1)
        self.results_placeholder = ctk.CTkLabel(
            self.results_frame, text='Run an optimisation to see per-file results.',
            text_color=('gray40', 'gray65'))
        self.results_placeholder.grid(row=0, column=0, pady=20)

    def _build_log_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        self.log_textbox = ctk.CTkTextbox(tab, state='disabled', wrap='word')
        self.log_textbox.grid(row=0, column=0, sticky='nsew', padx=6, pady=6)

    def _build_footer(self) -> None:
        footer = ctk.CTkFrame(self, fg_color='transparent')
        footer.grid(row=3, column=0, padx=20, pady=(4, 16), sticky='ew')
        footer.grid_columnconfigure(0, weight=1)

        self.progressbar = ctk.CTkProgressBar(footer, mode='determinate')
        self.progressbar.grid(row=0, column=0, columnspan=4, sticky='ew')
        self.progressbar.set(0)

        self.status_label = ctk.CTkLabel(footer, text='Idle', anchor='w')
        self.status_label.grid(row=1, column=0, sticky='w', pady=(6, 8))

        self.savings_label = ctk.CTkLabel(footer, text='', anchor='e',
                                          font=ctk.CTkFont(size=13, weight='bold'))
        self.savings_label.grid(row=1, column=1, sticky='e', padx=12, pady=(6, 8))

        self.open_button = ctk.CTkButton(footer, text='Open output folder', width=150,
                                         state='disabled', command=self._open_output)
        self.open_button.grid(row=1, column=2, padx=(0, 8), pady=(6, 8))

        self.report_button = ctk.CTkButton(footer, text='View report', width=110,
                                           state='disabled', command=self._open_report)
        self.report_button.grid(row=1, column=3, pady=(6, 8))

        buttons = ctk.CTkFrame(footer, fg_color='transparent')
        buttons.grid(row=2, column=0, columnspan=4, sticky='ew')
        buttons.grid_columnconfigure(0, weight=1)
        self.start_button = ctk.CTkButton(
            buttons, text='Start optimisation', height=42,
            font=ctk.CTkFont(size=15, weight='bold'), command=self._start)
        self.start_button.grid(row=0, column=0, sticky='ew')
        self.cancel_button = ctk.CTkButton(
            buttons, text='Cancel', height=42, width=120, state='disabled',
            fg_color='gray40', hover_color='gray30', command=self._cancel)
        self.cancel_button.grid(row=0, column=1, padx=(10, 0))

    # ------------------------------------------------------------------
    # Widget state
    # ------------------------------------------------------------------
    def _model_label(self, model_id: str) -> str:
        for value, label in ai_module.MODEL_CHOICES:
            if value == model_id:
                return label
        return ai_module.MODEL_CHOICES[0][1]

    def _model_id(self, label: str) -> str:
        for value, text in ai_module.MODEL_CHOICES:
            if text == label:
                return value
        return ai_module.DEFAULT_MODEL

    def _sync_widget_states(self) -> None:
        mode = LABEL_TO_MODE.get(self.mode_var.get(), MODE_SMART)
        smart = mode == MODE_SMART
        self.target_menu.configure(state='normal' if smart else 'disabled')
        fixed = mode == MODE_FIXED
        self.quality_slider.configure(state='normal' if fixed else 'disabled')
        self.quality_label.configure(state='normal' if fixed else 'disabled')

        ai_on = self.ai_enabled_var.get()
        for widget in (self.ai_model_menu, self.ai_key_entry):
            widget.configure(state='normal' if ai_on else 'disabled')
        self._refresh_ai_hint()

    def _refresh_ai_hint(self) -> None:
        if not self.ai_enabled_var.get():
            self.ai_hint.configure(text='')
            return
        hint = ai_module.availability_hint(self._collect_ai_settings())
        if hint:
            self.ai_hint.configure(text=f'Not ready: {hint}', text_color=('#b23c17', '#ef8b63'))
        else:
            self.ai_hint.configure(text='Ready - alt text will be generated after '
                                        'the images are optimised.',
                                   text_color=('#1a7f4b', '#4ec98a'))

    def _on_quality_changed(self, value) -> None:
        self.quality_label.configure(text=str(int(float(value))))

    def _on_preset_selected(self, name: str) -> None:
        if not self.config_data.apply_preset(name):
            return
        self.settings = self.config_data.settings
        self._push_settings_to_widgets()
        preset = self.config_data.all_presets().get(name) or {}
        self.preset_hint.configure(text=preset.get('description', ''))
        logger.info(f'Preset applied: {name}')

    def _push_settings_to_widgets(self) -> None:
        s = self.settings
        self.mode_var.set(MODE_LABELS.get(s.mode, MODE_LABELS[MODE_SMART]))
        self.target_var.set(TARGET_LABELS.get(s.target, TARGET_LABELS['balanced']))
        self.quality_var.set(s.quality)
        self.quality_label.configure(text=str(s.quality))
        self.effort_var.set(s.effort)
        self.effort_label.configure(text=str(s.effort))
        self.auto_var.set(s.auto_settings)
        self.format_var.set(s.output_format)
        self.max_width_var.set(str(s.max_width or ''))
        self.max_height_var.set(str(s.max_height or ''))
        self.widths_var.set(', '.join(str(w) for w in s.widths))
        self.strip_var.set(s.strip_metadata)
        self.srgb_var.set(s.convert_to_srgb)
        self.never_larger_var.set(s.never_larger)
        if not vector_module.availability_hint():
            self.vectorize_var.set(s.vectorize)
        self._sync_widget_states()

    def _save_preset(self) -> None:
        dialog = ctk.CTkInputDialog(text='Name for this preset:', title='Save preset')
        name = (dialog.get_input() or '').strip()
        if not name:
            return
        self._collect_settings()
        self.config_data.save_preset(name)
        self.preset_menu.configure(values=list(self.config_data.all_presets()))
        self.preset_var.set(name)
        logger.info(f'Preset saved: {name}')

    # ------------------------------------------------------------------
    # Settings collection
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_int(value: str) -> Optional[int]:
        value = (value or '').strip()
        if not value:
            return None
        try:
            parsed = int(value)
            return parsed if parsed > 0 else None
        except ValueError:
            return None

    def _collect_settings(self):
        s = self.settings
        s.mode = LABEL_TO_MODE.get(self.mode_var.get(), MODE_SMART)
        s.target = LABEL_TO_TARGET.get(self.target_var.get(), 'balanced')
        s.quality = int(self.quality_var.get())
        s.effort = int(self.effort_var.get())
        s.auto_settings = bool(self.auto_var.get())
        s.output_format = self.format_var.get()
        s.max_width = self._parse_int(self.max_width_var.get())
        s.max_height = self._parse_int(self.max_height_var.get())
        widths = []
        for chunk in self.widths_var.get().replace(';', ',').split(','):
            parsed = self._parse_int(chunk)
            if parsed:
                widths.append(parsed)
        s.widths = tuple(sorted(set(widths), reverse=True))
        s.recursive = bool(self.recursive_var.get())
        s.skip_existing = bool(self.skip_existing_var.get())
        s.strip_metadata = bool(self.strip_var.get())
        s.convert_to_srgb = bool(self.srgb_var.get())
        s.never_larger = bool(self.never_larger_var.get())
        s.vectorize = bool(self.vectorize_var.get()) and not vector_module.availability_hint()
        return s

    def _collect_ai_settings(self):
        a = self.ai_settings
        a.enabled = bool(self.ai_enabled_var.get())
        a.model = self._model_id(self.ai_model_var.get())
        a.api_key = self.ai_key_var.get().strip()
        a.generate_filenames = bool(self.ai_filenames_var.get())
        try:
            a.context = self.ai_context_box.get('1.0', 'end').strip()
        except Exception:
            pass
        return a

    # ------------------------------------------------------------------
    # Folder pickers
    # ------------------------------------------------------------------
    def _browse_input(self) -> None:
        chosen = filedialog.askdirectory(title='Select the folder with your images')
        if not chosen:
            return
        self.input_path_var.set(chosen)
        if not self.output_path_var.get():
            self.output_path_var.set(os.path.join(chosen, 'optimized'))
        self._rescan()

    def _browse_output(self) -> None:
        chosen = filedialog.askdirectory(title='Select the output folder')
        if chosen:
            self.output_path_var.set(chosen)

    def _rescan(self) -> None:
        folder = self.input_path_var.get()
        if not os.path.isdir(folder):
            self.scan_label.configure(text='')
            return
        files = discover(folder, bool(self.recursive_var.get()))
        total = sum(os.path.getsize(f) for f in files if os.path.exists(f))
        self.scan_label.configure(
            text=f'{len(files)} image(s) found, {report.format_bytes(total)} total')

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    def _start(self) -> None:
        input_dir = self.input_path_var.get().strip()
        output_dir = self.output_path_var.get().strip()

        if not input_dir or not os.path.isdir(input_dir):
            messagebox.showerror('Pick a source folder',
                                 'Choose a folder that contains your images.', parent=self)
            return
        if not output_dir:
            messagebox.showerror('Pick an output folder',
                                 'Choose where the optimised images should go.', parent=self)
            return
        if os.path.abspath(input_dir) == os.path.abspath(output_dir):
            if not messagebox.askyesno(
                    'Same folder',
                    'The source and output folders are the same. Optimised files will '
                    'be written alongside your originals, and same-named files will be '
                    'overwritten.\n\nContinue?', parent=self):
                return

        settings = self._collect_settings()
        ai_settings = self._collect_ai_settings()

        if settings.output_format != 'auto' and \
                settings.output_format not in fmt.available_formats():
            messagebox.showerror('Format unavailable',
                                 fmt.missing_format_hint(settings.output_format),
                                 parent=self)
            return

        if ai_settings.enabled:
            hint = ai_module.availability_hint(ai_settings)
            if hint and not messagebox.askyesno(
                    'AI alt text unavailable',
                    f'{hint}\n\nContinue without alt text?', parent=self):
                return

        files = discover(input_dir, settings.recursive)
        if not files:
            messagebox.showwarning(
                'Nothing to do',
                f'No supported images found in {input_dir}.\n\nSupported: '
                f'{", ".join(fmt.INPUT_EXTENSIONS)}', parent=self)
            return

        self._clear_results(
            f'Optimising {len(files)} image(s) - each one appears here as it '
            f'finishes...')
        self.start_button.configure(state='disabled', text='Optimising...')
        self.cancel_button.configure(state='normal')
        self.open_button.configure(state='disabled')
        self.report_button.configure(state='disabled')
        self.progressbar.set(0)
        self.status_label.configure(text=f'Starting on {len(files)} image(s)...')
        self.savings_label.configure(text='')
        self.tabs.set('Results')
        self.update_idletasks()

        self.cancel_event = threading.Event()
        self.worker = threading.Thread(
            target=self._run_worker,
            args=(input_dir, output_dir, settings, ai_settings, files),
            daemon=True)
        self.worker.start()

    def _run_worker(self, input_dir, output_dir, settings, ai_settings, files) -> None:
        logger.info('=' * 70)
        logger.info(f'{len(files)} image(s) | mode={settings.mode} '
                    f'target={settings.target} format={settings.output_format} '
                    f'effort={settings.effort}')
        logger.info(f'{input_dir}  ->  {output_dir}')

        workers = int(self.workers_var.get())
        try:
            summary = run_batch(input_dir, output_dir, settings, files=files,
                                workers=workers,
                                progress=lambda p: self.after(0, self._on_progress, p),
                                cancel=self.cancel_event)
        except Exception as exc:
            logger.error(f'Run failed: {type(exc).__name__}: {exc}')
            self.after(0, self._on_finished, None)
            return

        if ai_settings.enabled and not summary.cancelled:
            self._run_ai(summary, ai_settings)

        if self.config_data.write_reports and summary.succeeded:
            self._write_reports(summary)

        self.after(0, self._on_finished, summary)

    def _run_ai(self, summary: BatchSummary, ai_settings) -> None:
        targets = [r.source for r in summary.succeeded]
        if not targets:
            return
        self.after(0, lambda: self.status_label.configure(
            text=f'Generating alt text for {len(targets)} image(s)...'))
        logger.info(f'Generating alt text with {ai_settings.model}...')
        try:
            def on_ai_progress(done, total, meta):
                # self.after passes args positionally, so hand it a closure
                # rather than a dict that would bind to `require_redraw`.
                self.after(0, lambda: self.status_label.configure(
                    text=f'Alt text {done} of {total}...'))

            metadata = ai_module.describe_batch(
                targets, ai_settings, cancel=self.cancel_event,
                progress=on_ai_progress)
        except ai_module.AIUnavailable as exc:
            logger.warning(f'Alt text skipped: {exc}')
            return
        except Exception as exc:
            logger.error(f'Alt text failed: {type(exc).__name__}: {exc}')
            return

        ai_module.apply_to_results(summary.results, metadata)
        failures = [m for m in metadata.values() if m.error]
        tokens = sum(m.input_tokens + m.output_tokens for m in metadata.values())
        logger.info(f'Alt text: {len(metadata) - len(failures)}/{len(targets)} '
                    f'described ({tokens:,} tokens)')
        for meta in failures:
            logger.warning(f'  {os.path.basename(meta.source)}: {meta.error}')

    def _write_reports(self, summary: BatchSummary) -> None:
        try:
            report.write_html_report(summary, os.path.join(summary.output_root,
                                                           'report.html'))
            report.write_json(summary, os.path.join(summary.output_root, 'report.json'))
            report.write_markup(summary,
                                os.path.join(summary.output_root, 'snippets.html'),
                                base_url=self.base_url_var.get().strip(),
                                sizes=self.config_data.sizes_attr)
            logger.info(f'Reports written to {summary.output_root}')
        except Exception as exc:
            logger.error(f'Could not write reports: {type(exc).__name__}: {exc}')

    def _on_progress(self, progress: BatchProgress) -> None:
        self.progressbar.set(progress.fraction)
        eta = progress.eta_seconds
        eta_text = f' | about {report.format_duration(eta)} left' if eta else ''
        self.status_label.configure(
            text=f'{progress.done} of {progress.total}{eta_text}')
        if progress.result:
            self._append_result_row(progress.result)
            self._log_result(progress.result)

    def _log_result(self, result: FileResult) -> None:
        name = os.path.relpath(result.source, self.input_path_var.get())
        if not result.ok:
            logger.error(f'{name}: {result.error}')
            return
        if result.skipped:
            logger.info(f'{name}: {result.note or "skipped"}')
            return
        primary = result.primary
        detail = ''
        if primary:
            detail = (f' | {primary.format_key} '
                      f'{"lossless" if primary.lossless else f"quality {primary.quality}"}')
            if primary.score is not None:
                detail += f', SSIM {primary.score:.4f}'
        if len(result.variants) > 1:
            detail += f' | {len(result.variants)} variants'
        logger.info(f'{name}: {report.format_bytes(result.original_size)} -> '
                    f'{report.format_bytes(result.new_size)} '
                    f'({result.saved_ratio * 100:.1f}% smaller){detail}')
        if result.decision:
            logger.info(f'    {result.decision}')
        if result.note:
            logger.info(f'    {result.note}')
        if result.vector_note:
            logger.info(f'    {result.vector_note}')

    def _append_result_row(self, result: FileResult) -> None:
        if self.results_placeholder is not None:
            self.results_placeholder.destroy()
            self.results_placeholder = None

        self.result_rows.append(result)
        row_index = len(self.result_rows)
        frame = ctk.CTkFrame(self.results_frame, fg_color='transparent')
        frame.grid(row=row_index, column=0, sticky='ew', pady=1)

        name = os.path.relpath(result.source, self.input_path_var.get())
        if not result.ok:
            ctk.CTkLabel(frame, text=name, width=320, anchor='w').grid(row=0, column=0, padx=4)
            ctk.CTkLabel(frame, text=result.error or 'failed', anchor='w',
                         text_color=('#b23c17', '#ef8b63')).grid(row=0, column=1,
                                                                 columnspan=5, padx=4,
                                                                 sticky='w')
        else:
            primary = result.primary
            encoded = ''
            if primary:
                encoded = (f'{primary.format_key} '
                           f'{"lossless" if primary.lossless else f"q{primary.quality}"}')
            if result.vector:
                encoded += ' + svg'
            pct = result.saved_ratio * 100
            color = ('#1a7f4b', '#4ec98a') if pct >= 0 else ('#b23c17', '#ef8b63')
            if result.skipped:
                saved_text = 'skipped'
            elif pct >= 0:
                saved_text = f'{pct:.1f}%'
            else:
                saved_text = f'+{-pct:.1f}% bigger'
            cells = (
                (name, 320, 'w', None),
                (result.stats.kind_label if result.stats else '', 130, 'w', None),
                (encoded, 130, 'w', None),
                (report.format_bytes(result.original_size), 90, 'e', None),
                (report.format_bytes(result.new_size), 90, 'e', None),
                (saved_text, 90, 'e', color),
            )
            for column, (text, width, anchor, text_color) in enumerate(cells):
                kwargs = {'text_color': text_color} if text_color else {}
                ctk.CTkLabel(frame, text=text, width=width, anchor=anchor,
                             font=ctk.CTkFont(size=12), **kwargs
                             ).grid(row=0, column=column, padx=4, sticky=anchor)

        running_before = sum(r.original_size for r in self.result_rows
                             if r.ok and not r.skipped)
        running_after = sum(r.new_size for r in self.result_rows
                            if r.ok and not r.skipped)
        if running_before:
            saved = running_before - running_after
            self.savings_label.configure(
                text=f'{report.format_bytes(saved)} saved '
                     f'({saved / running_before * 100:.0f}%)')

    def _clear_results(self, message: str = '') -> None:
        for child in self.results_frame.winfo_children():
            child.destroy()
        self.result_rows = []
        # A tab that has never been shown does not paint until something is
        # added to it, so an empty Results tab looks broken at the start of a
        # run. Always keep a placeholder until the first result lands.
        self.results_placeholder = ctk.CTkLabel(
            self.results_frame, text=message or 'Run an optimisation to see '
                                                'per-file results.',
            text_color=('gray40', 'gray65'))
        self.results_placeholder.grid(row=0, column=0, pady=20)
        self.log_textbox.configure(state='normal')
        self.log_textbox.delete('1.0', 'end')
        self.log_textbox.configure(state='disabled')

    def _on_finished(self, summary: Optional[BatchSummary]) -> None:
        self.summary = summary
        self.start_button.configure(state='normal', text='Start optimisation')
        self.cancel_button.configure(state='disabled')

        if summary is None:
            self.status_label.configure(text='Failed - see the Log tab')
            return

        self.open_button.configure(state='normal')
        if self.config_data.write_reports and summary.succeeded:
            self.report_button.configure(state='normal')

        if summary.cancelled:
            self.status_label.configure(
                text=f'Cancelled after {len(summary.succeeded)} image(s)')
        else:
            self.progressbar.set(1)
            parts = [f'Done: {len(summary.succeeded)} optimised']
            if summary.skipped:
                parts.append(f'{len(summary.skipped)} up to date')
            if summary.failed:
                parts.append(f'{len(summary.failed)} failed')
            traced = sum(1 for r in summary.succeeded if r.vector)
            if traced:
                parts.append(f'{traced} also as SVG')
            parts.append(f'in {report.format_duration(summary.elapsed)}')
            self.status_label.configure(text=' | '.join(parts))

        logger.info('-' * 70)
        logger.info(f'{report.format_bytes(summary.original_bytes)} -> '
                    f'{report.format_bytes(summary.new_bytes)} '
                    f'({summary.saved_ratio * 100:.1f}% smaller, '
                    f'{report.format_bytes(summary.saved_bytes)} saved) '
                    f'in {report.format_duration(summary.elapsed)}')
        for result in summary.failed:
            logger.error(f'{os.path.basename(result.source)}: {result.error}')

    def _cancel(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.cancel_button.configure(state='disabled', text='Cancelling...')
            self.status_label.configure(text='Finishing the images already in flight...')

    def _open_output(self) -> None:
        folder = self.output_path_var.get()
        if os.path.isdir(folder):
            open_in_file_manager(folder)

    def _open_report(self) -> None:
        path = os.path.join(self.output_path_var.get(), 'report.html')
        if os.path.exists(path):
            webbrowser.open(f'file://{os.path.abspath(path)}')

    # ------------------------------------------------------------------
    # Logging + shutdown
    # ------------------------------------------------------------------
    def _drain_log_queue(self) -> None:
        try:
            while True:
                record = log_queue.get_nowait()
                message = logging.Formatter(
                    '%(asctime)s  %(message)s', datefmt='%H:%M:%S').format(record)
                self.log_textbox.configure(state='normal')
                self.log_textbox.insert('end', message + '\n')
                self.log_textbox.configure(state='disabled')
                self.log_textbox.see('end')
        except queue.Empty:
            pass
        finally:
            self.after(120, self._drain_log_queue)

    def _on_close(self) -> None:
        if self.cancel_event and self.worker and self.worker.is_alive():
            if not messagebox.askyesno('Still working',
                                       'An optimisation is still running. Quit anyway?',
                                       parent=self):
                return
            self.cancel_event.set()
        try:
            self._collect_settings()
            self._collect_ai_settings()
            self.config_data.last_input = self.input_path_var.get()
            self.config_data.last_output = self.output_path_var.get()
            self.config_data.workers = int(self.workers_var.get())
            self.config_data.write_reports = bool(self.reports_var.get())
            self.config_data.base_url = self.base_url_var.get().strip()
            self.config_data.appearance = ctk.get_appearance_mode()
            config_module.save(self.config_data)
        except Exception:
            pass       # never block quitting on a config write
        self.destroy()


def main() -> None:
    if not logger.handlers:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(console)
    app = ImageOptimizerApp()
    app.mainloop()


if __name__ == '__main__':
    main()
