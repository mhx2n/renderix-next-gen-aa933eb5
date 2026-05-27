"""Translation tool — /tr [lang] <text or reply>.
Owner manages key via /mkey and daily user limit via /mlimit.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ChatAction

from . import _mistral
from ..config import OWNER_ID
from .. import db
from ..utils import safe_user_error


# ISO-ish language codes the bot understands explicitly. Anything else is passed
# through to the model verbatim ("translate to <lang>").
LANG_NAMES = {
    "en": "English", "bn": "Bengali", "hi": "Hindi", "ur": "Urdu",
    "ar": "Arabic", "fr": "French", "es": "Spanish", "de": "German",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "zh": "Chinese (Simplified)",
    "ja": "Japanese", "ko": "Korean", "tr": "Turkish", "id": "Indonesian",
    "ms": "Malay", "vi": "Vietnamese", "th": "Thai", "fa": "Persian",
    "ta": "Tamil", "te": "Telugu", "ml": "Malayalam", "mr": "Marathi",
    "gu": "Gujarati", "pa": "Punjabi", "ne": "Nepali", "si": "Sinhala",
    "my": "Burmese", "uk": "Ukrainian", "pl": "Polish", "nl": "Dutch",
    "sv": "Swedish", "fi": "Finnish", "no": "Norwegian", "da": "Danish",
    "cs": "Czech", "el": "Greek", "he": "Hebrew", "ro": "Romanian",
    "hu": "Hungarian", "bg": "Bulgarian",
}


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_owner(uid: int) -> bool:
    return uid == OWNER_ID


def _parse(raw: str, reply_text: str | None) -> tuple[str, str]:
    """Returns (target_lang_name, text). Default target = English (or Bengali if input looks English)."""
    parts = raw.split(None, 2)
    args = parts[1:] if len(parts) > 1 else []
    target = "auto"
    text = ""

    if args:
        first = args[0].strip().lower().lstrip("/")
        if first in LANG_NAMES:
            target = LANG_NAMES[first]
            text = args[1] if len(args) > 1 else ""
        elif len(first) <= 24 and first.replace("-", "").replace("_", "").isalpha() and len(args) > 1:
            # arbitrary language name like "spanish" or "swahili"
            target = first.capitalize()
            text = args[1]
        else:
            text = " ".join(args)
    if not text.strip() and reply_text:
        text = reply_text.strip()
    return target, text.strip()


async def cmd_tr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    rep = msg.reply_to_message
    reply_text = None
    if rep:
        reply_text = rep.text or rep.caption
    target, text = _parse(msg.text or "", reply_text)
    if not text:
        await msg.reply_text(
            "Usage: <code>/tr [lang] &lt;text&gt;</code>  or reply to a message with <code>/tr [lang]</code>\n\n"
            "Examples:\n"
            "• <code>/tr Hello world</code>  (auto → English/Bengali)\n"
            "• <code>/tr bn How are you?</code>\n"
            "• <code>/tr fr Good morning</code>\n"
            "• Reply to any message with <code>/tr es</code>\n\n"
            "Codes: en, bn, hi, ur, ar, fr, es, de, it, pt, ru, zh, ja, ko, tr, id, fa, ta, te + more.",
            parse_mode="HTML",
        )
        return

    if len(text) > 4000:
        await msg.reply_text("Text too long (max 4000 chars).")
        return

    uid = update.effective_user.id
    key = await _mistral.get_key()
    if not key:
        await msg.reply_text(
            "Translation is currently unavailable.\n"
            "The bot owner has not configured the AI engine yet."
        )
        return

    # quota — owner unlimited
    if not _is_owner(uid):
        ok, used, limit = await _mistral.check_quota(uid, "tr")
        if not ok:
            await msg.reply_text(
                f"Daily translation limit reached ({limit}/day).\n"
                "Please try again tomorrow."
            )
            return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    placeholder = await msg.reply_text("Translating...")

    if target == "auto":
        sys_prompt = (
            "You are a professional translator. Detect the input language. "
            "If it is English, translate to Bengali. Otherwise translate to English. "
            "Reply with the translation ONLY — no explanations, no quotes, no labels."
        )
    else:
        sys_prompt = (
            f"You are a professional translator. Translate the user's text into {target}. "
            "Preserve meaning, tone, names, numbers, and formatting. "
            "Reply with the translation ONLY — no explanations, no quotes, no labels."
        )

    try:
        out = await _mistral.chat(
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": text},
            ],
            max_tokens=1500,
            temperature=0.2,
            timeout=60,
        )
        out = (out or "").strip()
        if not out:
            await placeholder.edit_text("Translation returned empty.")
            return
        header = f"<b>Translation</b>"
        if target != "auto":
            header += f"  →  <i>{_esc(target)}</i>"
        body = f"{header}\n\n{_esc(out)}"
        try:
            await placeholder.edit_text(body, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            await placeholder.edit_text(out)
    except Exception:
        try:
            await placeholder.edit_text(safe_user_error("Translation"))
        except Exception:
            pass


# ---------- owner commands shared with OCR ----------
async def cmd_mkey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update.effective_user.id):
        await update.effective_message.reply_text("Owner only.")
        return
    args = context.args
    if not args:
        cur = await _mistral.get_key()
        masked = (cur[:4] + "…" + cur[-4:]) if len(cur) >= 10 else ("set" if cur else "not set")
        await update.effective_message.reply_text(
            f"AI engine key: <code>{masked}</code>\n\n"
            "Set:    <code>/mkey &lt;API_KEY&gt;</code>\n"
            "Clear:  <code>/mkey clear</code>",
            parse_mode="HTML",
        )
        return
    val = args[0].strip()
    if val.lower() in ("clear", "remove", "delete", "off", "none"):
        await _mistral.set_key("")
        await update.effective_message.reply_text("AI engine key cleared. /tr and /ocr are now disabled.")
        return
    await _mistral.set_key(val)
    # delete the message that contained the key for safety
    try:
        await update.effective_message.delete()
    except Exception:
        pass
    await context.bot.send_message(
        update.effective_chat.id,
        "AI engine key saved. /tr and /ocr are live.",
    )


async def cmd_mlimit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update.effective_user.id):
        await update.effective_message.reply_text("Owner only.")
        return
    args = context.args
    if not args:
        lim = await _mistral.get_daily_limit()
        await update.effective_message.reply_text(
            f"AI daily limit per user: <b>{lim}</b>\n"
            "(applies to /tr and /ocr combined per-tool)\n\n"
            "Change: <code>/mlimit &lt;number&gt;</code>  (0 disables for everyone except owner)",
            parse_mode="HTML",
        )
        return
    try:
        n = int(args[0])
        if n < 0 or n > 100000:
            raise ValueError
    except Exception:
        await update.effective_message.reply_text("Usage: /mlimit <0–100000>")
        return
    await _mistral.set_daily_limit(n)
    await update.effective_message.reply_text(f"AI daily limit set to {n}/user/tool.")


def register(app: Application):
    app.add_handler(CommandHandler("tr", cmd_tr))
    app.add_handler(CommandHandler("mkey", cmd_mkey))
    app.add_handler(CommandHandler("mlimit", cmd_mlimit))
