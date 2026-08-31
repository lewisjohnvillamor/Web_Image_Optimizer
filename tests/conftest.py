import os
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_photo(width=640, height=480, seed=1):
    """Smooth gradients plus shapes and light grain - behaves like a photo."""
    yy, xx = np.mgrid[0:height, 0:width]
    base = np.stack([
        120 + 80 * np.sin(xx / 90.0),
        100 + 70 * np.cos(yy / 70.0),
        140 + 60 * np.sin((xx + yy) / 110.0),
    ], axis=-1)
    # Fine texture on top of the gradients: without it the image reads as a
    # flat illustration to the analyser, which is not what we want to test.
    texture = (18 * np.sin(xx / 3.1 + yy / 5.3) + 12 * np.cos(xx / 1.7 - yy / 2.3))
    base = base + texture[..., None]
    grain = np.random.default_rng(seed).normal(0, 2, base.shape)
    img = Image.fromarray(np.clip(base + grain, 0, 255).astype('uint8'))
    draw = ImageDraw.Draw(img)
    draw.ellipse([60, 50, 300, 260], fill=(220, 90, 60))
    return img.filter(ImageFilter.GaussianBlur(0.6))


def make_flat_graphic(width=400, height=300, alpha=True):
    img = Image.new('RGBA' if alpha else 'RGB', (width, height),
                    (0, 0, 0, 0) if alpha else (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([20, 20, width - 20, height - 20], 24, fill=(30, 120, 220, 255))
    draw.ellipse([60, 60, 180, 180], fill=(255, 220, 40, 255))
    return img


def make_text_screenshot(width=600, height=400):
    img = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(img)
    for y in range(10, height - 14, 16):
        draw.text((10, y), 'Revenue 12,345.67  status OK  region EU-west', fill=(20, 20, 20))
    return img


@pytest.fixture
def photo():
    return make_photo()


@pytest.fixture
def flat_graphic():
    return make_flat_graphic()


@pytest.fixture
def text_screenshot():
    return make_text_screenshot()


@pytest.fixture
def sample_tree(tmp_path):
    """A small source folder covering the interesting content types."""
    src = tmp_path / 'src'
    (src / 'nested').mkdir(parents=True)
    make_photo().save(src / 'photo.jpg', quality=92)
    make_flat_graphic().save(src / 'logo.png')
    make_text_screenshot().save(src / 'screenshot.png')
    make_photo(320, 240, seed=7).save(src / 'nested' / 'inner.png')
    return src
