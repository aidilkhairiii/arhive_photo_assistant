---
name: archive-photo-triage
description: Use when an OpenClaw or Telegram user uploads old, archival, scanned, damaged, faded, blurry, or historical photos and wants the Historical Archive Management Assistant to assess condition, catalogue items, estimate research value, prioritize restoration, recommend actions, or build archive metadata.
---

# Historical Archive Management Assistant

## Purpose

Handle the Telegram-facing Historical Archive Management Assistant workflow:

1. User sends one archival photo, or a batch of up to 20 photos, in Telegram.
2. OpenClaw saves or accesses the uploaded image file(s).
3. Run the local archive management pipeline.
4. For one image, return one final plain-English archive-management report.
5. For a batch, return one final collection report with restoration ranking,
   category groups, research value, metadata summary, and recommended actions.

The skill should use the existing project modules. Do not reimplement blur,
brightness, contrast, fading, scoring, or report rendering in the chat layer.

## Skill Specification

Use these details when explaining or presenting the OpenClaw skill:

- Skill name: Historical Archive Management Assistant
- Target user: history students, museum studies students, archivists,
  educators, archive managers, and heritage staff who need to organize scanned
  historical photographs.
- Real-world problem: the user has many old photos and needs to know how to
  catalogue, prioritize, preserve, and manage the collection.
- Input format: one image, or up to 20 images in one Telegram batch, as JPG, PNG, BMP, TIFF, or WEBP.
- CV/image-processing method: resize, grayscale, denoise, content-box crop,
  Laplacian sharpness, brightness mean, contrast standard deviation, color or
  tonal fading analysis, simple image tags, weighted condition score.
- Step-by-step workflow: upload photo, preprocess, assess quality, analyze
  fading, reuse existing tags, generate catalogue data, estimate research value,
  recommend preservation actions, then return one final Telegram-ready message.
- Output format for one image: one final text response with collection summary,
  restoration priority, category breakdown, research value, metadata summary,
  recommended action, and plain-English narrative.
- Output format for a batch: JSON object with `batch_summary`,
  `priority_ranking`, `research_ranking`, `categories`, `metadata_table`, and
  `images`; the Telegram response should summarize those results in one
  message.
- Limitation handling: tags are guesses, denoising may reduce true sharpness,
  and bad uploads return a friendly error.
- Ethical boundary: screening aid only; not a replacement for expert archival
  inspection or cultural/historical verification.

## Required Project Location

Run commands from the project root. Common locations:

```bash
/Users/aidilkhairi/Desktop/arhive_photo_assistant
/opt/archive_photo_assistant
```

The one-photo runner is:

```bash
python3 skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py --image /path/to/uploaded_photo.jpg --telegram-text
```

The batch runner is:

```bash
python3 skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py --images /path/photo1.jpg /path/photo2.jpg /path/photo3.jpg --no-llm --telegram-text
```

Limit batches to 20 images. If the user uploads more than 20, process the first
20 and explain that the remainder were skipped.

Telegram may split larger album uploads into multiple message groups. If the
user is uploading a collection, wait briefly until no new images are arriving
before running the batch command. Do not run separate 2-photo or 4-photo batches
when the user clearly intends one collection.

## User-Facing Style

Keep replies warm, short, and useful for museum staff. Do not sound like a raw
developer log.

Use this tone:

```text
Collection Summary
I checked 4 photos. Restore first: photo12.jpg because it has severe fading and low contrast.

Condition mix:
Poor: 2, Good: 2

Collection groups:
People Collection: 3
Architecture Collection: 1
Documents Collection: 1
```

Avoid:

- Markdown tables in Telegram.
- Long technical subscores unless the user asks.
- Absolute server paths such as `/opt/archive_photo_assistant/...`.
- Statements like "no physical damage" unless the system actually measured
  physical damage. The pipeline measures scan/image condition only.

For batch output, prefer the runner's `telegram_display.summary_text`,
`telegram_display.ranking_text`, `telegram_display.categories_text`,
`telegram_display.research_text`, `telegram_display.metadata_text`, and
`telegram_display.actions_text`.
Combine them into one final Telegram message. Do not attach report cards,
catalogue files, debug logs, progress messages, or tool output unless the user
asks for files in a separate request.

## Telegram Reply Contract

Every Telegram request must produce exactly one final reply:

- One uploaded image -> one final message with the single-photo report.
- Multiple uploaded images -> one final message with the complete batch report.
- No progress replies.
- No debug replies.
- No report-card image reply by default.
- No catalogue-file attachment by default.
- No tool-level Telegram calls from analysis, OCR, quality, tagging, ranking, or report generation code.

Pipeline and helper functions must return data only. The Telegram/OpenClaw
boundary is responsible for formatting that data into one final response.
For OpenClaw-triggered Telegram requests, call the runner with
`--telegram-text`; its stdout is already the final Telegram reply. Do not send a
second agent summary, debug message, raw JSON, file attachment, or report-card
image unless the user asks for files in a separate request.

## Telegram Single-Image Workflow

When the Telegram user uploads a JPG, JPEG, PNG, BMP, TIFF, or WEBP photo:

