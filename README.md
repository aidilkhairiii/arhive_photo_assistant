# Archive Photo Assistant

A pipeline that assesses the condition of old/archival photographs and recommends
restoration priority. The work is split into **4 modules**, one per team member:

| Module | File | Owner | Status |
|---|---|---|---|
| 1. Image Upload & Preprocessing | `preprocessing.py` | (me) | ✅ Done |
| 2. Image Quality Assessment | `quality_assestment.py` | teammate | ✅ Done |
| 3. Fading Analysis & Object Tagging | `fading_analysis.py` | teammate | ✅ Done |
| 4. Report Generation & Dashboard | `report_generator.py` | teammate | ✅ Done |
| Orchestrator (runs all modules) | `main.py` | shared | ✅ Done |

The modules run as a **pipeline**: Module 1 prepares each image and writes a
metadata JSON; Modules 2–4 read that JSON and add their own results on top.

```
data/raw/img.jpg
      │
      ▼
[ Module 1: preprocessing ] ── writes ──▶ data/processed/*.png  +  outputs/<id>.json
      │                                                                │
      ▼                                                                │ (read this)
[ Module 2: quality ] ── blur / brightness / contrast ────────────────┤
[ Module 3: fading  ] ── histogram / fading / tags (LLM) ─────────────┤
[ Module 4: report  ] ── condition score / priority / dashboard ◀─────┘
```

---

## What Module 1 does (my part)

`preprocessing.py` takes raw images and turns them into clean, standardized inputs
for the rest of the team. For **every** image it performs:

1. **Upload / load** — read the image from disk (`data/raw/`).
2. **Resize** — to **512×512**. By default it preserves the aspect ratio and
   pads (letterbox) so the photo is **not distorted** — distortion would corrupt
   Module 2's blur/contrast measurements. (`--stretch` forces a plain stretch.)
3. **Grayscale conversion**.
4. **Noise reduction** — Non-Local Means denoising (handles scanned-photo grain).
5. **Normalization** — pixel values scaled to float `[0, 1]` (saved as `.npy`
   only when you pass `--save-normalized`).
6. **Metadata** — writes one JSON file per image: the **contract** the other
   modules read.

### Key design decisions (please don't break these)
- **The original is never deleted.** Its path stays in the JSON so any module can
  re-read full-resolution color if it needs to.
- **Two processed versions are produced**, because different modules need
  different things:
  - `<id>_color.png` (512×512 color, denoised) → **Module 3** (color histogram, fading)
  - `<id>_gray.png` (512×512 grayscale, denoised) → **Module 2** (blur, contrast)
- 512×512 is fixed in `TARGET_SIZE` at the top of `preprocessing.py`. If you
  change it, tell the team — Module 2's thresholds may depend on it.

---

## Folder structure

```
.
├── preprocessing.py        # Module 1 (done)
├── quality_assestment.py   # Module 2 (done)
├── fading_analysis.py      # Module 3 (empty)
├── report_generator.py     # Module 4 (empty)
├── main.py                 # orchestrator (empty)
├── requirements.txt
├── data/
│   ├── raw/                # <-- put dataset images here
│   └── processed/          # Module 1 writes processed images here
└── outputs/                # Module 1 writes one <id>.json per image here
```

---

## How to run Module 1

```bash
pip install -r requirements.txt

# process every image in data/raw
python preprocessing.py --input data/raw --output data/processed --meta outputs

# or a single image
python preprocessing.py --input data/raw/IMAGENAME.jpg --output data/processed --meta outputs
```

Useful flags:
- `--stretch` — stretch to 512×512 instead of aspect-preserving letterbox.
- `--save-normalized` — also save the normalized float array as `<id>_norm.npy`.

---

## The output contract (read this if you're doing Module 2, 3, or 4)

For each image, Module 1 writes `outputs/<image_id>.json`:

```json
{
  "image_id": "img_001",
  "status": "ok",
  "original_path": "data/raw/img_001.jpg",
  "processed_color_path": "data/processed/img_001_color.png",
  "processed_gray_path":  "data/processed/img_001_gray.png",
  "normalized_npy_path":  null,
  "original_size": [1200, 800],
  "processed_size": [512, 512],
  "preprocessing": {
    "resized": true, "keep_aspect": true, "grayscale": true,
    "denoised": true, "normalized": true
  },
  "analysis": {
    "content_box": [65, 0, 381, 512],
    "colorfulness": 22.79,
    "is_grayscale": false,
    "noise_sigma": 4.87
  }
}
```

