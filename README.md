# Web Image Optimizer

**Author:** Lewis John Villamor

A desktop app and CLI that makes images actually ready for the web: it picks
the right format and quality **per image** by measuring the result, generates
the responsive variants and the `<picture>` markup to serve them, and can write
the alt text most sites are missing.

![Screenshot Placeholder](screenshot.png)
*(Add a screenshot of your application here named `screenshot.png`)*

---

## Why this is different from "convert everything to WebP at quality 80"

A single quality slider is a guess applied to every image, and it is wrong in
both directions at once — wasteful on soft photos, visibly damaged on
screenshots and logos. This version measures instead of guessing:

1. **Analyse** the pixels to work out what the image *is* — photo,
   illustration, flat graphic, or a screenshot full of small text.
2. **Encode it several ways**, and score each result against the original
   using SSIM (structural similarity) on the luma channel.
3. **Keep the smallest file** that still clears the visual target you chose.

Run on this repository's own test fixtures, against what the previous version
of this tool produced:

| File | Source | Old: WebP q80 | New: smart | Chosen automatically | Smaller by |
|---|---|---|---|---|---|
| `logo.png` | 3.8 KiB | 3.2 KiB | **500 B** | WebP lossless | 85% |
| `photo.jpg` | 418.4 KiB | 179.1 KiB | **55.3 KiB** | AVIF q48 | 69% |
| `screenshot.png` | 113.9 KiB | 44.0 KiB | **1.6 KiB** | WebP lossless | 96% |
| **Total** | **536.1 KiB** | **226.2 KiB** | **57.4 KiB** | | **75%** |

58% total reduction before, 89% now — and the screenshot and logo are now
**pixel-identical** to the source rather than smeared by lossy compression.

The honest trade-off: measuring costs time. That run took 0.2s the old way and
3.7s the new way (the quality search runs at a cheap encoder setting and only
re-encodes the winner at full effort, which keeps it to roughly 3x). Use `--skip-existing` for incremental builds (a second run
over unchanged files takes milliseconds), turn the effort slider down, or pick
`Fixed quality` mode if you want the old speed back.

> These are synthetic fixtures, chosen to cover the three content types. Your
> own images will land on different numbers — the app tells you exactly what it
> did for every file.

---

## What it does

**Compression**
* **Per-image quality search** — binary-searches the quality scale for the
  cheapest setting that still meets your visual target (Maximum / High /
  Balanced / Smallest), measured with SSIM.
* **Content-aware settings** — flat graphics go lossless (usually smaller *and*
  perfect), screenshots get quality headroom so text doesn't ring, busy photos
  get compressed harder because the detail hides the artefacts.
* **AVIF, WebP, JPEG and PNG** output. `auto` encodes both AVIF and WebP and
  keeps whichever came out smaller.
* **Never larger than the original** — if nothing we encode beats the source
  file, the original is copied through untouched.

**Correctness fixes over the previous version**
* **EXIF orientation is applied**, so portrait phone photos stop coming out
  sideways.
* **Colour profiles are converted to sRGB** before being stripped, instead of
  being discarded and shifting every colour.
* **Alpha is composited onto a background** for formats that can't carry it,
  instead of failing with `cannot write mode RGBA as JPEG`.

**Delivery**
* **Responsive variants** — give it a list of widths and it writes
  `hero-1600w.avif`, `hero-800w.avif`, and so on.
* **`<picture>` markup generated for you**, with `srcset`, `sizes`, `width`,
  `height` (no layout shift), `loading="lazy"` and the alt text filled in.
* **Reports** — an HTML summary, plus JSON and CSV for pipelines.

**AI alt text (optional)**
* Generates WCAG-conscious alt text, a caption and an SEO filename suggestion
  for every image, using Claude. Decorative images correctly get *empty* alt
  text rather than noise for screen readers.
