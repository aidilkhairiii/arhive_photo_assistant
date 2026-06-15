"""Telegram-facing text formatting for Historical Archive Management results.

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

    fading = result.get("fading_analysis", {})
    issues = _join_values(result.get("issues", []), default="No major issues")
    tags = _join_values(result.get("tags") or fading.get("tags", []))
    narrative = result.get("narrative", "")
    research_value = result.get("research_value", "Unknown")
    recommended_action = result.get("recommended_action", "Review catalogue record")
    action_reason = result.get("action_reason") or result.get("priority_reason", "")

    return (
        "Collection Summary\n"
        "Analysis complete for 1 photo.\n"
        f"Condition: {result.get('condition_label')} ({result.get('condition_score')}/100)\n"
        f"Detected tags: {tags}\n\n"
        "Restoration Priority Ranking\n"
        f"1. {result.get('file_name', 'uploaded image')} - "
        f"{result.get('restoration_priority', result.get('priority'))} priority. "
        f"{result.get('priority_reason', issues)}.\n\n"
        "Category Breakdown\n"
        f"- { _join_values(result.get('categories', []), default='Uncategorized Collection') }\n\n"
        "Top Research-Relevant Images\n"
        f"1. {result.get('file_name', 'uploaded image')} - {research_value} research value. "
        f"{result.get('research_reason', '')}\n\n"
        "Metadata Summary\n"
        f"- {result.get('file_name', 'uploaded image')}: "
        f"{result.get('condition_label')} | "
        f"{result.get('restoration_priority', result.get('priority'))} priority | "
        f"{research_value} research | {recommended_action}\n\n"
        "Recommended Actions\n"
        f"- {recommended_action}: {action_reason}\n\n"
        f"{narrative}"
    ).rstrip()


def format_batch_summary(result: dict[str, Any]) -> str:
    display = result.get("telegram_display", {})
    if display.get("summary_text"):
        return display["summary_text"]

    summary = result.get("batch_summary", {})
    condition_distribution = summary.get("condition_distribution", {})
    category_distribution = summary.get("category_distribution", {})
    priority_distribution = summary.get("priority_distribution", {})
    research_distribution = summary.get("research_value_distribution", {})
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
        + "\n\nPriority mix:\n"
        + _join_values([f"{label}: {priority_distribution.get(label, 0)}" for label in ("HIGH", "MEDIUM", "LOW")])
        + "\n\nResearch value mix:\n"
        + _join_values([f"{label}: {research_distribution.get(label, 0)}" for label in ("HIGH", "MEDIUM", "LOW")])
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
    research_text = display.get("research_text")
    if research_text:
        sections.append(research_text)
    metadata_text = display.get("metadata_text")
    if metadata_text:
        sections.append(metadata_text)
    actions_text = display.get("actions_text")
    if actions_text:
        sections.append(actions_text)
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
