"""
Module 4 — Report Generation & Restoration-Priority Dashboard
=============================================================

Target user : history / museum archivists who have hundreds of scanned old
              photographs and need to decide WHICH ONES to restore first.

This module is the final stage of the pipeline. It consumes the metadata JSON
produced by Modules 1-3 (which already filled in ``quality`` and
``fading_analysis``) and turns those raw numbers into a DECISION:

    1. a single 0-100 ``condition_score`` (weighted blend of all signals),
    2. a ``condition_label``  (Excellent / Good / Fair / Poor),
    3. a restoration ``priority``  (High / Medium / Low),
    4. a plain-English ``narrative``  ("what is wrong + what to do next"),
    5. a VISUAL report card  (PNG)  -> one per photo, and
    6. a VISUAL ranking dashboard (PNG) -> the whole archive at a glance.

The narrative is HYBRID:
    * the score, label, priority and issue list are computed deterministically
      in pure Python (reproducible, explainable on a slide), and
    * the human-readable sentence is written by an LLM (via OpenRouter) IF an
      ``OPENROUTER_API_KEY`` is present in the environment or in a ``.env`` file,
      otherwise a rule-based template is used.
    The LLM call uses only the standard library (no extra pip install) and is
    wrapped in try/except so a live demo NEVER hard-fails.

``.env`` keys read by this module:
    OPENROUTER_API_KEY=sk-or-...           # enables the LLM narrative
    LLM_MODEL=deepseek/deepseek-v4-flash   # any OpenRouter model slug

The module is decoupled from Telegram on purpose. A bot only needs:

    report = generate_report(metadata)        # one photo  -> card + fields
    dash   = generate_dashboard(metadata_list) # whole set -> /rank dashboard

Run on every metadata JSON file in a folder:
    python report_generator.py --meta outputs

Run without any Claude call (pure template, no API):
    python report_generator.py --meta outputs --no-llm
"""

from __future__ import annotations

import argparse
import json
import os
import textwrap
from pathlib import Path
from typing import Any

import cv2
import numpy as np

import matplotlib
matplotlib.use("Agg")  # headless: render straight to PNG, no display needed
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Wedge


# ---------------------------------------------------------------------------
# Config — tweak these and the whole scoring changes. Documented for the team.
# ---------------------------------------------------------------------------

# Weighted blend of the five "goodness" sub-scores. Must sum to 1.0.
# Decided with the group: fading LEADS (it is the most common archival defect
# AND the one restoration actually fixes), sharpness is LOW because Module 2
# measures it on the *denoised* image, so it understates real detail.
WEIGHTS = {
    "fading":      0.35,
    "cleanliness": 0.20,   # from noise_sigma (higher noise = worse)
    "contrast":    0.20,
    "brightness":  0.15,   # deviation from a comfortable mid-tone
    "sharpness":   0.10,
}

# Fading is a 4-bucket label from Module 3 -> turn it into a 0-100 goodness.
FADING_GOODNESS = {"None": 100, "Mild": 70, "Moderate": 40, "Severe": 10}

REPORTS_DIR = "outputs/reports"

# Longer LLM narratives are trimmed to this many characters ON THE CARD only
# (the full text is kept in the JSON and can be sent as a Telegram caption).
CARD_NARRATIVE_CHARS = 300

# Colours used in the visuals.
PRIORITY_COLOR = {"High": "#c0392b", "Medium": "#e67e22", "Low": "#27ae60"}
FADING_COLOR = {"None": "#27ae60", "Mild": "#f1c40f", "Moderate": "#e67e22", "Severe": "#c0392b"}

# Default LLM for the narrative; overridden by LLM_MODEL in the environment/.env.
DEFAULT_LLM_MODEL = "deepseek/deepseek-v4-flash"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# Path resolution (matches the helper in Modules 2 & 3)
# ---------------------------------------------------------------------------

