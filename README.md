# Historical Archive Management Assistant

A pipeline that helps users manage historical photo collections: assess
condition, catalogue items, estimate research value, prioritize restoration, and
recommend next actions. The work is split into **4 modules**, one per team
member:

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
├── fading_analysis.py      # Module 3 (done)
├── report_generator.py     # Module 4 (done)
├── main.py                 # orchestrator (done)
├── requirements.txt
├── skills/
│   └── archive-photo-triage/
│       ├── SKILL.md        # OpenClaw skill for Telegram photo uploads
│       └── scripts/
│           └── run_archive_photo_pipeline.py
├── data/
│   ├── raw/                # <-- put dataset images here
│   └── processed/          # Module 1 writes processed images here
└── outputs/                # metadata, reports, dashboards, catalogues
```

---

## Historical archive collection workflow

The upgraded assistant supports both one-photo reports and batch catalogue
generation for history students, museum studies students, archivists,
educators, and archive managers.

### One photo

```bash
python skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py --image path/to/photo.jpg
```

Output:
- report-card PNG
- condition score from 0 to 100
- condition category: Excellent, Good, Fair, or Poor
- fading/color degradation analysis
- archive-friendly tags
- research value: HIGH, MEDIUM, or LOW
- recommended action: Archive Only, Digitize Soon, Restoration Recommended, or
  Immediate Preservation Recommended

### Batch of up to 20 photos

```bash
python skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py \
  --images path/photo01.jpg path/photo02.jpg path/photo03.jpg \
  --no-llm
```

The batch runner continues if one image fails. It returns JSON shaped for
Telegram display, CSV export, and future database storage:

```json
{
  "batch_summary": {},
  "priority_ranking": [],
  "research_ranking": [],
  "categories": {},
  "metadata_table": [],
  "images": []
}
```

It also writes:
- `outputs/catalogues/<batch_id>_catalogue.json`
- `outputs/catalogues/<batch_id>_catalogue.csv`
- per-image metadata JSON files
- per-image report-card PNG files

The batch output is designed to answer archive workflow questions:
- Which images need restoration first?
- Which photos are People, Architecture, Documents, Objects, or Scenes?
- Which images have higher research value?
- What action should be taken next for each image?

Telegram album uploads are supported by `telegram_bot.py`: upload up to 20
images together and the bot waits briefly after the last received image before
processing. This helps avoid partial batches when Telegram delivers a large
selection in chunks.

The bot responds with exactly one final message containing the collection
summary, restoration priority ranking, category breakdown, top research-relevant
images, metadata summary, recommended actions, and catalogue note.

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
{ "fading": "Moderate", "tags": ["building", "outdoor", "historical_object"] }
```
- Fading: analyze the color histogram / saturation; classify None / Mild / Moderate / Severe.
  **Check `is_grayscale` first** — if true, there's no colour to fade, so use a
  tonal/sepia-based judgement instead of saturation.
- Tags: currently simple OpenCV heuristics (edge density, face detection, and
  document-like mark detection). Tags are archive-friendly labels such as
  `portrait`, `group_photo`, `building`, `document`, `handwritten`, `outdoor`,
  `indoor`, and `historical_object`. Swapping in a vision model is a future
  upgrade.

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

- **Condition label:** Excellent ≥80 · Good 65–79 · Fair 50–64 · Poor <50
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
telegram_reply = report["narrative"]  # return one final Telegram message

# on /rank:
dashboard_path = generate_dashboard(all_metas)  # generated file path for later use
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

---

## Handover — what's left & how to continue

**Status:** Modules 1–4 and the `main.py` orchestrator are done and tested on the
61-image dataset. Running `python main.py` produces, for every photo: a metadata
JSON (`outputs/<id>.json`), a styled report card (`outputs/reports/<id>_card.png`),
and the archive dashboard (`outputs/reports/dashboard.png`).

Two deliverables remain.

### A. Telegram / OpenClaw skill — *(done)*

