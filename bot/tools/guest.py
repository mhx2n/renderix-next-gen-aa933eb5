"""Guest AI Bot / Bot-to-Bot / @mention handler.

Implements the May 2026 Telegram platform features:
  • Mentioning @<bot_username> in ANY chat triggers a reply, even when the
    bot is not a member of the chat.
  • The bot may reply to messages from other bots (bot-to-bot automation).
  • Replies stream in (animated) as Copilot generates them.

Only the Microsoft Copilot provider is used here, and the system prompt
forces a professional, concise answering style.
"""
from __future__ import annotations

import asyncio
import html
import re
import time

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application, MessageHandler, ContextTypes, filters,
    ApplicationHandlerStop,
)

from ..providers import copilot
from ..utils import format_ai_answer
from .. import db


SYSTEM_PREFIX = (
    "You are a professional, concise AI assistant powered by Microsoft "
    "Copilot, responding inside a Telegram chat as a guest bot. "
    "Always reply in the same language the user used. "
    "Use clear structure, no fluff, no apologies, no self-introduction "
    "unless asked. Prefer short paragraphs and bullet points where helpful. "
    "Keep answers under ~1500 characters when possible.\n\n"
    "User message:\n"
)

# Throttle live edits so we never hit Telegram's flood limit while streaming.
_EDIT_MIN_INTERVAL = 1.4  # seconds
_MAX_LEN = 3800


def _bot_mentioned(text: str, username: str) -> bool:
    if not text or not username:
        return False
    return re.search(rf"(?i)(?<!\w)@{re.escape(username)}\b", text) is not None


def _strip_mention(text: str, username: str) -> str:
    return re.sub(rf"(?i)(?<!\w)@{re.escape(username)}\b", "", text).strip()


async def _safe_edit(message, text: str):
    try:
        await message.edit_text(
            text[:_MAX_LEN], parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await message.edit_text(text[:_MAX_LEN], disable_web_page_preview=True)
        except Exception:
            pass


async def _handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    text = (msg.text or msg.caption or "").strip()
    if not text or text.startswith(("/", ".")):
        return

    bot_user = (context.bot.username or "").lstrip("@")
    chat = update.effective_chat
    if not chat:
        return
    rep = msg.reply_to_message
    sender = msg.from_user
    if sender and sender.id == context.bot.id:
        return  # never reply to ourselves
    # Never reply to other bots — avoids bot-to-bot loops in groups where
    # an AI bot (e.g. @QuryaBot) answers and our bot would chain-reply.
    if sender and sender.is_bot:
        return

    is_group = chat.type in ("group", "supergroup")
    mentioned = _bot_mentioned(text, bot_user)
    replied_to_bot = bool(rep and rep.from_user and rep.from_user.id == context.bot.id)

    # Trigger only on explicit guest signals — otherwise let on_text handle it.
    if not (mentioned or (is_group and replied_to_bot)):
        return

    # Banned users (private only — bots have no ban state)
    try:
        if sender and not sender.is_bot and await db.is_banned(sender.id):
            raise ApplicationHandlerStop
    except ApplicationHandlerStop:
        raise
    except Exception:
        pass

    prompt = _strip_mention(text, bot_user) if mentioned else text
    if rep and not replied_to_bot:
        rep_text = (rep.text or rep.caption or "").strip()
        if rep_text:
            prompt = f"[Replied message]:\n{rep_text[:1500]}\n\n[Question]:\n{prompt}"
    if not prompt.strip():
        prompt = "Hello"

    try:
        await context.bot.send_chat_action(chat.id, ChatAction.TYPING)
    except Exception:
        pass

    try:
        placeholder = await msg.reply_text(
            "✨ thinking…",
            reply_to_message_id=msg.message_id,
            allow_sending_without_reply=True,
        )
    except Exception:
        raise ApplicationHandlerStop

    last_edit = 0.0
    last_sent = ""
    final_text = ""
    try:
        async for partial in copilot.ask_stream(SYSTEM_PREFIX + prompt, []):
            final_text = partial
            now = time.monotonic()
            if now - last_edit < _EDIT_MIN_INTERVAL:
                continue
            preview = format_ai_answer(partial) + " ▍"
            if preview == last_sent:
                continue
            last_sent = preview
            last_edit = now
            await _safe_edit(placeholder, preview)
    except asyncio.TimeoutError:
        await _safe_edit(placeholder, "⏱ Copilot timed out. Please try again.")
        raise ApplicationHandlerStop
    except Exception as e:
        await _safe_edit(
            placeholder,
            "Copilot is temporarily unavailable. Please try again shortly.",
        )
        try:
            await db.log("ERROR", sender.id if sender else 0, "guest", str(e)[:400])
        except Exception:
            pass
        raise ApplicationHandlerStop

    if not final_text:
        await _safe_edit(placeholder, "Copilot returned no content.")
        raise ApplicationHandlerStop

    body = format_ai_answer(final_text)
    if len(body) <= _MAX_LEN:
        await _safe_edit(placeholder, body)
    else:
        await _safe_edit(placeholder, body[:_MAX_LEN])
        for i in range(_MAX_LEN, len(body), _MAX_LEN):
            try:
                await msg.reply_text(
                    body[i:i + _MAX_LEN], parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                break

    try:
        if sender and not sender.is_bot:
            await db.log("INFO", sender.id, "guest", prompt[:200])
    except Exception:
        pass

    # Stop the lower-priority on_text handler from processing this same update.
    raise ApplicationHandlerStop


def register(app: Application):
    # group = -2  → runs before on_any_owner_message (-1) and on_text (0),
    # but its filter excludes COMMAND messages so the command gate at -2
    # is unaffected.
    flt = (filters.TEXT | filters.CAPTION) & ~filters.COMMAND
    app.add_handler(MessageHandler(flt, _handle), group=-2)