def _resolve_path(path: str | Path, metadata_path: str | Path | None = None) -> Path:
    """Resolve a relative path from the preprocessing metadata contract."""
    path = Path(path)
    if path.is_absolute():
        return path

    roots: list[Path] = [Path.cwd()]
    if metadata_path is not None:
        parent = Path(metadata_path).resolve().parent
        roots.extend([parent, parent.parent])

    for root in roots:
        candidate = root / path
        if candidate.exists():
            return candidate
    return roots[0] / path


def load_display_image(metadata: dict[str, Any], metadata_path: str | Path | None = None):
    """Load the photo to show on the card (color, content-box cropped, RGB)."""
    path = metadata.get("processed_color_path") or metadata.get("original_path")
    if not path:
        return None

    resolved = _resolve_path(path, metadata_path)
    img = cv2.imread(str(resolved), cv2.IMREAD_COLOR)
    if img is None:
        return None

    # Drop the black letterbox bars so the card shows only the real photo.
    box = metadata.get("analysis", {}).get("content_box")
    if box and len(box) == 4:
        x, y, w, h = [int(v) for v in box]
        h_img, w_img = img.shape[:2]
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(w_img, x + w), min(h_img, y + h)
        if x2 > x1 and y2 > y1:
            img = img[y1:y2, x1:x2]

    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------------------
# Scoring — pure, deterministic, explainable
# ---------------------------------------------------------------------------

def cleanliness_goodness(noise_sigma: float) -> float:
    """noise_sigma was measured BEFORE denoising. Higher = grainier = worse.

    Clean scans here are ~1-5, heavily noisy ones 20+. Map to 0-100 goodness.
    """
    return float(np.clip(100.0 - noise_sigma * 4.0, 0.0, 100.0))


def brightness_goodness(brightness: float) -> float:
    """Both too-dark AND too-bright are bad, so this is a deviation penalty.

    A comfortable mid-tone band (45-70 on the 0-100 brightness score) is ideal;
    goodness falls off linearly outside it.
    """
    if 45.0 <= brightness <= 70.0:
        return 100.0
    if brightness < 45.0:
        return max(0.0, 100.0 - (45.0 - brightness) * 2.0)
    return max(0.0, 100.0 - (brightness - 70.0) * 2.0)


def compute_condition(metadata: dict[str, Any]) -> tuple[int, dict[str, float]]:
    """Return (condition_score 0-100, sub-scores) from the Module 1-3 metrics."""
    quality = metadata.get("quality", {})
    fading_block = metadata.get("fading_analysis", {})
    analysis = metadata.get("analysis", {})

    # NOTE: the JSON key is "blur" (the blur->sharpness rename was left
    # half-done in Module 2); it holds the sharpness score. Higher = sharper.
    sharpness = float(quality.get("blur", 0))
    brightness = float(quality.get("brightness", 0))
    contrast = float(quality.get("contrast", 0))
    fading = fading_block.get("fading", "Severe")
    noise_sigma = float(analysis.get("noise_sigma", 0))

    subscores = {
        "fading":      float(FADING_GOODNESS.get(fading, 40)),
        "cleanliness": cleanliness_goodness(noise_sigma),
        "contrast":    min(contrast, 100.0),
        "brightness":  brightness_goodness(brightness),
        "sharpness":   min(sharpness, 100.0),
    }

    score = sum(subscores[key] * WEIGHTS[key] for key in WEIGHTS)
    return int(round(score)), subscores


def condition_label(score: int) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 65:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Poor"


def priority_from_score(score: int) -> str:
    """Worse condition => higher restoration priority."""
    if score < 50:
        return "High"
    if score < 70:
        return "Medium"
    return "Low"