- `status` is `"ok"` or `"error"` (with an `"error"` message). **Skip images
  where `status != "ok"`.**
- Use `processed_gray_path` / `processed_color_path` to load the prepared image,
  or `original_path` if you need the untouched original.

### ⚠️ The `analysis` block — important for Modules 2 & 3
The dataset is real archival photos, so it has three realities you must handle.
Module 1 measures them for you:

- **`content_box` = `[x, y, w, h]`** — because images are letterboxed to 512×512,
  the frame has **black padding bars**. This box is the region of REAL pixels.
  **Crop to it before computing any statistic**, or the black bars will skew your
  brightness / contrast / histogram:
  ```python
  x, y, w, h = meta["analysis"]["content_box"]
  region = img[y:y+h, x:x+w]      # measure on this, not the full 512x512
  ```
- **`is_grayscale` / `colorfulness`** — ~56% of this dataset is black & white
  (`colorfulness ≈ 0`). **Module 3:** if `is_grayscale` is true, fading-by-color
  won't work — use a tonal/sepia method instead.
- **`noise_sigma`** — noise level measured **before** Module 1 denoised the image
  (Module 1 removes the noise, so this is your only record of it). Higher = noisier.
  Clean images here are ~1–5; heavily noisy ones are ~20+.

---

## How to continue my work (Modules 2 → 4)

Each module should **read the Module 1 JSON, add its results, and write them back
(or hand them to `main.py`).** Suggested pattern so everything stays compatible:

### Module 2 — `quality_assestment.py`
Module 2 is now implemented. It is a pure image-processing module, so it does
not use any LLM API.

Input:
- The preferred input is the metadata dictionary or JSON file produced by
  `preprocessing.py`.
- It uses `processed_gray_path` to read the preprocessed grayscale image.
- It uses `analysis.content_box` to crop away the black letterbox padding before
  calculating any score.

Minimum metadata needed:
```json
{
  "status": "ok",
  "processed_gray_path": "data/processed/img_001_gray.png",
  "analysis": {
    "content_box": [65, 0, 381, 512]
  }
}
```

Processing flow:
1. Check that preprocessing status is `"ok"`.
2. Load the image from `processed_gray_path`.
3. Crop to `analysis.content_box`.
4. Calculate blur, brightness and contrast.
5. Return scores scaled from 0 to 100.

Output:
```json
{ "blur": 75, "brightness": 68, "contrast": 52 }
```

Meaning of each score:
- `blur` = sharpness score using variance of the Laplacian. Higher means sharper
  and less blurry.
- `brightness` = average grayscale pixel value. Higher means brighter.
- `contrast` = grayscale standard deviation. Higher means stronger contrast.

Main functions:
```python
calculate_blur_score(image)
calculate_brightness(image)
calculate_contrast(image)
assess_quality(metadata)
assess_image_quality(metadata)  # alias for assess_quality()
```

Example use inside Python:
```python
from quality_assestment import assess_quality

quality = assess_quality(meta_from_preprocessing)
print(quality)
```

Example result added back into the metadata:
```json
{
  "image_id": "img_001",
  "status": "ok",
  "quality": {
    "blur": 75,
    "brightness": 68,
    "contrast": 52
  }
}
```

Run Module 2 on all metadata JSON files:
```bash
python quality_assestment.py --meta outputs
```

Run Module 2 without writing results back to JSON:
```bash
python quality_assestment.py --meta outputs --no-write
```

### Module 3 — `fading_analysis.py`
Read `processed_color_path` (color is required for fading), **crop to
`content_box`**, then compute and return:
```json
{ "fading": "Moderate", "tags": ["building", "people", "street"] }
```
- Fading: analyze the color histogram / saturation; classify None / Mild / Moderate / Severe.
  **Check `is_grayscale` first** — if true, there's no colour to fade, so use a
  tonal/sepia-based judgement instead of saturation.
- Tags: currently simple OpenCV heuristics (edge density + Haar face detection).
  Swapping in an LLM vision API is a future upgrade.

### Module 4 — `report_generator.py`
Module 4 is the final stage. It reads each metadata JSON (with Module 1–3 results
already in it), turns the raw numbers into a **decision**, and produces the
**visual outputs** the assignment requires.

