"""
Telegram Bot - Historical Archive Management Assistant
=====================================

Receives photo uploads from Telegram, runs the archive management pipeline, and
sends exactly one final formatted reply back to the user.

Required .env:
    TELEGRAM_BOT_TOKEN=your_bot_token

Run:
    python telegram_bot.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from telegram_formatting import format_telegram_result


PROJECT_ROOT = Path(__file__).resolve().parent
RUNNER = PROJECT_ROOT / "skills/archive-photo-triage/scripts/run_archive_photo_pipeline.py"
TEMP_UPLOAD_DIR = Path(os.environ.get("ARCHIVE_PHOTO_TEMP_DIR", "/tmp/archive_photo_telegram_uploads"))
MAX_BATCH_IMAGES = 20
BATCH_SETTLE_SECONDS = 10.0
TELEGRAM_TEXT_LIMIT = 3900


async def deliver_final_reply(bot, chat_id: int, text: str) -> None:
    """Only Telegram delivery point for request handlers."""
    if len(text) > TELEGRAM_TEXT_LIMIT:
        text = (
            text[: TELEGRAM_TEXT_LIMIT - 95].rstrip()
            + "\n\nResult shortened for Telegram. Full catalogue data was still generated."
        )
    await bot.send_message(chat_id=chat_id, text=text)


def load_dotenv() -> None:
    """Load simple KEY=VALUE lines from .env without adding another dependency."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_token() -> str:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing. Add it to .env first.")
    return token


async def run_pipeline(*args: str) -> dict[str, Any]:
    """Run the local pipeline runner and parse its JSON output."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(RUNNER),
        *args,
        cwd=str(PROJECT_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    output = stdout.decode("utf-8", errors="replace").strip()
    errors = stderr.decode("utf-8", errors="replace").strip()

    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "error": "Pipeline did not return valid JSON.",
            "details": errors or output,
        }

    if process.returncode != 0 and result.get("status") != "error":
        result["status"] = "error"
        result["error"] = errors or "Pipeline failed."

    return result


async def process_pending_batch_after_delay(application, bot, chat_id: int) -> None:
    try:
        await asyncio.sleep(BATCH_SETTLE_SECONDS)
    except asyncio.CancelledError:
        return

    batches = application.bot_data.setdefault("pending_photo_batches", {})
    batch = batches.pop(chat_id, None)
    if not batch:
        return

    paths = [Path(path) for path in batch["paths"]]
    if not paths:
        return

    if len(paths) == 1:
        result = await run_pipeline("--image", str(paths[0]))
        await deliver_final_reply(
            bot,
            chat_id,
            result.get("telegram_message") or format_telegram_result(result, "single"),
        )
        return

    result = await run_pipeline("--images", *[str(path) for path in paths], "--no-llm")
    await deliver_final_reply(
        bot,
        chat_id,
        result.get("telegram_message") or format_telegram_result(result, "batch"),
    )


async def enqueue_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, local_path: Path) -> None:
    message = update.message
    if message is None:
        return

    batches = context.application.bot_data.setdefault("pending_photo_batches", {})
    batch = batches.setdefault(
        message.chat_id,
        {
            "chat_id": message.chat_id,
            "paths": [],
            "task": None,
        },
    )
    batch["paths"].append(str(local_path))

    task = batch.get("task")
    if task and not task.done():
        task.cancel()
    batch["task"] = context.application.create_task(
        process_pending_batch_after_delay(context.application, context.bot, message.chat_id)
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await deliver_final_reply(
        context.bot,
        update.effective_chat.id,
        "Hi! I am your Historical Archive Management Assistant. Send me one "
        "archival photo for a preservation action report, or upload up to 20 "
        "photos at once for a collection catalogue.\n\n"
        f"When you upload several photos, I wait about {int(BATCH_SETTLE_SECONDS)} seconds "
        "after the last image so Telegram has time to deliver the whole set.\n\n"
        "For batches I will show a collection summary, restoration priority "
        "ranking, category breakdown, research-relevant images, metadata "
        "summary, recommended actions, and catalogue note in one final message. "
        "Use /rank after processing photos to update the restoration-priority dashboard."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def rank(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = await run_pipeline("--rank")
    await deliver_final_reply(
        context.bot,
        update.effective_chat.id,
        result.get("telegram_message") or format_telegram_result(result, "rank"),
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or not message.photo:
        return

    TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    local_path = TEMP_UPLOAD_DIR / f"telegram_{message.chat_id}_{uuid4().hex}.jpg"

    photo = message.photo[-1]
    telegram_file = await context.bot.get_file(photo.file_id)
    await telegram_file.download_to_drive(custom_path=str(local_path))
    await enqueue_upload(update, context, local_path)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or message.document is None:
        return

    mime_type = message.document.mime_type or ""
    if not mime_type.startswith("image/"):
        await deliver_final_reply(context.bot, message.chat_id, "Please upload an image file such as JPG or PNG.")
        return

    suffix = Path(message.document.file_name or "uploaded_image.jpg").suffix or ".jpg"
    TEMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    local_path = TEMP_UPLOAD_DIR / f"telegram_{message.chat_id}_{uuid4().hex}{suffix}"

    telegram_file = await context.bot.get_file(message.document.file_id)
    await telegram_file.download_to_drive(custom_path=str(local_path))
    await enqueue_upload(update, context, local_path)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await deliver_final_reply(
        context.bot,
        update.effective_chat.id,
        "Send me one archival photo for a preservation action report, or upload "
        "up to 20 photos together to create a collection catalogue with "
        "restoration priorities, research value, and recommended actions. Use "
        "/rank to generate a dashboard after you process photos."
    )


def main() -> None:
    token = get_token()
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("rank", rank))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Historical Archive Management Telegram bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