def collect_issues(metadata: dict[str, Any], subscores: dict[str, float]) -> list[str]:
    """Human-readable defect list (the 'Issues: x, y' line on the card)."""
    issues: list[str] = []
    quality = metadata.get("quality", {})
    analysis = metadata.get("analysis", {})
    fading = metadata.get("fading_analysis", {}).get("fading", "")

    if fading in ("Moderate", "Severe"):
        issues.append(f"{fading} fading")
    if subscores["sharpness"] < 30:
        issues.append("Blur / soft detail")
    if subscores["contrast"] < 35:
        issues.append("Low contrast / washed out")

    brightness = quality.get("brightness", 50)
    if brightness < 35:
        issues.append("Too dark")
    elif brightness > 80:
        issues.append("Too bright / over-exposed")

    if analysis.get("noise_sigma", 0) > 12:
        issues.append("Heavy noise / grain")

    if not issues:
        issues.append("No major issues")
    return issues


# ---------------------------------------------------------------------------
# Narrative — hybrid (Claude if a key is present, template otherwise)
# ---------------------------------------------------------------------------

_DOTENV_LOADED = False


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a local .env into os.environ (once, no override).

    Tiny stdlib parser so we don't need the python-dotenv package. Looks in the
    current working directory first, then next to this module.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        break


def _call_openrouter(prompt: str, api_key: str, model: str,
                     timeout: int = 60, retries: int = 3) -> str:
    """Call an OpenRouter (OpenAI-compatible) chat model using only stdlib.

    Retries transient failures (timeouts, 429 rate limits, 5xx, empty content)
    with a short backoff, so a slow/free model doesn't fall back to the template
    unnecessarily.
    """
    import time
    import urllib.error
    import urllib.request

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Archive Photo Assistant",
    }

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(OPENROUTER_URL, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            if content:
                return content
            last_error = RuntimeError("empty content from model")
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in (429,) and not (500 <= error.code < 600):
                raise  # genuine client error (bad key, bad model) -> don't retry
        except Exception as error:  # timeout, connection reset, JSON error, etc.
            last_error = error
        time.sleep(1.5 * (attempt + 1))  # backoff before the next attempt

    raise last_error if last_error else RuntimeError("OpenRouter call failed")


def build_narrative(
    image_id: str,
    score: int,
    label: str,
    priority: str,
    issues: list[str],
    tags: list[str],
    is_grayscale: bool,
    use_llm: bool = True,
) -> tuple[str, str]:
    """Return (narrative_text, source) where source is 'openrouter:<model>' or 'template'."""
    template = (
        f"Condition assessed as {label} ({score}/100). "
        f"Main issues: {', '.join(issues)}. "
        f"Restoration priority: {priority}."
    )

    if not use_llm:
        return template, "template"

    _load_dotenv()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return template, "template"

    model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
    prompt = (
        "You help a history/museum archivist triage old photographs for "
        "restoration. Based ONLY on these measured image signals (do NOT guess "
        "what the photo depicts or invent historical facts):\n"
        f"- Condition score: {score}/100 ({label})\n"
        f"- Restoration priority: {priority}\n"
        f"- Detected issues: {', '.join(issues)}\n"
        f"- Black & white scan: {is_grayscale}\n"
        f"- Auto-generated tags (UNCERTAIN, may be wrong): {', '.join(tags) or 'none'}\n\n"
        "Write 2-3 SHORT plain-English sentences (under 50 words total) for a "
        "non-technical archivist: what condition the scan is in, which defects "
        "matter most, and what to do next. Stay grounded in the signals above "
        "and avoid describing content you cannot verify."
    )

    try:
        text = _call_openrouter(prompt, api_key, model)
        return (text or template), (f"openrouter:{model}" if text else "template")
    except Exception:
        # Network down, bad key, unknown model, rate limit, etc. -> demo keeps working.
        return template, "template"


# ---------------------------------------------------------------------------
# Visual 1 — per-photo report card
# ---------------------------------------------------------------------------

# --- card visual theme -------------------------------------------------------
PAGE_BG = "#eceff3"
CARD_BG = "#ffffff"
CARD_EDGE = "#dfe4ea"
HEADER_BG = "#2c3e50"
TEXT_DARK = "#2c3e50"
TEXT_MUTED = "#8895a7"
TRACK_BG = "#e8ebf0"
WELL_BG = "#f4f6f9"
GRADE_GOOD = "#27ae60"
GRADE_WARN = "#e67e22"
GRADE_BAD = "#c0392b"


def _grade_color(value: float) -> str:
    """Traffic-light colour for a 0-100 quality value."""
    if value >= 65:
        return GRADE_GOOD
    if value >= 40:
        return GRADE_WARN
    return GRADE_BAD


def _draw_gauge(ax, score: int, color: str) -> None:
    """Donut gauge showing the condition score on a square inset axes."""
    ax.set_xlim(-1.3, 1.3)
    ax.set_ylim(-1.3, 1.3)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Wedge((0, 0), 1.0, 0, 360, width=0.34, facecolor=TRACK_BG, edgecolor="none"))
    frac = max(0.0, min(100.0, float(score))) / 100.0
    if frac > 0:
        ax.add_patch(Wedge((0, 0), 1.0, 90 - frac * 360, 90, width=0.34, facecolor=color, edgecolor="none"))
    ax.text(0, 0.10, str(int(score)), ha="center", va="center", fontsize=23, fontweight="bold", color=TEXT_DARK)
    ax.text(0, -0.34, "/ 100", ha="center", va="center", fontsize=9, color=TEXT_MUTED)


