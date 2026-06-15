"""Telegram-facing text formatting for Archive Photo Assistant results.

The analysis pipeline returns dictionaries only. This module is the single
place that turns those dictionaries into final chat text.
"""

from __future__ import annotations

from typing import Any


TELEGRAM_TEXT_LIMIT = 3900


def fit_telegram_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    """Keep final text inside Telegram's message size with a clear note."""
    if len(text) <= limit:
        return text
    note = "\n\nResult shortened for Telegram. Full catalogue data was still generated."
    return text[: limit - len(note)].rstrip() + note


def _join_values(values: Any, default: str = "none") -> str:
    if not values:
        return default
    if isinstance(values, str):
        return values
    return ", ".join(str(value) for value in values) or default


def _quality_value(quality: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in quality:
            return quality[key]
    return "unknown"


def format_single_result(result: dict[str, Any]) -> str:
    """Build the one final Telegram message for a single uploaded image."""
    if result.get("status") != "ok":
        return (
            "Sorry, I could not process that image. "
            f"{result.get('error', 'Please try another JPG or PNG photo.')}"
        )

    quality = result.get("quality", {})
    fading = result.get("fading_analysis", {})
    issues = _join_values(result.get("issues", []), default="No major issues")
    tags = _join_values(result.get("tags") or fading.get("tags", []))
    narrative = result.get("narrative", "")

    return (
        "Analysis complete.\n"
        f"Condition: {result.get('condition_label')} ({result.get('condition_score')}/100)\n"
        f"Restoration priority: {result.get('restoration_priority', result.get('priority'))}\n"
        f"Main issues: {issues}\n"
        f"Fading: {fading.get('fading', 'Unknown')}\n"
        "Quality: "
        f"sharpness {_quality_value(quality, 'blur', 'sharpness')}, "
        f"brightness {_quality_value(quality, 'brightness')}, "
        f"contrast {_quality_value(quality, 'contrast')}\n"
        f"Tags: {tags}\n\n"
        f"{narrative}"
    ).rstrip()


def format_batch_summary(result: dict[str, Any]) -> str:
    display = result.get("telegram_display", {})
    if display.get("summary_text"):
        return display["summary_text"]

    summary = result.get("batch_summary", {})
    condition_distribution = summary.get("condition_distribution", {})
    category_distribution = summary.get("category_distribution", {})
    high_priority = summary.get("high_priority_items", [])

    condition_lines = [
        f"- {label}: {condition_distribution.get(label, 0)}"
        for label in ("Excellent", "Good", "Fair", "Poor")
    ]
    category_lines = [
        f"- {category}: {count}"
        for category, count in sorted(category_distribution.items())
    ] or ["- None"]

    skipped = summary.get("skipped_images", 0)
    skipped_text = (
        f"\nSkipped: {skipped} image(s), max batch is {summary.get('max_images')}"
        if skipped
        else ""
    )

    return (
        "Analysis complete.\n"
        f"I checked {summary.get('processed_images', 0)} of {summary.get('total_images', 0)} photo(s).\n"
        f"Failed: {summary.get('failed_images', 0)}"
        f"{skipped_text}\n\n"
        "Condition mix:\n"
        + "\n".join(condition_lines)
        + "\n\nCollection groups:\n"
        + "\n".join(category_lines)
        + f"\n\nHigh-priority items: {len(high_priority)}"
    )


def format_priority_ranking(result: dict[str, Any], limit: int = 10) -> str:
    display = result.get("telegram_display", {})
    if display.get("ranking_text"):
        return display["ranking_text"]

    ranking = result.get("priority_ranking", [])
    if not ranking:
        return "No priority ranking is available."

    lines = ["Recommended restoration order:"]
    for item in ranking[:limit]:
        lines.append(
            f"{item.get('rank')}. {item.get('file_name')} - "
            f"{item.get('priority')} priority. {item.get('reason')}."
        )

    if len(ranking) > limit:
        lines.append(f"...and {len(ranking) - limit} more in the catalogue file.")

    return "\n\n".join(lines)


def format_batch_result(result: dict[str, Any]) -> str:
    """Build the one final Telegram message for a batch upload."""
    if result.get("status") != "ok":
        return result.get("error", "No images were processed successfully.")

    sections = [
        format_batch_summary(result),
        format_priority_ranking(result),
    ]
    display = result.get("telegram_display", {})
    categories_text = display.get("categories_text")
    if categories_text:
        sections.append(categories_text)
    catalogue_note = display.get("catalogue_note")
    if catalogue_note:
        sections.append(catalogue_note)
    return "\n\n".join(section for section in sections if section)


def format_rank_result(result: dict[str, Any]) -> str:
    """Build the one final Telegram message for a rank/dashboard request."""
    if result.get("status") != "ok":
        return result.get("error", "No dashboard is available yet.")
    return f"Restoration-priority dashboard updated for {result.get('photo_count', 0)} photo(s)."


def format_telegram_result(result: dict[str, Any], request_kind: str | None = None) -> str:
    """Format any pipeline result into one final Telegram-ready message."""
    if request_kind == "single":
        return fit_telegram_text(format_single_result(result))
    if request_kind == "batch":
        return fit_telegram_text(format_batch_result(result))
    if request_kind == "rank":
        return fit_telegram_text(format_rank_result(result))

    if "batch_summary" in result or "priority_ranking" in result:
        return fit_telegram_text(format_batch_result(result))
    if "dashboard_path" in result or "photo_count" in result:
        return fit_telegram_text(format_rank_result(result))
    return fit_telegram_text(format_single_result(result))