1. Save or locate the Telegram attachment on disk.
2. Run the one-photo runner with `--image` and `--telegram-text`.
3. Return exactly the final text printed to stdout as the only Telegram reply.

Use this one-message format in Telegram:

```text
Collection Summary
Analysis complete for 1 photo.

Restoration Priority Ranking
1. <file_name> - <priority> priority. <reason>.

Category Breakdown
- <category>

Top Research-Relevant Images
1. <file_name> - <research_value> research value.

Metadata Summary
- <file_name>: <condition> | <priority> priority | <research_value> research | <recommended_action>

Recommended Actions
- <recommended_action>: <reason>

<narrative>
```

## Telegram Batch Workflow

When the Telegram user uploads multiple images together:

1. Save or locate every Telegram attachment on disk.
2. Run the batch runner with `--images`, all saved paths, and `--telegram-text`.
3. Continue processing even if one image fails.
4. Return exactly the final text printed to stdout as the only Telegram reply.
5. The final text contains:
   - `telegram_display.summary_text`
   - `telegram_display.ranking_text`
   - `telegram_display.categories_text`
   - `telegram_display.research_text`
   - `telegram_display.metadata_text`
   - `telegram_display.actions_text`
   - a short note that catalogue JSON/CSV files were prepared

The batch runner returns:

```json
{
  "status": "ok",
  "batch_summary": {
    "total_images": 20,
    "processed_images": 19,
    "failed_images": 1,
    "condition_distribution": {
      "Excellent": 2,
      "Good": 6,
      "Fair": 7,
      "Poor": 4
    },
    "priority_distribution": {
      "HIGH": 4,
      "MEDIUM": 10,
      "LOW": 5
    },
    "research_value_distribution": {
      "HIGH": 6,
      "MEDIUM": 9,
      "LOW": 4
    },
    "category_distribution": {
      "People Collection": 8,
      "Architecture Collection": 4,
      "Documents Collection": 3
    },
    "high_priority_items": [],
    "recommended_restoration_order": [],
    "catalogue_json_path": "outputs/catalogues/batch_20260615_010000_catalogue.json",
    "catalogue_csv_path": "outputs/catalogues/batch_20260615_010000_catalogue.csv"
  },
  "priority_ranking": [],
  "research_ranking": [],
  "categories": {},
  "metadata_table": [],
  "images": [],
  "failures": []
}
```

Each item in `images` is a catalogue entry:

```json
{
  "file_name": "photo12.jpg",
  "description": "Archival photograph with possible building, outdoor content...",
  "tags": ["building", "outdoor"],
  "condition_score": 41,
  "condition_category": "Poor",
  "restoration_priority": "HIGH",
  "research_value": "HIGH",
  "recommended_action": "Immediate Preservation Recommended",
  "priority_reason": "severe fading and low contrast"
}
```

## Single-Image Runner Output

Without `--telegram-text`, the runner prints JSON with this shape:

```json
{
  "status": "ok",
  "image_id": "uploaded_photo",
  "metadata_path": "outputs/uploaded_photo.json",
  "card_path": "outputs/reports/uploaded_photo_card.png",
  "condition_score": 47,
  "condition_label": "Poor",
  "priority": "High",
  "issues": ["Severe fading", "Blur / soft detail"],
  "quality": {
    "blur": 75,
    "brightness": 68,
    "contrast": 52
  },
  "fading_analysis": {
    "fading": "Moderate",
    "tags": ["building", "street"]
  },
  "narrative": "Plain-English restoration recommendation..."
}
```

## Pipeline Details

The runner executes the modules in this order:

1. `preprocessing.py`
   - loads the uploaded image
   - resizes to 512 x 512
   - saves processed color and grayscale images
   - writes metadata fields like `processed_gray_path`, `processed_color_path`,
     and `analysis.content_box`
2. `quality_assestment.py`
   - reads the grayscale image
   - crops to `content_box`
   - calculates blur, brightness, and contrast
3. `fading_analysis.py`
   - reads the color image
   - crops to `content_box`
   - classifies fading and creates tags
4. `report_generator.py`
   - computes condition score and restoration priority
   - writes narrative
   - renders the report-card PNG

## Dashboard Or Rank Request

If the user asks for `/rank`, "ranking", "dashboard", or "which photos should be
restored first", run:

```bash
python3 skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py --rank --telegram-text
```

Return exactly the final text printed to stdout. Do not attach the dashboard
image by default; the dashboard is still generated on disk for a separate file
request.

## Error Handling

Reply with a short helpful message when:

- the upload is not an image
- the image cannot be decoded
- the runner returns `status: "error"`
- no previous reports exist for `/rank`

Do not expose stack traces to Telegram users. Keep technical paths in the chat
only when useful for the developer.

## Boundaries

This tool is a screening assistant for archival triage. It does not replace
expert restoration judgement. Do not claim certainty about historical content,
identity, authenticity, culture, ethnicity, or the people in the photo.

Tags are simple automated guesses and may be wrong. Present them as "detected
tags" or "possible tags", not facts.