def _bar(ax, x0: float, y: float, full_w: float, value: float, color: str, hh: float = 0.0125) -> None:
    """Rounded track + value fill for a 0-100 metric (drawn in 0..1 axes coords)."""
    ax.add_patch(FancyBboxPatch((x0, y - hh), full_w, 2 * hh,
                                boxstyle="round,pad=0,rounding_size=0.012",
                                facecolor=TRACK_BG, edgecolor="none"))
    fill_w = max(0.0, min(100.0, value)) / 100.0 * full_w
    if fill_w > 0:
        ax.add_patch(FancyBboxPatch((x0, y - hh), fill_w, 2 * hh,
                                    boxstyle="round,pad=0,rounding_size=0.012",
                                    facecolor=color, edgecolor="none"))


def _fit_narrative(text: str, max_chars: int = CARD_NARRATIVE_CHARS) -> str:
    """Trim a narrative for display on the card, breaking on a sentence end."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    for sep in (". ", "! ", "? "):
        idx = clipped.rfind(sep)
        if idx > max_chars * 0.5:
            return clipped[:idx + 1]
    return clipped.rsplit(" ", 1)[0] + "…"


def render_card(metadata: dict[str, Any], report: dict[str, Any], out_dir: str | Path,
                metadata_path: str | Path | None = None) -> Path:
    """Render the report card PNG and return its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    image_id = metadata["image_id"]
    quality = metadata.get("quality", {})
    fading = metadata.get("fading_analysis", {}).get("fading", "Unknown")
    img = load_display_image(metadata, metadata_path)

    score = report["condition_score"]
    label = report["condition_label"]
    priority = report["priority"]
    pcolor = PRIORITY_COLOR.get(priority, "#333333")

    fig = plt.figure(figsize=(7.5, 10.0), dpi=120)
    fig.patch.set_facecolor(PAGE_BG)
    bg = fig.add_axes([0, 0, 1, 1])
    bg.set_xlim(0, 1)
    bg.set_ylim(0, 1)
    bg.axis("off")

    # card body + header band
    bg.add_patch(FancyBboxPatch((0.05, 0.03), 0.90, 0.94,
                                boxstyle="round,pad=0,rounding_size=0.012",
                                facecolor=CARD_BG, edgecolor=CARD_EDGE, linewidth=1.2))
    bg.add_patch(Rectangle((0.05, 0.90), 0.90, 0.07, facecolor=HEADER_BG, edgecolor="none"))
    bg.text(0.085, 0.946, "ARCHIVE PHOTO — CONDITION REPORT",
            fontsize=14, fontweight="bold", color="white", va="center")
    bg.text(0.085, 0.917, image_id, fontsize=9, color="#cdd6e0", va="center")

    # photo well + photo
    bg.add_patch(FancyBboxPatch((0.09, 0.605), 0.82, 0.275,
                                boxstyle="round,pad=0,rounding_size=0.01",
                                facecolor=WELL_BG, edgecolor=CARD_EDGE, linewidth=1))
    photo_ax = fig.add_axes([0.105, 0.62, 0.79, 0.245])
    photo_ax.axis("off")
    if img is not None:
        photo_ax.imshow(img)
    else:
        photo_ax.text(0.5, 0.5, "(image unavailable)", ha="center", va="center", color=TEXT_MUTED)

    # verdict: donut gauge + label + priority pill
    gauge_ax = fig.add_axes([0.09, 0.45, 0.16, 0.12])
    _draw_gauge(gauge_ax, score, pcolor)
    bg.text(0.30, 0.545, label.upper(), fontsize=20, fontweight="bold", color=pcolor, va="center")
    bg.text(0.30, 0.505, "condition score", fontsize=9.5, color=TEXT_MUTED, va="center")
    bg.text(0.30, 0.455, f"  {priority.upper()} RESTORATION PRIORITY  ",
            fontsize=9, fontweight="bold", color="white", va="center",
            bbox=dict(boxstyle="round,pad=0.45", facecolor=pcolor, edgecolor="none"))

    # quality metrics (traffic-light bars)
    bg.text(0.09, 0.405, "QUALITY", fontsize=9, fontweight="bold", color=TEXT_MUTED, va="center")
    y = 0.365
    for name, value in (("Sharpness", quality.get("blur", 0)),
                        ("Brightness", quality.get("brightness", 0)),
                        ("Contrast", quality.get("contrast", 0))):
        bg.text(0.09, y, name, fontsize=10, color=TEXT_DARK, va="center")
        _bar(bg, 0.31, y, 0.52, value, _grade_color(value))
        bg.text(0.855, y, f"{int(value)}", fontsize=9, fontweight="bold", color=TEXT_DARK, va="center")
        y -= 0.045

    # fading chip
    bg.text(0.09, y, "Fading", fontsize=10, color=TEXT_DARK, va="center")
    bg.text(0.31, y, f"  {fading}  ", fontsize=9, fontweight="bold", color="white", va="center",
            bbox=dict(boxstyle="round,pad=0.4", facecolor=FADING_COLOR.get(fading, "#555555"), edgecolor="none"))

    # issues
    issues = report.get("issues", [])
    bg.text(0.09, 0.205, "Issues:", fontsize=9.5, fontweight="bold", color=TEXT_DARK, va="center")
    bg.text(0.205, 0.205, "   ·   ".join(issues), fontsize=9.5, color="#56606e", va="center")

    # narrative box
    bg.add_patch(FancyBboxPatch((0.09, 0.07), 0.82, 0.115,
                                boxstyle="round,pad=0,rounding_size=0.01",
                                facecolor="#f5f7fa", edgecolor=CARD_EDGE, linewidth=1))
    wrapped = "\n".join(textwrap.wrap(_fit_narrative(report["narrative"]), width=84))
    bg.text(0.105, 0.173, wrapped, fontsize=8.8, color=TEXT_DARK, va="top")

    # footer disclaimer
    bg.text(0.09, 0.047,
            "Automated screening from image metrics — not a substitute for expert inspection.",
            fontsize=7, color=TEXT_MUTED, style="italic", va="center")

    path = out_dir / f"{image_id}_card.png"
    fig.savefig(path, dpi=120, facecolor=PAGE_BG)
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Visual 2 — whole-archive ranking dashboard (/rank)
# ---------------------------------------------------------------------------

