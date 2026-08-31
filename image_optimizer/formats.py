"""Output-format registry and capability detection.

Everything the rest of the package needs to know about *which* codecs are
usable on this machine lives here, so the GUI/CLI can degrade gracefully
instead of exploding with a Pillow ``KeyError`` half-way through a batch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

from PIL import Image, features

# Anything Pillow can open that makes sense as a web source image.
INPUT_EXTENSIONS: Tuple[str, ...] = (
    '.jpg', '.jpeg', '.jpe', '.png', '.webp', '.avif',
    '.tif', '.tiff', '.bmp', '.gif', '.ppm', '.jfif',
)


@dataclass(frozen=True)
class FormatSpec:
    """Static description of an output codec."""

    key: str                 # 'webp'
    label: str               # 'WebP'
    pil_format: str          # 'WEBP'
    extension: str           # '.webp'
    supports_alpha: bool
    supports_lossless: bool
    supports_animation: bool
    mime: str
    # Save kwargs that never change for this codec.
    base_options: Dict[str, object] = field(default_factory=dict)


FORMATS: Dict[str, FormatSpec] = {
    'webp': FormatSpec(
        key='webp', label='WebP', pil_format='WEBP', extension='.webp',
        supports_alpha=True, supports_lossless=True, supports_animation=True,
        mime='image/webp',
    ),
    'avif': FormatSpec(
        key='avif', label='AVIF', pil_format='AVIF', extension='.avif',
        supports_alpha=True, supports_lossless=True, supports_animation=True,
        mime='image/avif',
    ),
    'jpeg': FormatSpec(
        key='jpeg', label='JPEG', pil_format='JPEG', extension='.jpg',
        supports_alpha=False, supports_lossless=False, supports_animation=False,
        mime='image/jpeg',
        base_options={'optimize': True, 'progressive': True, 'subsampling': 'keep'},
    ),
    'png': FormatSpec(
        key='png', label='PNG', pil_format='PNG', extension='.png',
        supports_alpha=True, supports_lossless=True, supports_animation=True,
        mime='image/png',
        base_options={'optimize': True},
    ),
}

# Order matters: 'auto' tries these and keeps the smallest result.
AUTO_CANDIDATES: Tuple[str, ...] = ('avif', 'webp')


def _avif_available() -> bool:
    try:
        if features.check('avif'):
            return True
    except Exception:
        pass
    try:  # Pillow < 11 needs the plugin package.
        import pillow_avif  # noqa: F401
        return '.avif' in Image.registered_extensions()
    except Exception:
        return False


def available_formats() -> Tuple[str, ...]:
    """Output format keys this installation can actually write."""
    keys = ['webp'] if features.check('webp') else []
    if _avif_available():
        keys.append('avif')
    keys.extend(['jpeg', 'png'])
    return tuple(keys)


def auto_candidates() -> Tuple[str, ...]:
    usable = available_formats()
    return tuple(k for k in AUTO_CANDIDATES if k in usable) or ('webp',)


def resolve(format_key: str) -> FormatSpec:
    try:
        return FORMATS[format_key.lower()]
    except KeyError:
        raise ValueError(
            f"Unknown output format {format_key!r}. "
            f"Known: {', '.join(sorted(FORMATS))}"
        ) from None


def missing_format_hint(format_key: str) -> str:
    """Human-readable install hint for a codec we can't write."""
    if format_key == 'avif':
        return ("AVIF support is unavailable. Install it with "
                "`pip install pillow-avif-plugin` (or upgrade to Pillow >= 11.3).")
    if format_key == 'webp':
        return ("WebP support is unavailable in this Pillow build. "
                "Reinstall Pillow from a wheel: `pip install --force-reinstall Pillow`.")
    return f"{format_key} output is unavailable in this Pillow build."