* Off by default. See [Privacy](#privacy) below.

**Workflow**
* **Batch, recursive, parallel** — folder structure preserved, all cores used.
* **Cancel button** that actually stops the run.
* **Incremental** — `--skip-existing` leaves up-to-date outputs alone.
* **Presets** for common jobs, plus your own saved ones.
* **CLI** for build pipelines and CI.
* Settings persist between launches.

---

## Installation

Python 3.9 or newer.

```bash
git clone <your-repository-url>
cd Web_Image_Optimizer

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

Or as a package, which also puts `image-optimizer` on your PATH:

```bash
pip install -e ".[all]"
```

Optional extras, all of which the app runs fine without:

| Extra | Gives you | Without it |
|---|---|---|
| `pillow-avif-plugin` | AVIF output | AVIF is hidden; WebP is used |
| `anthropic` | AI alt text | The AI tab explains it is unavailable |
| `customtkinter` | The desktop GUI | The CLI still works |

Check what your installation can write:

```bash
python -m image_optimizer --list-formats
```

---

## Using the app

```bash
python image_optimizer_gui.py
```

1. Pick a **source folder** and an **output folder**.
2. Pick a **preset** — "Web (recommended)" is the right answer for most sites.
3. Press **Start optimisation**.

The **Results** tab fills in as files complete, showing what each image was
classified as, what settings it got and how much it saved. The **Log** tab has
the detail. When it finishes, **View report** opens the HTML summary and
`snippets.html` in the output folder has the markup to paste into your pages.

### Tabs

* **Quality** — mode (Smart / Fixed / Lossless), visual target, encoder effort.
* **Output** — format, size caps, responsive widths, URL prefix, metadata,
  parallel workers.
* **AI alt text** — model, API key, and the site context that steers wording.

---

## Using the CLI

```bash
# The common case
python -m image_optimizer ./images ./dist

# Responsive set plus the markup to serve it
python -m image_optimizer ./images ./dist \
    --widths 1600,1200,800,400 --markup --base-url /assets/img

# A named preset, with reports
python -m image_optimizer ./images ./dist \
    --preset "E-commerce product shots" --html report.html --json report.json

# Incremental rebuild - unchanged files are stat'd, not re-encoded
python -m image_optimizer ./images ./dist --skip-existing

# With alt text
export ANTHROPIC_API_KEY=sk-ant-...
python -m image_optimizer ./images ./dist --alt-text \
    --ai-context "Independent bookshop in Manila" --markup
```

`--dry-run` lists what would be processed. `--list-presets` shows the presets.
The exit code is non-zero if any file failed, so it works as a CI gate.

---

## Using it as a library

```python
from image_optimizer import OptimizeSettings, run_batch

summary = run_batch(
    'src/images', 'dist/images',
    OptimizeSettings(output_format='auto', target='balanced',
                     widths=(1600, 800, 400)),
    progress=lambda p: print(f'{p.done}/{p.total}'),
)
print(f'{summary.saved_ratio:.0%} smaller')
```

`optimize_file()` handles a single image, `analyze()` returns the content
classification, and `ssim()` is available on its own if you want to score
encodes yourself.

---

## Presets

| Preset | For |
|---|---|
| Web (recommended) | Most sites. Smart quality, AVIF/WebP, sensible defaults. |
| Hero / full-bleed | Large above-the-fold imagery, high target, responsive set. |
| Thumbnails | Small and aggressive, capped at 400px. |
| E-commerce product shots | Detail-preserving, with a full gallery width set. |
| Maximum compression | Smallest files that still clear a visible-quality floor. |
| Lossless / archival | Pixel-identical. Metadata and colour profiles kept. |
| Legacy JPEG only | Pipelines that can't serve WebP or AVIF yet. |

Configure anything you like and **Save as...** to add your own. Presets live in
plain JSON (`~/.config/web-image-optimizer/config.json` on Linux,
`%APPDATA%` on Windows, `~/Library/Application Support` on macOS) so a team can
share one.

---

## Privacy

Everything except AI alt text runs entirely on your machine — no network calls.

Turning on AI alt text sends **a downscaled copy of each image** (max 768px, as
JPEG) to the Anthropic API. It is off by default, the API key is read from
`ANTHROPIC_API_KEY` unless you paste one in, and **the key is never written to
the config file**. Don't enable it for images you can't share with a third-party
service.

---

## Supported formats

**Input:** JPG/JPEG, PNG, WebP, AVIF, TIFF, BMP, GIF, PPM
**Output:** WebP, AVIF, JPEG, PNG

---

## Limitations

* Animated GIF/WebP is passed through at a fixed quality — the perceptual
  search runs on still images only.
* SSIM is a good, cheap proxy for visible difference, not a perfect model of
  human vision. On very noisy or grainy sources no quality setting reaches a
  high target, so the tool falls back to the content analyser's recommendation
  rather than burning bytes chasing a score it can't hit.
* Smart mode encodes each image several times; it is several times slower than
  fixed-quality conversion. That is the cost of the file sizes above.
* SEO filenames are *suggested* in the reports; files are not renamed, since
  renaming would break links you already have.
* CMYK and 16-bit-per-channel sources are converted to 8-bit sRGB.

---

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

119 tests cover the perceptual metric, the content classifier, the encoder
(including EXIF orientation, alpha handling and the never-larger guarantee),
batch execution and cancellation, the reports and markup, presets, the CLI, and
the AI layer against a fake client.

The GUI is a thin layer over the `image_optimizer` package — all decisions live
in the package, which is what the tests exercise.

```
image_optimizer/
    analysis.py   what kind of image is this
    quality.py    SSIM and the per-image quality search
    engine.py     decode, correct, encode, verify one image
    batch.py      discovery, parallelism, cancellation
    report.py     JSON/CSV/HTML reports and <picture> markup
    ai.py         optional Claude alt text
    config.py     presets and persisted preferences
    cli.py        command line
image_optimizer_gui.py    desktop app
```

---

## Contributing

Issues and pull requests are welcome. Please run `python -m pytest` before
opening a PR, and add a test for anything that changes encoder behaviour.

---

## License

MIT — see [LICENSE.md](LICENSE.md).

## Disclaimer

This software is provided "AS IS", without warranty of any kind, express or
implied. The author, Lewis John Villamor, is not liable for any claim, damages,
or other liability arising from, out of, or in connection with the software.
Keep backups of your originals, especially when the input and output folders
are the same.
