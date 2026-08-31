"""Presets and persisted preferences.

Nobody wants to re-dial six settings every launch, and "what settings did we
use for the product shots?" is a question teams actually ask. Presets answer
both; user presets live next to the app config as plain JSON so they can be
committed to a repo and shared.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Dict, Optional

from .ai import AISettings
from .engine import MODE_FIXED, MODE_LOSSLESS, MODE_SMART, OptimizeSettings

APP_DIR_NAME = 'web-image-optimizer'
CONFIG_FILENAME = 'config.json'


def config_dir() -> str:
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, APP_DIR_NAME)


def config_path() -> str:
    return os.path.join(config_dir(), CONFIG_FILENAME)


# --------------------------------------------------------------------------
# Built-in presets
# --------------------------------------------------------------------------

BUILTIN_PRESETS: Dict[str, Dict[str, object]] = {
    'Web (recommended)': {
        'description': 'Smart per-image quality, AVIF with WebP fallback. '
                       'The right default for most sites.',
        'settings': dict(output_format='auto', mode=MODE_SMART, target='balanced',
                         auto_settings=True, effort=4, strip_metadata=True),
    },
    'Hero / full-bleed': {
        'description': 'High visual target and responsive widths for large '
                       'above-the-fold imagery.',
        'settings': dict(output_format='auto', mode=MODE_SMART, target='high',
                         auto_settings=True, effort=5, max_width=2400,
                         widths=(2400, 1600, 1200, 800)),
    },
    'Thumbnails': {
        'description': 'Small, aggressive, capped at 400px.',
        'settings': dict(output_format='webp', mode=MODE_SMART, target='small',
                         auto_settings=True, effort=4, max_width=400),
    },
    'E-commerce product shots': {
        'description': 'Detail-preserving quality with the responsive set a '
                       'product gallery needs.',
        'settings': dict(output_format='auto', mode=MODE_SMART, target='high',
                         auto_settings=True, effort=5, max_width=2000,
                         widths=(2000, 1200, 800, 400)),
    },
    'Maximum compression': {
        'description': 'Smallest files that still clear a visible-quality floor.',
        'settings': dict(output_format='auto', mode=MODE_SMART, target='small',
                         auto_settings=True, effort=6),
    },
    'Lossless / archival': {
        'description': 'Pixel-identical output. Metadata and colour profiles kept.',
        'settings': dict(output_format='webp', mode=MODE_LOSSLESS, effort=6,
                         auto_settings=False, strip_metadata=False,
                         convert_to_srgb=False),
    },
    'Legacy JPEG only': {
        'description': 'For pipelines that cannot serve WebP or AVIF yet.',
        'settings': dict(output_format='jpeg', mode=MODE_SMART, target='balanced',
                         auto_settings=True, effort=4),
    },
}


@dataclass
class AppConfig:
    settings: OptimizeSettings
    ai: AISettings
    last_input: str = ''
    last_output: str = ''
    appearance: str = 'System'
    workers: int = 0                      # 0 => auto
    write_reports: bool = True
    base_url: str = ''
    sizes_attr: str = '100vw'
    user_presets: Dict[str, Dict[str, object]] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.user_presets is None:
            self.user_presets = {}

    def to_dict(self) -> Dict[str, object]:
        return {
            'version': 2,
            'settings': self.settings.to_dict(),
            'ai': self.ai.to_dict(),
            'last_input': self.last_input,
            'last_output': self.last_output,
            'appearance': self.appearance,
            'workers': self.workers,
            'write_reports': self.write_reports,
            'base_url': self.base_url,
            'sizes_attr': self.sizes_attr,
            'user_presets': self.user_presets,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> 'AppConfig':
        data = data or {}
        return cls(
            settings=OptimizeSettings.from_dict(data.get('settings') or {}),
            ai=AISettings.from_dict(data.get('ai') or {}),
            last_input=str(data.get('last_input') or ''),
            last_output=str(data.get('last_output') or ''),
            appearance=str(data.get('appearance') or 'System'),
            workers=int(data.get('workers') or 0),
            write_reports=bool(data.get('write_reports', True)),
            base_url=str(data.get('base_url') or ''),
            sizes_attr=str(data.get('sizes_attr') or '100vw'),
            user_presets=dict(data.get('user_presets') or {}),
        )

    # -- presets ---------------------------------------------------------
    def all_presets(self) -> Dict[str, Dict[str, object]]:
        merged = dict(BUILTIN_PRESETS)
        merged.update(self.user_presets)
        return merged

    def apply_preset(self, name: str) -> bool:
        preset = self.all_presets().get(name)
        if not preset:
            return False
        # Start from defaults so a preset never inherits stray state.
        base = OptimizeSettings()
        for key, value in (preset.get('settings') or {}).items():
            if key in OptimizeSettings.__dataclass_fields__:
                setattr(base, key, tuple(value) if key == 'widths' else value)
        base.recursive = self.settings.recursive
        base.skip_existing = self.settings.skip_existing
        self.settings = base
        return True

    def save_preset(self, name: str, description: str = '') -> None:
        self.user_presets[name] = {
            'description': description or 'Saved from the current settings.',
            'settings': self.settings.to_dict(),
        }


def load(path: Optional[str] = None) -> AppConfig:
    """Load config, falling back to defaults on anything unreadable."""
    path = path or config_path()
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return AppConfig.from_dict(json.load(fh))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return AppConfig(settings=OptimizeSettings(), ai=AISettings())


def save(config: AppConfig, path: Optional[str] = None) -> str:
    """Write config atomically so a crash mid-write cannot corrupt it."""
    path = path or config_path()
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as fh:
        json.dump(config.to_dict(), fh, indent=2)
    os.replace(temp, path)
    return path
