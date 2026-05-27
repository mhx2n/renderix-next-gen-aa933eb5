"""OCR tool — /ocr (reply to a photo or image document).
Shares key + daily-limit infra with /tr.
"""
from __future__ import annotations

import io
from typing import Optional

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from . import _mistral
from ..config import OWNER_ID

MAX_BYTES = 10 * 1024 * 1024  # 10 MB cap
MAX_OUT_CHARS = 3500  # Telegram message safety; longer goes as a file


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_owner(uid: int) -> bool:
    return uid == OWNER_ID


async def _get_image(update: Update, context: ContextTypes.DEFAULT_TYPE
                     ) -> tuple[Optional[bytes], str]:
    """Pull image bytes + mime from the replied message or current message."""
    msg = update.effective_message
    src = msg.reply_to_message or msg
    file_id = None
    mime = "image/jpeg"

    if src.photo:
        file_id = src.photo[-1].file_id
        mime = "image/jpeg"
    elif src.document and (src.document.mime_type or "").startswith("image/"):
        if src.document.file_size and src.document.file_size > MAX_BYTES:
            return None, ""
        file_id = src.document.file_id
        mime = src.document.mime_type or "image/jpeg"
    if not file_id:
        return None, ""

    f = await context.bot.get_file(file_id)
    buf = io.BytesIO()
    await f.download_to_memory(buf)
    data = buf.getvalue()
    if len(data) > MAX_BYTES:
        return None, ""
    return data, mime


def _parse_target(raw: str) -> Optional[str]:
    """Optional language argument: /ocr en  → translate result to English."""
    from .translate import LANG_NAMES
    parts = raw.split(None, 2)
    if len(parts) < 2:
        return None
    code = parts[1].strip().lower().lstrip("/")
    if code in LANG_NAMES:
        return LANG_NAMES[code]
    if len(code) <= 24 and code.replace("-", "").replace("_", "").isalpha():
        return code.capitalize()
    return None


async def cmd_ocr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id

    # Key check first
    key = await _mistral.get_key()
    if not key:
        await msg.reply_text(
            "OCR is currently unavailable.\n"
            "The bot owner has not configured the AI engine yet."
        )
        return

    # Image required
    img_bytes, mime = await _get_image(update, context)
    if not img_bytes:
        await msg.reply_text(
            "Reply to a photo or image document with /ocr.\n\n"
            "Tips:\n"
            "• Use a clear, well-lit image\n"
            "• Avoid blurry / distorted text\n"
            "• Max 10 MB\n\n"
            "Optional: <code>/ocr &lt;lang&gt;</code> to also translate the extracted text.\n"
            "Example: <code>/ocr en</code> (reply to a Bangla image).",
            parse_mode=ParseMode.HTML,
        )
        return

    # Quota — owner unlimited
    if not _is_owner(uid):
        ok, used, limit = await _mistral.check_quota(uid, "ocr")
        if not ok:
            await msg.reply_text(
                f"Daily OCR limit reached ({limit}/day).\n"
                "Please try again tomorrow."
            )
            return

    target_lang = _parse_target(msg.text or "")
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    placeholder = await msg.reply_text("Extracting text from image...")

    prompt = (
        "You are a high-accuracy OCR engine. Extract ALL visible text from this image "
        "exactly as it appears. Preserve the original language, line breaks, lists, "
        "punctuation, numbers and order. Do NOT translate. Do NOT add commentary, "
        "explanations, headings, or quotation marks. If the image has no readable text, "
        "reply with exactly: NO_TEXT_FOUND"
    )

    try:
        raw = await _mistral.vision_extract(img_bytes, prompt, mime=mime, timeout=120)
        raw = (raw or "").strip()
        if not raw or raw.upper().startswith("NO_TEXT_FOUND"):
            await placeholder.edit_text(
                "No readable text was found in this image.\n"
                "Try a clearer / higher-resolution photo."
            )
            return

        translated = ""
        if target_lang:
            try:
                await placeholder.edit_text("Text extracted. Translating...")
            except Exception:
                pass
            try:
                translated = await _mistral.chat(
                    messages=[
                        {"role": "system",
                         "content": f"You are a professional translator. Translate the user's text into {target_lang}. "
                                    "Preserve meaning and formatting. Reply with the translation only."},
                        {"role": "user", "content": raw},
                    ],
                    max_tokens=1800, temperature=0.2, timeout=60,
                )
                translated = (translated or "").strip()
            except Exception:
                translated = "[translation unavailable]"

        # Build output
        if translated:
            body = (
                f"<b>OCR — Extracted</b>\n<pre>{_esc(raw)}</pre>\n\n"
                f"<b>Translation</b> → <i>{_esc(target_lang)}</i>\n<pre>{_esc(translated)}</pre>"
            )
        else:
            body = f"<b>OCR — Extracted Text</b>\n<pre>{_esc(raw)}</pre>"

        # Long output -> send as .txt file
        if len(body) > MAX_OUT_CHARS:
            try:
                await placeholder.delete()
            except Exception:
                pass
            doc = io.BytesIO()
            content = raw
            if translated:
                content += f"\n\n--- Translation ({target_lang}) ---\n{translated}"
            doc.write(content.encode("utf-8"))
            doc.seek(0)
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=doc, filename="ocr.txt",
                caption="Extracted text (too long for a message).",
                reply_to_message_id=msg.message_id,
            )
            return

        try:
            await placeholder.edit_text(body, parse_mode=ParseMode.HTML,
                                        disable_web_page_preview=True)
        except Exception:
            await placeholder.edit_text(raw)
    except Exception:
        try:
            await placeholder.edit_text(safe_user_error("OCR"))
        except Exception:
            pass


def register(app: Application):
    app.add_handler(CommandHandler("ocr", cmd_ocr))
