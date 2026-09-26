"""Lossless preprocessing: remove bytes that carry no visible information.

These are optimisations in the real sense - they change the encoded bytes
without changing a single pixel anyone can see, so they need no quality gate
and cost nothing when they do not apply. Each one was adopted because it was
measured, and the measurement is quoted where it matters.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
from PIL import Image

# Below this many distinct RGB values under the transparent region there is
# nothing to gain - the encoder already sees a flat field.
WASTE_THRESHOLD = 2


def transparent_rgb_variety(img: Image.Image) -> int:
    """How many distinct RGB values hide under fully transparent pixels.

    An exported PNG often keeps whatever colour the artwork had before it was
    erased. Those pixels are invisible, but a lossless encoder still has to
    encode them, and noise there is expensive.
    """
    if img.mode not in ('RGBA', 'LA', 'PA'):
        return 0
    arr = np.asarray(img.convert('RGBA'))
    clear = arr[..., 3] == 0
    if not clear.any():
        return 0
    hidden = arr[..., :3][clear]
    return int(np.unique(hidden.reshape(-1, 3), axis=0).shape[0])


def clean_transparent_rgb(img: Image.Image) -> Tuple[Image.Image, bool]:
    """Flatten the colour hiding under fully transparent pixels.

    Invisible by definition - alpha is zero - so this is lossless to the eye
    while turning an expensive noise field into a constant the encoder packs
    to almost nothing. Measured on a logo whose transparent region carried
    random RGB, as design-tool exports often do: lossless WebP 522,072 ->
    4,306 bytes (-99.2%), PNG 605,943 -> 11,810 (-98.1%).

    Returns the image and whether anything changed.
    """
    if transparent_rgb_variety(img) < WASTE_THRESHOLD:
        return img, False
    arr = np.asarray(img.convert('RGBA')).copy()
    clear = arr[..., 3] == 0
    # Zero is as good as any constant and keeps the field uniform; what the
    # encoder wants is one value, not a particular one.
    arr[..., :3][clear] = 0
    return Image.fromarray(arr, 'RGBA'), True


def is_greyscale(img: Image.Image) -> bool:
    """True when a colour-mode image carries no colour at all."""
    if img.mode in ('L', 'LA', '1'):
        return True
    if img.mode not in ('RGB', 'RGBA'):
        return False
    arr = np.asarray(img.convert('RGB'))
    if arr.ndim != 3:
        return False
    return bool((arr.max(axis=2) == arr.min(axis=2)).all())


def as_greyscale(img: Image.Image) -> Image.Image:
    """Drop the two redundant channels, keeping alpha if there is any."""
    if img.mode in ('RGBA', 'LA', 'PA') or 'transparency' in img.info:
        rgba = img.convert('RGBA')
        out = rgba.convert('L')
        out.putalpha(rgba.getchannel('A'))
        return out
    return img.convert('L')
