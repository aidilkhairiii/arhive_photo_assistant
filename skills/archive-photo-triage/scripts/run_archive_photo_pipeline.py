#!/usr/bin/env python3
"""Run Historical Archive Management for uploaded images or dashboard updates."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from tempfile import gettempdir
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMP_ROOT = Path(os.environ.get("TMPDIR", gettempdir()))
os.environ.setdefault("MPLCONFIGDIR", str(TEMP_ROOT / "archive_photo_assistant_mpl"))
os.environ.setdefault("XDG_CACHE_HOME", str(TEMP_ROOT / "archive_photo_assistant_cache"))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from preprocessing import preprocess_image  # noqa: E402
from quality_assestment import update_metadata_with_quality  # noqa: E402
from fading_analysis import update_metadata_with_fading  # noqa: E402
from report_generator import generate_dashboard, generate_report  # noqa: E402
from telegram_formatting import format_telegram_result  # noqa: E402


SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
MAX_BATCH_IMAGES = 20
PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
RESEARCH_VALUE_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
CATEGORY_RULES = {
    "People Collection": {"portrait", "group_photo"},
    "Architecture Collection": {"building", "monument"},
    "Documents Collection": {"document", "handwritten"},
    "Objects Collection": {"vehicle", "historical_object"},
    "Scenes Collection": {"outdoor", "indoor", "ceremony"},
}
HIGH_RESEARCH_TAGS = {"building", "document", "handwritten", "group_photo"}
MEDIUM_RESEARCH_TAGS = {"portrait", "historical_object", "outdoor"}


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _safe_image_name(path: Path) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in path.stem)
    suffix = path.suffix.lower()
    return f"{timestamp}_{stem}{suffix}"


def _stage_upload(image_path: Path, uploads_dir: Path) -> Path:
    if not image_path.exists() or not image_path.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")
    if image_path.suffix.lower() not in SUPPORTED_EXTS:
        raise ValueError(f"Unsupported image type: {image_path.suffix}")

    uploads_dir.mkdir(parents=True, exist_ok=True)
    staged_path = uploads_dir / _safe_image_name(image_path)
    shutil.copy2(image_path, staged_path)
    return staged_path


def _write_metadata(metadata: dict[str, Any], meta_dir: Path) -> Path:
    meta_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = meta_dir / f"{metadata['image_id']}.json"
    with open(metadata_path, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2, default=_json_default)
    return metadata_path


def _priority_score(metadata: dict[str, Any]) -> int:
    """Higher means the image should be restored earlier."""
    report = metadata.get("report", {})
    quality = metadata.get("quality", {})
    fading = metadata.get("fading_analysis", {}).get("fading", "Moderate")

    score = 100 - int(report.get("condition_score", 100))
    sharpness = int(quality.get("blur", 100))
    contrast = int(quality.get("contrast", 100))
    brightness = int(quality.get("brightness", 50))

    if fading == "Severe":
        score += 15
    elif fading == "Moderate":
        score += 8

    if sharpness < 30:
        score += 10
    elif sharpness < 50:
        score += 5

    if contrast < 30:
        score += 10
    elif contrast < 45:
        score += 5

    if brightness < 30 or brightness > 85:
        score += 6

    return max(0, min(100, int(score)))


def _priority_level(priority_score: int) -> str:
    if priority_score >= 65:
        return "HIGH"
    if priority_score >= 35:
        return "MEDIUM"
    return "LOW"


def _priority_reason(metadata: dict[str, Any]) -> str:
    report = metadata.get("report", {})
    issues = [issue for issue in report.get("issues", []) if issue != "No major issues"]
    if issues:
        return ", ".join(issues[:3])

    quality = metadata.get("quality", {})
    fading = metadata.get("fading_analysis", {}).get("fading", "Unknown")
    return (
        f"{fading.lower()} fading; sharpness {quality.get('blur')}, "
        f"contrast {quality.get('contrast')}"
    )


def _research_value(tags: list[str]) -> tuple[str, str]:
    """Estimate research value from existing visible-content tags only."""
    tag_set = set(tags)
    high_matches = sorted(tag_set & HIGH_RESEARCH_TAGS)
    if high_matches:
        return (
            "HIGH",
            f"Detected tags suggest strong research use: {', '.join(high_matches)}.",
        )

    medium_matches = sorted(tag_set & MEDIUM_RESEARCH_TAGS)
    if medium_matches:
        return (
            "MEDIUM",
            f"Detected tags suggest contextual archive value: {', '.join(medium_matches)}.",
        )

    return (
        "LOW",
        "Detected tags do not indicate a high-value research category yet.",
    )


def _recommended_action(
    condition_category: str,
    restoration_priority: str,
    research_value: str,
    priority_score: int,
) -> tuple[str, str]:
    """Return a preservation action focused on what the user should do next."""
    if restoration_priority == "HIGH" and (
        condition_category == "Poor" or research_value == "HIGH" or priority_score >= 75
    ):
        return (
            "Immediate Preservation Recommended",
            "Stabilize and review soon because condition risk and collection value are both significant.",
        )

    if restoration_priority == "HIGH" or condition_category == "Poor":
        return (
            "Restoration Recommended",
            "Add this item to the restoration queue before lower-risk archive-only items.",
        )

    if restoration_priority == "MEDIUM" or research_value == "HIGH":
        return (
            "Digitize Soon",
            "Prioritize a clean digital copy and catalogue record before routine storage.",
        )

    return (
        "Archive Only",
        "Store with normal catalogue controls and revisit after higher-priority items.",
    )


def _catalogue_description(metadata: dict[str, Any]) -> str:
    report = metadata.get("report", {})
    fading = metadata.get("fading_analysis", {}).get("fading", "Unknown")
    tags = metadata.get("fading_analysis", {}).get("tags", [])
    tag_text = ", ".join(tags[:4]) if tags else "archival photograph"
    issues = [issue for issue in report.get("issues", []) if issue != "No major issues"]
    issue_text = "; ".join(issues[:2]) if issues else "no major automated issues"
    return (
        f"Archival photograph with possible {tag_text} content. "
        f"Condition is {report.get('condition_label')} "
        f"({report.get('condition_score')}/100); fading: {fading}. "
        f"Automated issues: {issue_text}."
    )


def _categories_for_tags(tags: list[str]) -> list[str]:
    categories = [
        category
        for category, accepted_tags in CATEGORY_RULES.items()
        if accepted_tags.intersection(tags)
    ]
    return categories or ["Uncategorized Collection"]


def _catalogue_entry(metadata: dict[str, Any], metadata_path: Path) -> dict[str, Any]:
    report = metadata.get("report", {})
    tags = metadata.get("fading_analysis", {}).get("tags", [])
    priority_score = _priority_score(metadata)
    priority = _priority_level(priority_score)
    categories = _categories_for_tags(tags)
    research_value, research_reason = _research_value(tags)
    recommended_action, action_reason = _recommended_action(
        report.get("condition_label"),
        priority,
        research_value,
        priority_score,
    )

    return {
        "file_name": metadata.get("source_file_name") or Path(metadata.get("original_path") or metadata["image_id"]).name,
        "image_id": metadata["image_id"],
        "description": _catalogue_description(metadata),
        "tags": tags,
        "categories": categories,
        "primary_category": categories[0],
        "condition_score": report.get("condition_score"),
        "condition_category": report.get("condition_label"),
        "restoration_priority": priority,
        "priority_score": priority_score,
        "priority_reason": _priority_reason(metadata),
        "research_value": research_value,
        "research_reason": research_reason,
        "recommended_action": recommended_action,
        "recommendation": recommended_action,
        "action_reason": action_reason,
        "quality": metadata.get("quality", {}),
        "fading_analysis": metadata.get("fading_analysis", {}),
        "issues": report.get("issues", []),
        "metadata_path": str(metadata_path),
        "card_path": report.get("card_path"),
    }


def _error_entry(image_path: Path, error: str) -> dict[str, Any]:
    return {
        "status": "error",
        "file_name": image_path.name,
        "image_id": image_path.stem,
        "error": error,
    }


def _rank_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        entries,
        key=lambda item: (
            PRIORITY_ORDER.get(item.get("restoration_priority", "LOW"), 9),
            -int(item.get("priority_score", 0)),
            int(item.get("condition_score") or 100),
            item.get("file_name", ""),
        ),
    )
    ranking: list[dict[str, Any]] = []
    for index, item in enumerate(ranked, start=1):
        ranking.append({
            "rank": index,
            "file_name": item["file_name"],
            "image_id": item["image_id"],
            "priority": item["restoration_priority"],
            "priority_score": item["priority_score"],
            "condition_score": item["condition_score"],
            "condition_category": item["condition_category"],
            "reason": item["priority_reason"],
            "research_value": item.get("research_value"),
            "recommended_action": item.get("recommended_action"),
            "tags": item["tags"],
            "card_path": item.get("card_path"),
        })
    return ranking


def _rank_research_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        entries,
        key=lambda item: (
            RESEARCH_VALUE_ORDER.get(item.get("research_value", "LOW"), 9),
            PRIORITY_ORDER.get(item.get("restoration_priority", "LOW"), 9),
            int(item.get("condition_score") or 100),
            item.get("file_name", ""),
        ),
    )
    return [
        {
            "rank": index,
            "file_name": item["file_name"],
            "image_id": item["image_id"],
            "research_value": item.get("research_value", "LOW"),
            "reason": item.get("research_reason", ""),
            "tags": item.get("tags", []),
            "condition_category": item.get("condition_category"),
            "restoration_priority": item.get("restoration_priority"),
            "recommended_action": item.get("recommended_action"),
        }
        for index, item in enumerate(ranked, start=1)
    ]


def _build_categories(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    for entry in entries:
        for category in entry.get("categories", ["Uncategorized Collection"]):
            categories.setdefault(category, []).append(entry["file_name"])
    return dict(sorted(categories.items()))


def _build_metadata_table(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "file_name": entry["file_name"],
            "category": entry.get("primary_category"),
            "tags": entry.get("tags", []),
            "condition": entry.get("condition_category"),
            "priority": entry.get("restoration_priority"),
            "research_value": entry.get("research_value"),
            "recommendation": entry.get("recommended_action"),
        }
        for entry in entries
    ]


def _build_batch_summary(
    batch_id: str,
    total_images: int,
    entries: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    ranking: list[dict[str, Any]],
    research_ranking: list[dict[str, Any]],
    categories: dict[str, list[str]],
    catalogue_json_path: Path,
    catalogue_csv_path: Path,
) -> dict[str, Any]:
    condition_distribution = Counter(entry["condition_category"] for entry in entries)
    priority_distribution = Counter(entry["restoration_priority"] for entry in entries)
    research_value_distribution = Counter(entry["research_value"] for entry in entries)
    category_distribution = {category: len(images) for category, images in categories.items()}
    high_priority = [item for item in ranking if item["priority"] == "HIGH"]
    high_research = [item for item in research_ranking if item["research_value"] == "HIGH"]

    return {
        "batch_id": batch_id,
        "total_images": total_images,
        "processed_images": len(entries),
        "failed_images": len(failures),
        "condition_distribution": {
            "Excellent": condition_distribution.get("Excellent", 0),
            "Good": condition_distribution.get("Good", 0),
            "Fair": condition_distribution.get("Fair", 0),
            "Poor": condition_distribution.get("Poor", 0),
        },
        "priority_distribution": {
            "HIGH": priority_distribution.get("HIGH", 0),
            "MEDIUM": priority_distribution.get("MEDIUM", 0),
            "LOW": priority_distribution.get("LOW", 0),
        },
        "research_value_distribution": {
            "HIGH": research_value_distribution.get("HIGH", 0),
            "MEDIUM": research_value_distribution.get("MEDIUM", 0),
            "LOW": research_value_distribution.get("LOW", 0),
        },
        "category_distribution": category_distribution,
        "detected_categories": list(categories.keys()),
        "high_priority_items": high_priority,
        "high_research_items": high_research,
        "top_restoration_priorities": ranking[:5],
        "top_research_relevant_images": research_ranking[:5],
        "recommended_restoration_order": [item["file_name"] for item in ranking],
        "catalogue_json_path": str(catalogue_json_path),
        "catalogue_csv_path": str(catalogue_csv_path),
    }


def _write_catalogue_exports(
    batch_id: str,
    catalogue_dir: Path,
    payload: dict[str, Any],
) -> tuple[Path, Path]:
    catalogue_dir.mkdir(parents=True, exist_ok=True)
    json_path = catalogue_dir / f"{batch_id}_catalogue.json"
    csv_path = catalogue_dir / f"{batch_id}_catalogue.csv"

    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, default=_json_default)

    csv_fields = [
        "file_name",
        "description",
        "tags",
        "primary_category",
        "condition_score",
        "condition_category",
        "restoration_priority",
        "priority_score",
        "priority_reason",
        "research_value",
        "research_reason",
        "recommended_action",
        "action_reason",
        "metadata_path",
        "card_path",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=csv_fields)
        writer.writeheader()
        for entry in payload.get("images", []):
            if entry.get("status") == "error":
                continue
            row = {field: entry.get(field, "") for field in csv_fields}
            row["tags"] = "; ".join(entry.get("tags", []))
            writer.writerow(row)

    return json_path, csv_path


def _format_distribution(distribution: dict[str, int]) -> str:
    parts = [f"{name}: {count}" for name, count in distribution.items() if count]
    return ", ".join(parts) if parts else "none yet"


def _friendly_batch_display(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("batch_summary", {})
    ranking = payload.get("priority_ranking", [])
    research_ranking = payload.get("research_ranking", [])
    categories = payload.get("categories", {})
    metadata_table = payload.get("metadata_table", [])
    failures = payload.get("failures", [])
    skipped = payload.get("skipped_images", [])

    top = ranking[0] if ranking else None
    total = summary.get("total_images", 0)
    processed = summary.get("processed_images", 0)
    failed = summary.get("failed_images", 0)

    summary_lines = [
        "Collection Summary",
        "Analysis complete.",
        f"I checked {processed} of {total} photo(s).",
    ]
    if failed:
        summary_lines.append(f"{failed} photo(s) could not be processed, but the rest were completed.")
    if skipped:
        summary_lines.append(f"{len(skipped)} extra photo(s) were skipped because the batch limit is {MAX_BATCH_IMAGES}.")
    if top:
        summary_lines.append(
            f"Restore first: {top['file_name']} ({top['priority']} priority) because {top['reason']}."
        )

    summary_lines.extend([
        "",
        "Condition mix:",
        _format_distribution(summary.get("condition_distribution", {})),
        "Priority mix:",
        _format_distribution(summary.get("priority_distribution", {})),
        "Research value mix:",
        _format_distribution(summary.get("research_value_distribution", {})),
        "",
        "Collection groups:",
        _format_distribution(summary.get("category_distribution", {})),
    ])

    ranking_lines = ["Restoration priority ranking:"]
    if ranking:
        for item in ranking[:10]:
            ranking_lines.append(
                f"{item['rank']}. {item['file_name']} - {item['priority']} priority. "
                f"Action: {item.get('recommended_action')}. Reason: {item['reason']}."
            )
        if len(ranking) > 10:
            ranking_lines.append(f"...and {len(ranking) - 10} more item(s) in the catalogue.")
    else:
        ranking_lines.append("No successful images were available to rank.")

    category_lines = ["Category Breakdown:"]
    if categories:
        for category, files in categories.items():
            preview = ", ".join(files[:4])
            more = f", +{len(files) - 4} more" if len(files) > 4 else ""
            category_lines.append(f"- {category}: {preview}{more}")
    else:
        category_lines.append("- No categories were created.")

    if failures:
        failed_names = ", ".join(item.get("file_name", "unknown") for item in failures[:5])
        category_lines.append(f"\nCould not process: {failed_names}")

    research_lines = ["Top Research-Relevant Images:"]
    if research_ranking:
        for item in research_ranking[:5]:
            tag_text = ", ".join(item.get("tags", [])[:4]) or "no tags"
            research_lines.append(
                f"{item['rank']}. {item['file_name']} - {item['research_value']} value "
                f"({tag_text}). {item.get('recommended_action')}."
            )
    else:
        research_lines.append("No successful images were available for research ranking.")

    metadata_lines = ["Metadata Summary:"]
    if metadata_table:
        for item in metadata_table[:8]:
            metadata_lines.append(
                f"- {item['file_name']}: {item['category']} | {item['condition']} | "
                f"{item['priority']} priority | {item['research_value']} research | "
                f"{item['recommendation']}"
            )
        if len(metadata_table) > 8:
            metadata_lines.append(f"...and {len(metadata_table) - 8} more item(s) in the CSV.")
    else:
        metadata_lines.append("No metadata rows were created.")

    actions = Counter(entry.get("recommended_action") for entry in payload.get("images", []) if entry.get("status") != "error")
    action_lines = ["Recommended Actions:"]
    if actions:
        for action, count in sorted(actions.items()):
            action_lines.append(f"- {action}: {count} item(s)")
    else:
        action_lines.append("- No actions available.")

    return {
        "summary_text": "\n".join(summary_lines),
        "ranking_text": "\n".join(ranking_lines),
        "categories_text": "\n".join(category_lines),
        "research_text": "\n".join(research_lines),
        "metadata_text": "\n".join(metadata_lines),
        "actions_text": "\n".join(action_lines),
        "catalogue_note": "I prepared catalogue JSON and CSV files for download.",
        "catalogue_files": {
            "json": summary.get("catalogue_json_path"),
            "csv": summary.get("catalogue_csv_path"),
        },
    }


def _process_one_image(
    image_path: Path,
    args: argparse.Namespace,
    batch_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    image_path = image_path.expanduser().resolve()
    uploads_dir = (PROJECT_ROOT / args.uploads_dir).resolve()
    output_dir = (PROJECT_ROOT / args.output_dir).resolve()
    meta_dir = (PROJECT_ROOT / args.meta_dir).resolve()
    reports_dir = (PROJECT_ROOT / args.reports_dir).resolve()

    staged_path = _stage_upload(image_path, uploads_dir)

    metadata = preprocess_image(
        str(staged_path),
        str(output_dir),
        keep_aspect=not args.stretch,
        save_normalized=False,
    )
    metadata["source_file_name"] = image_path.name
    metadata["staged_path"] = str(staged_path)
    if batch_id:
        metadata["batch_id"] = batch_id

    if metadata.get("status") != "ok":
        metadata_path = _write_metadata(metadata, meta_dir)
        return {
            "status": "error",
            "image_id": metadata.get("image_id", staged_path.stem),
            "file_name": image_path.name,
            "metadata_path": str(metadata_path),
            "error": metadata.get("error", "Preprocessing failed."),
        }, None

    metadata = update_metadata_with_quality(metadata, base_dir=PROJECT_ROOT)
    metadata = update_metadata_with_fading(metadata, base_dir=PROJECT_ROOT)
    metadata["report"] = generate_report(
        metadata,
        out_dir=reports_dir,
        use_llm=not args.no_llm,
    )
    metadata_path = _write_metadata(metadata, meta_dir)

    report = metadata["report"]
    fading = metadata.get("fading_analysis", {})
    catalogue_entry = _catalogue_entry(metadata, metadata_path)

    return {
        "status": "ok",
        "image_id": metadata["image_id"],
        "file_name": image_path.name,
        "metadata_path": str(metadata_path),
        "card_path": report.get("card_path"),
        "condition_score": report.get("condition_score"),
        "condition_label": report.get("condition_label"),
        "condition_category": report.get("condition_label"),
        "priority": report.get("priority"),
        "restoration_priority": catalogue_entry["restoration_priority"],
        "priority_score": catalogue_entry["priority_score"],
        "priority_reason": catalogue_entry["priority_reason"],
        "research_value": catalogue_entry["research_value"],
        "research_reason": catalogue_entry["research_reason"],
        "recommended_action": catalogue_entry["recommended_action"],
        "recommendation": catalogue_entry["recommendation"],
        "action_reason": catalogue_entry["action_reason"],
        "issues": report.get("issues", []),
        "quality": metadata.get("quality", {}),
        "fading_analysis": fading,
        "tags": fading.get("tags", []),
        "categories": catalogue_entry["categories"],
        "description": catalogue_entry["description"],
        "narrative": report.get("narrative", ""),
        "narrative_source": report.get("narrative_source", ""),
        "processed_color_path": metadata.get("processed_color_path"),
        "processed_gray_path": metadata.get("processed_gray_path"),
        "catalogue_entry": catalogue_entry,
    }, catalogue_entry


def run_one_photo(args: argparse.Namespace) -> dict[str, Any]:
    result, _ = _process_one_image(Path(args.image), args)
    return result


def _iter_batch_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for image in args.images or []:
        paths.append(Path(image))

    if args.batch_dir:
        batch_dir = Path(args.batch_dir).expanduser().resolve()
        if not batch_dir.exists() or not batch_dir.is_dir():
            raise FileNotFoundError(f"Batch directory not found: {batch_dir}")
        paths.extend(
            path for path in sorted(batch_dir.iterdir())
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTS
        )

    unique_paths: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser().resolve())
        if key not in seen:
            unique_paths.append(path)
            seen.add(key)
    return unique_paths


def run_batch(args: argparse.Namespace) -> dict[str, Any]:
    paths = _iter_batch_paths(args)
    if not paths:
        return {
            "status": "error",
            "error": "No batch images were provided.",
        }

    max_images = max(1, min(int(args.max_images), MAX_BATCH_IMAGES))
    skipped = [str(path) for path in paths[max_images:]]
    paths = paths[:max_images]
    batch_id = args.batch_id or datetime.now().strftime("batch_%Y%m%d_%H%M%S")

    entries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    image_results: list[dict[str, Any]] = []

    for path in paths:
        try:
            result, catalogue_entry = _process_one_image(path, args, batch_id=batch_id)
            if result.get("status") == "ok" and catalogue_entry is not None:
                entries.append(catalogue_entry)
                image_results.append(catalogue_entry)
            else:
                failure = {
                    "status": "error",
                    "file_name": path.name,
                    "image_id": result.get("image_id", path.stem),
                    "error": result.get("error", "Image failed."),
                    "metadata_path": result.get("metadata_path"),
                }
                failures.append(failure)
                image_results.append(failure)
        except Exception as error:
            failure = _error_entry(path, str(error))
            failures.append(failure)
            image_results.append(failure)

    ranking = _rank_entries(entries)
    research_ranking = _rank_research_entries(entries)
    categories = _build_categories(entries)
    metadata_table = _build_metadata_table(entries)
    catalogue_dir = (PROJECT_ROOT / args.catalogue_dir).resolve()
    placeholder_json = catalogue_dir / f"{batch_id}_catalogue.json"
    placeholder_csv = catalogue_dir / f"{batch_id}_catalogue.csv"

    payload: dict[str, Any] = {
        "status": "ok" if entries else "error",
        "batch_summary": {},
        "priority_ranking": ranking,
        "research_ranking": research_ranking,
        "categories": categories,
        "metadata_table": metadata_table,
        "images": image_results,
        "failures": failures,
        "skipped_images": skipped,
    }
    summary = _build_batch_summary(
        batch_id=batch_id,
        total_images=len(paths),
        entries=entries,
        failures=failures,
        ranking=ranking,
        research_ranking=research_ranking,
        categories=categories,
        catalogue_json_path=placeholder_json,
        catalogue_csv_path=placeholder_csv,
    )
    if skipped:
        summary["skipped_images"] = len(skipped)
        summary["max_images"] = max_images
    payload["batch_summary"] = summary

    json_path, csv_path = _write_catalogue_exports(batch_id, catalogue_dir, payload)
    payload["batch_summary"]["catalogue_json_path"] = str(json_path)
    payload["batch_summary"]["catalogue_csv_path"] = str(csv_path)
    payload["telegram_display"] = _friendly_batch_display(payload)

    if not entries:
        payload["error"] = "No images were processed successfully."

    return payload


def run_rank(args: argparse.Namespace) -> dict[str, Any]:
    meta_dir = (PROJECT_ROOT / args.meta_dir).resolve()
    reports_dir = (PROJECT_ROOT / args.reports_dir).resolve()

    metadata_list: list[dict[str, Any]] = []
    for metadata_path in sorted(meta_dir.glob("*.json")):
        with open(metadata_path, "r", encoding="utf-8") as file:
            metadata = json.load(file)
        if metadata.get("status") == "ok" and "report" in metadata:
            metadata_list.append(metadata)

    if not metadata_list:
        return {
            "status": "error",
            "error": "No completed reports found yet. Upload and assess at least one photo first.",
        }

    dashboard_path = generate_dashboard(metadata_list, out_dir=reports_dir)
    if dashboard_path is None:
        return {
            "status": "error",
            "error": "Could not generate dashboard from current reports.",
        }

    return {
        "status": "ok",
        "dashboard_path": str(dashboard_path),
        "photo_count": len(metadata_list),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Historical Archive Management for a Telegram/OpenClaw upload."
    )
    parser.add_argument("--image", help="Path to one uploaded image.")
    parser.add_argument("--images", nargs="+", help="Paths to up to 20 uploaded images.")
    parser.add_argument("--batch-dir", help="Directory of images to analyze as one collection.")
    parser.add_argument("--batch-id", help="Optional id for catalogue export filenames.")
    parser.add_argument("--max-images", type=int, default=MAX_BATCH_IMAGES, help="Maximum images per batch.")
    parser.add_argument("--rank", action="store_true", help="Generate the archive ranking dashboard.")
    parser.add_argument("--uploads-dir", default="uploads", help="Where uploaded images are staged.")
    parser.add_argument("--output-dir", default="data/processed", help="Where processed images are written.")
    parser.add_argument("--meta-dir", default="outputs", help="Where metadata JSON files are written/read.")
    parser.add_argument("--reports-dir", default="outputs/reports", help="Where report cards/dashboard are written.")
    parser.add_argument("--catalogue-dir", default="outputs/catalogues", help="Where batch catalogue JSON/CSV files are written.")
    parser.add_argument("--stretch", action="store_true", help="Stretch images instead of letterboxing.")
    parser.add_argument("--no-llm", action="store_true", help="Use template narrative instead of OpenRouter.")
    parser.add_argument("--telegram-text", action="store_true", help="Print only the final Telegram-ready message.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    request_kind = (
        "rank"
        if args.rank
        else "batch"
        if args.images or args.batch_dir
        else "single"
        if args.image
        else None
    )

    try:
        if args.rank:
            result = run_rank(args)
        elif args.images or args.batch_dir:
            result = run_batch(args)
        elif args.image:
            result = run_one_photo(args)
        else:
            parser.error("provide --image, --images, --batch-dir, or --rank")
    except Exception as error:
        result = {
            "status": "error",
            "error": str(error),
        }

    result["telegram_message"] = format_telegram_result(result, request_kind)

    if args.telegram_text:
        print(result["telegram_message"])
        return 0

    print(json.dumps(result, indent=2, default=_json_default))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