**Input:** the `outputs/<id>.json` files — needs `quality` (Module 2) and
`fading_analysis` (Module 3) already present.

**Condition score** — a single deterministic 0–100 score from a weighted blend of
all signals. Weights live in `WEIGHTS` at the top of the file (change one line to
retune):

| Signal | Source | Weight |
|---|---|---|
| Fading | `fading_analysis.fading` | 0.35 |
| Cleanliness (noise) | `analysis.noise_sigma` | 0.20 |
| Contrast | `quality.contrast` | 0.20 |
| Brightness (deviation from mid-tone) | `quality.brightness` | 0.15 |
| Sharpness | `quality.blur` | 0.10 |

Sharpness is weighted low on purpose: Module 2 measures it on the *denoised*
image, so it understates real detail.

- **Condition label:** Excellent ≥80 · Good 65–79 · Fair 50–64 · Poor 35–49 · Critical <35
- **Restoration priority:** High <50 · Medium 50–69 · Low ≥70

**Narrative (hybrid):** the score / priority / issues are pure Python; the
plain-English verdict is written by an **LLM via OpenRouter** if a key is set,
otherwise a rule-based template is used. The LLM call uses only the standard
library (no extra pip install) and is wrapped so the pipeline never hard-fails.
To enable it, create a `.env` in the project root (`.env` is git-ignored):
```
OPENROUTER_API_KEY=sk-or-...
LLM_MODEL=deepseek/deepseek-v4-flash
```

**Output** — a `report` block written back into each JSON, plus two PNGs in
`outputs/reports/`:
- `<id>_card.png` — per-photo report card (photo + quality bars + fading badge +
  condition, priority, recommendation). The card shows a trimmed narrative; the
  full text stays in the JSON.
- `dashboard.png` — whole-archive ranking: High/Medium/Low counts + a
  "top N to restore first" table.

```json
"report": {
  "condition_score": 47,
  "condition_label": "Poor",
  "priority": "High",
  "issues": ["Severe fading", "Blur / soft detail"],
  "subscores": { "fading": 10, "cleanliness": 89, "contrast": 47, "brightness": 100, "sharpness": 11 },
  "narrative": "The scan is in poor condition ...",
  "narrative_source": "openrouter:deepseek/deepseek-v4-flash",
  "card_path": "outputs/reports/<id>_card.png"
}
```

Run Module 4 on all metadata:
```bash
python report_generator.py --meta outputs             # cards + dashboard
python report_generator.py --meta outputs --no-llm    # force template (no API)
python report_generator.py --meta outputs --no-render # scores only, no PNGs
```

**For the Telegram bot** — Module 4 is decoupled, so the bot just calls two
functions:
```python
from report_generator import generate_report, generate_dashboard

# on a photo upload (after running Modules 1–3 to build `meta`):
report = generate_report(meta)        # -> {"card_path", "narrative", "priority", ...}
bot.send_photo(report["card_path"]); bot.send_message(report["narrative"])

# on /rank:
bot.send_photo(generate_dashboard(all_metas))   # ranked dashboard image
```

### `main.py` — orchestrator
Runs the whole pipeline (Modules 1 → 4) over a folder of images and writes results
back into `outputs/<id>.json` as it goes, rendering the report cards + dashboard at
the end:
```bash
python main.py                         # full pipeline on data/raw
python main.py --skip-preprocessing    # re-run Modules 2–4 on existing outputs/*.json
```

### Note on the LLM
Module 4 uses an **LLM (via OpenRouter)** to write the human-readable restoration
narrative; everything else (scores, priority, fading, quality) is pure image
processing with no LLM. Module 3's object tags are currently simple OpenCV
heuristics (edge density + face detection), not an LLM — that's the obvious place
to upgrade to a vision model later. The Telegram bot / Hermes skill that fronts the
pipeline is built by the integration owner.

---

## Dependencies
```
opencv-python
numpy
matplotlib        # Module 4: report cards + dashboard
```
The Module 4 LLM narrative calls **OpenRouter** using only Python's standard
library (`urllib`) — no extra SDK to install. Set `OPENROUTER_API_KEY` and
`LLM_MODEL` in a `.env` file to enable it; without them Module 4 falls back to a
rule-based template and runs fully offline.