The OpenClaw skill is in `skills/archive-photo-triage/SKILL.md`. It is designed
for these user flows:

Single-image flow:
1. User sends one photo in Telegram.
2. OpenClaw saves or locates the uploaded image file.
3. OpenClaw runs the local one-photo runner.
4. The user receives exactly one final condition report message.

Batch flow:
1. User uploads up to 20 photos in Telegram.
2. OpenClaw saves or locates every uploaded image file.
3. OpenClaw runs the batch runner.
4. The user receives a collection summary, restoration-priority ranking,
   category groups, research ranking, metadata summary, recommended actions,
   and catalogue note in exactly one final message.

One-photo command:
```bash
python skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py --image path/to/uploaded_photo.jpg --telegram-text
```

Batch command:
```bash
python skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py --images photo1.jpg photo2.jpg photo3.jpg --no-llm --telegram-text
```

For Telegram/OpenClaw, `--telegram-text` prints the single final message to
return. Without `--telegram-text`, the command prints JSON containing the exact
files/results:
```json
{
  "status": "ok",
  "metadata_path": "outputs/<id>.json",
  "card_path": "outputs/reports/<id>_card.png",
  "condition_score": 47,
  "condition_label": "Poor",
  "priority": "High",
  "research_value": "MEDIUM",
  "recommended_action": "Restoration Recommended",
  "issues": ["Severe fading", "Blur / soft detail"],
  "quality": { "blur": 75, "brightness": 68, "contrast": 52 },
  "fading_analysis": { "fading": "Moderate", "tags": ["building", "street"] },
  "narrative": "Plain-English restoration recommendation..."
}
```

Telegram should send only the final formatted text. Do not send the report-card
image, raw JSON, progress text, debug text, or catalogue files unless the user
asks for files in a separate request.

Archive ranking/dashboard command:
```bash
python skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py --rank --telegram-text
```

If successful, Telegram should send the final text only. The dashboard image is
still generated on disk for a separate file request.

### B. Telegram bot runner — *(done)*

The direct Telegram bot wrapper is `telegram_bot.py`.

Add your Telegram token to `.env`:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

Install dependencies:
```bash
venv/bin/pip install -r requirements.txt
```

Start the bot:
```bash
venv/bin/python telegram_bot.py
```

Then open your bot in Telegram and send it a photo. For one photo, the bot will:

1. download the uploaded image,
2. run `skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py`,
3. send one final condition summary and narrative.

For a Telegram album/batch of up to 20 images, the bot will:

1. wait briefly until Telegram finishes sending the album,
2. run the batch runner,
3. send one final message with the collection summary, restoration-priority
   ranking, category breakdown, research ranking, metadata summary,
   recommended actions, and catalogue note.

User-facing Telegram replies should stay friendly and avoid raw server paths.
The batch runner includes `telegram_display.summary_text`,
`telegram_display.ranking_text`, `telegram_display.categories_text`,
`telegram_display.research_text`, `telegram_display.metadata_text`, and
`telegram_display.actions_text` for this purpose.

Supported commands:
```text
/start  - explain what the bot does
/help   - show help
/rank   - update the restoration-priority dashboard after photos are processed
```

The bot wrapper itself can still use this direct Python pattern if needed:

```python
from preprocessing import preprocess_upload
from quality_assestment import assess_quality
from fading_analysis import update_metadata_with_fading
from report_generator import generate_report, generate_dashboard

# --- on a photo message ---
meta = preprocess_upload(file_bytes, filename, "data/processed")  # Module 1
meta["quality"] = assess_quality(meta)                            # Module 2
meta = update_metadata_with_fading(meta)                          # Module 3
report = generate_report(meta)                                    # Module 4
telegram_reply = report["narrative"]    # one final plain-English verdict

# --- on /rank (optional, whole archive) ---
import glob, json
metas = [json.load(open(f)) for f in glob.glob("outputs/*.json")]
dashboard_path = generate_dashboard(metas)
```

### C. Hermes/OpenClaw skill file — *(done)*

