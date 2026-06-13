# Archive Photo Assistant

A pipeline that assesses the condition of old/archival photographs and recommends
restoration priority. The work is split into **4 modules**, one per team member:

| Module | File | Owner | Status |
|---|---|---|---|
| 1. Image Upload & Preprocessing | `preprocessing.py` | (me) | ✅ Done |
| 2. Image Quality Assessment | `quality_assestment.py` | teammate | ✅ Done |
| 3. Fading Analysis & Object Tagging | `fading_analysis.py` | teammate | ⬜ To do |
| 4. Report Generation & Dashboard | `report_generator.py` | teammate | ⬜ To do |
| Orchestrator (runs all modules) | `main.py` | shared | ⬜ To do |

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
├── quality_assestment.py   # Module 2 (empty)
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
Read `processed_gray_path`, **crop to `content_box`**, then compute and return:
```json
{ "blur": 75, "brightness": 68, "contrast": 52 }
```
Hints: blur = variance of the Laplacian (`cv2.Laplacian(...).var()`);
brightness = mean pixel value; contrast = std of pixel values. Scale to 0–100.
You can also surface `noise_sigma` from the metadata as a noise issue.

### Module 3 — `fading_analysis.py`
Read `processed_color_path` (color is required for fading), **crop to
`content_box`**, then compute and return:
```json
{ "fading": "Moderate", "tags": ["building", "people", "street"] }
```
- Fading: analyze the color histogram / saturation; classify None / Mild / Moderate / Severe.
  **Check `is_grayscale` first** — if true, there's no colour to fade, so use a
  tonal/sepia-based judgement instead of saturation.
- Tags / captioning: send the image to an LLM vision API (see below).

### Module 4 — `report_generator.py`
Combine Module 1 + 2 + 3 results per image, then:
- Calculate a condition score, rank restoration priority.
- Generate the report / dashboard, e.g.:
  ```
  Condition: Fair
  Issues: ✓ Moderate blur  ✓ Severe fading
  Priority: High Restoration Priority
  ```
- The human-readable narrative can be written by an LLM API.

### `main.py` — orchestrator
Loops over each image and chains the modules:
```python
m1 = preprocess_image(path, "data/processed")   # already implemented
m2 = assess_quality(m1)                          # Module 2
m3 = analyze_fading(m1)                           # Module 3
m4 = generate_report(m1, m2, m3)                 # Module 4
```

### Note on the LLM / orchestration requirement
Modules 3 (object tagging / captioning) and 4 (report writing) are the parts that
use an **LLM API**. Modules 1 and 2 are pure image processing — no LLM needed.
The "orchestration tool" mentioned for the project is what drives `main.py`'s loop
and holds the LLM calls. Confirm the exact tool/spelling with the lecturer before
wiring it up.

---

## Dependencies
```
opencv-python
numpy
```
Modules 3 and 4 will additionally need an LLM SDK (e.g. the Anthropic / OpenAI
client) once those are implemented.