def generate_dashboard(metadata_list: list[dict[str, Any]], out_dir: str | Path = REPORTS_DIR,
                       top_n: int = 10) -> Path | None:
    """Render the archive-wide restoration-priority dashboard PNG."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = [(m["image_id"], m["report"]) for m in metadata_list
            if isinstance(m, dict) and "report" in m]
    if not rows:
        return None

    counts = {"High": 0, "Medium": 0, "Low": 0}
    for _, report in rows:
        counts[report.get("priority", "Low")] = counts.get(report.get("priority", "Low"), 0) + 1

    ranked = sorted(rows, key=lambda r: r[1].get("condition_score", 100))  # worst first
    top = ranked[:top_n]

    fig = plt.figure(figsize=(11, 6))
    grid = fig.add_gridspec(1, 2, width_ratios=[1, 1.3], wspace=0.3)

    # --- priority counts ---
    ax_bar = fig.add_subplot(grid[0])
    order = ["High", "Medium", "Low"]
    ax_bar.bar(order, [counts[k] for k in order], color=[PRIORITY_COLOR[k] for k in order])
    ax_bar.set_title(f"Restoration priority — {len(rows)} photos", fontweight="bold")
    ax_bar.set_ylabel("number of photos")
    for i, key in enumerate(order):
        ax_bar.text(i, counts[key], str(counts[key]), ha="center", va="bottom", fontweight="bold")
    for spine in ("top", "right"):
        ax_bar.spines[spine].set_visible(False)

    # --- top-N to restore first ---
    ax_tbl = fig.add_subplot(grid[1])
    ax_tbl.axis("off")
    ax_tbl.set_title(f"Top {len(top)} to restore first", fontweight="bold", loc="left")
    cell_text = [
        [str(i + 1), image_id[:30], str(report.get("condition_score")), report.get("priority")]
        for i, (image_id, report) in enumerate(top)
    ]
    table = ax_tbl.table(cellText=cell_text, colLabels=["#", "Photo", "Score", "Priority"],
                         colWidths=[0.07, 0.58, 0.15, 0.20], loc="center", cellLoc="left")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.5)
    # Colour the priority cells to reinforce the High=red message.
    for row, (_, report) in enumerate(top, start=1):
        cell = table[row, 3]
        cell.get_text().set_color(PRIORITY_COLOR.get(report.get("priority"), "#333333"))
        cell.get_text().set_fontweight("bold")

    path = out_dir / "dashboard.png"
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(metadata: dict[str, Any], out_dir: str | Path = REPORTS_DIR,
                 render: bool = True, use_llm: bool = True,
                 metadata_path: str | Path | None = None) -> dict[str, Any]:
    """Compute the full report dict for one photo (and optionally render its card)."""
    score, subscores = compute_condition(metadata)
    label = condition_label(score)
    priority = priority_from_score(score)
    issues = collect_issues(metadata, subscores)
    tags = metadata.get("fading_analysis", {}).get("tags", [])
    is_grayscale = bool(metadata.get("analysis", {}).get("is_grayscale", False))

    narrative, source = build_narrative(
        metadata["image_id"], score, label, priority, issues, tags, is_grayscale, use_llm=use_llm,
    )

    report: dict[str, Any] = {
        "condition_score": score,
        "condition_label": label,
        "priority": priority,
        "issues": issues,
        "subscores": {k: round(v) for k, v in subscores.items()},
        "narrative": narrative,
        "narrative_source": source,
    }

    if render:
        report["card_path"] = str(render_card(metadata, report, out_dir, metadata_path))

    return report


def generate_report(metadata: dict[str, Any], out_dir: str | Path = REPORTS_DIR,
                    use_llm: bool = True) -> dict[str, Any]:
    """Telegram-friendly entry point: build + render one photo's report.

    Returns the report dict, including ``card_path`` and ``narrative``. The
    Telegram/OpenClaw layer should format these values into one final response.
    """
    return build_report(metadata, out_dir=out_dir, render=True, use_llm=use_llm)


# ---------------------------------------------------------------------------
# Batch over a metadata folder (mirrors Modules 2 & 3)
# ---------------------------------------------------------------------------

def process_metadata_file(metadata_path: str | Path, write_back: bool = True,
                          out_dir: str | Path = REPORTS_DIR, render: bool = True,
                          use_llm: bool = True) -> dict[str, Any]:
    """Read one metadata JSON, add the ``report`` block, optionally save it."""
    metadata_path = Path(metadata_path)
    with open(metadata_path, "r", encoding="utf-8") as file:
        metadata = json.load(file)

    if metadata.get("status") != "ok":
        return metadata

    metadata["report"] = build_report(
        metadata, out_dir=out_dir, render=render, use_llm=use_llm, metadata_path=metadata_path,
    )

    if write_back:
        with open(metadata_path, "w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=2)

    return metadata


def process_metadata_folder(metadata_dir: str | Path = "outputs", write_back: bool = True,
                            out_dir: str | Path = REPORTS_DIR, render: bool = True,
                            use_llm: bool = True, make_dashboard: bool = True) -> list[dict[str, Any]]:
    """Run Module 4 for every metadata JSON file in a folder."""
    metadata_dir = Path(metadata_dir)
    results: list[dict[str, Any]] = []

    for metadata_path in sorted(metadata_dir.glob("*.json")):
        metadata = process_metadata_file(
            metadata_path, write_back=write_back, out_dir=out_dir, render=render, use_llm=use_llm,
        )
        results.append(metadata)

        if metadata.get("status") == "ok":
            report = metadata["report"]
            print(f"[ok]    {metadata['image_id']:<40}  "
                  f"{report['condition_label']:<9} ({report['condition_score']:>3})  "
                  f"-> {report['priority']} priority")
        else:
            print(f"[skip]  {metadata.get('image_id', metadata_path.stem)}")

    if make_dashboard:
        dashboard = generate_dashboard(results, out_dir=out_dir)
        if dashboard:
            print(f"\nDashboard  ->  {dashboard}")

    done = sum(1 for item in results if "report" in item)
    print(f"Done. Reports generated for {done}/{len(results)} images.")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Module 4 — report generation & restoration-priority dashboard."
    )
    parser.add_argument("--meta", default="outputs",
                        help="Metadata JSON file or folder from the pipeline (default: outputs).")
    parser.add_argument("--reports-dir", default=REPORTS_DIR,
                        help=f"Where to write report cards / dashboard (default: {REPORTS_DIR}).")
    parser.add_argument("--no-write", action="store_true",
                        help="Do not write the report block back into the JSON.")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip the Claude narrative; use the rule-based template only.")
    parser.add_argument("--no-render", action="store_true",
                        help="Skip rendering the PNG cards (compute fields only).")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="Skip rendering the archive-wide dashboard.")
    args = parser.parse_args()

    metadata_path = Path(args.meta)
    if metadata_path.is_dir():
        process_metadata_folder(
            metadata_path,
            write_back=not args.no_write,
            out_dir=args.reports_dir,
            render=not args.no_render,
            use_llm=not args.no_llm,
            make_dashboard=not args.no_dashboard,
        )
    else:
        metadata = process_metadata_file(
            metadata_path,
            write_back=not args.no_write,
            out_dir=args.reports_dir,
            render=not args.no_render,
            use_llm=not args.no_llm,
        )
        if metadata.get("status") == "ok":
            report = metadata["report"]
            print(json.dumps({k: report[k] for k in
                              ("condition_score", "condition_label", "priority", "issues",
                               "narrative", "narrative_source")}, indent=2))
            if "card_path" in report:
                print(f"\nCard  ->  {report['card_path']}")
        else:
            print(f"[skip] {metadata.get('image_id', metadata_path.stem)}")


if __name__ == "__main__":
    main()