The skill file includes the required project mapping:

| Required section | What to write |
|---|---|
| Skill name | e.g. "Historical Archive Management Assistant" |
| Target user | History / museum archivists with large scanned-photo collections |
| Real-world problem | Which of hundreds of old photos should be restored first? |
| Input format | One photo, or up to 20 photos per batch (JPG/PNG/BMP/TIFF/WEBP); optional `/rank` over a set |
| CV / image-processing method | resize · grayscale · denoise (NLM) · Laplacian sharpness · brightness / contrast · colour-saturation & tonal fading · edge/face heuristics → weighted condition score |
| Step-by-step workflow | preprocess → quality → fading/tags → report card or batch catalogue → priority ranking |
| Output format | one final Telegram text report; generated report cards, catalogues, and dashboards stay on disk unless requested separately |
| Limitation handling | low-confidence tags; denoising hides true sharpness; no expert judgement |
| Ethical boundary | screening aid only, not a substitute for expert inspection; does not judge content or people |

### D. Cloud hosting — OpenClaw + Telegram

Deployment files are in `deployment/`.

Default production mode is:

```text
Telegram user -> OpenClaw Gateway -> archive-photo-triage skill -> local pipeline runner -> one final report message
```

Important: only one process can poll the same Telegram bot token. Use either
OpenClaw Gateway or `telegram_bot.py`, not both at the same time.

Before deployment, make sure `.env` contains:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
OPENROUTER_API_KEY=optional_openrouter_key
LLM_MODEL=deepseek/deepseek-v4-flash
```

Deploy from this laptop to the cloud server:
```bash
cd /Users/aidilkhairi/Desktop/arhive_photo_assistant
deployment/deploy_to_cloud.sh ubuntu@YOUR_SERVER_IP /Users/aidilkhairi/Downloads/aidilkey.pem --include-env
```

If your server user is not `ubuntu`, replace it with the correct one, e.g.
`ec2-user@YOUR_SERVER_IP`.

What the deploy script does:
- copies this project to `/opt/archive_photo_assistant`
- installs Python dependencies
- installs Node.js 22 if needed
- installs OpenClaw
- writes OpenClaw Telegram config to `~/.openclaw/openclaw.json`
- enables the `archive-photo-openclaw` system service

Check OpenClaw service on the server:
```bash
sudo systemctl status archive-photo-openclaw --no-pager
sudo journalctl -u archive-photo-openclaw -f
```

Pair the Telegram DM after the service starts:
```bash
openclaw pairing list telegram
openclaw pairing approve telegram <CODE>
```

Fallback mode, if you want the direct Python Telegram bot instead of OpenClaw:
```bash
MODE=telegram-bot deployment/deploy_to_cloud.sh ubuntu@YOUR_SERVER_IP /Users/aidilkhairi/Downloads/aidilkey.pem --include-env
```

Then check:
```bash
sudo systemctl status archive-photo-telegram --no-pager
sudo journalctl -u archive-photo-telegram -f
```

### Before you touch the code — things to know

- **`.env`** holds `OPENROUTER_API_KEY` + `LLM_MODEL` (git-ignored). With them,
  Module 4 writes an LLM narrative; without them it uses a rule-based template —
  the pipeline never breaks either way. **Rotate the key after the project.**
- **Sharpness is measured on the *denoised* image**, so it reads low for almost
  every photo — that's why it is weighted only 0.10 in the condition score
  (`WEIGHTS` in `report_generator.py`).
- The JSON quality key is **`blur`** but it holds the *sharpness* score (a
  half-finished rename). Read `quality["blur"]`.
- The card shows a **trimmed** narrative; the **full** text is in
  `report.narrative` and is included in the final Telegram message.
- Running **many** LLM narratives back-to-back can hit OpenRouter rate limits (the
  batch retries automatically). The live one-photo demo will not.
- Regenerate everything: `python main.py`. Re-run only Modules 2–4 on existing
  JSON: `python main.py --skip-preprocessing`.
