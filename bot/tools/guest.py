"""Guest AI Bot / @mention handler.

Triggers on explicit @<bot_username> mentions.
Guest reply-chains are stored under a dedicated "guest" session key so they
never collide with command-based AI modes like /co or .co.
"""
from __future__ import annotations

import asyncio
import json
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
    "Copilot, responding inside a Telegram chat. "
    "Always reply in the same language the user used. "
    "Use clear structure, no fluff, no apologies, no self-introduction "
    "unless asked. Prefer short paragraphs and bullet points where helpful. "
    "Keep answers under ~1500 characters when possible.\n\n"
    "User message:\n"
)

_EDIT_MIN_INTERVAL = 1.4
_MAX_LEN = 3800
_PLACEHOLDER = "✨ thinking…"


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


async def _safe_send(msg, context: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        return await msg.reply_text(
            text[:_MAX_LEN],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_to_message_id=msg.message_id,
            allow_sending_without_reply=True,
        )
    except Exception:
        try:
            return await msg.reply_text(
                text[:_MAX_LEN],
                disable_web_page_preview=True,
                reply_to_message_id=msg.message_id,
                allow_sending_without_reply=True,
            )
        except Exception:
            try:
                return await context.bot.send_message(
                    chat_id=msg.chat_id,
                    text=text[:_MAX_LEN],
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except Exception:
                try:
                    return await context.bot.send_message(
                        chat_id=msg.chat_id,
                        text=text[:_MAX_LEN],
                        disable_web_page_preview=True,
                    )
                except Exception:
                    return None


async def _load_guest_history(chat_id: int, reply_message_id: int | None):
    if not reply_message_id:
        return [], None
    try:
        sess = await db.get_session(chat_id, reply_message_id)
    except Exception:
        sess = None
    if not sess or sess[0] != "guest":
        return [], None
    try:
        history = json.loads(sess[1]) if sess[1] else []
    except Exception:
        history = []
    return history, reply_message_id


async def _run_guest(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    history: list | None = None,
    root_id: int | None = None,
):
    msg = update.effective_message
    chat = update.effective_chat
    sender = msg.from_user if msg else None
    if not msg or not chat:
        return False

    try:
        if sender and await db.is_banned(sender.id):
            return False
    except Exception:
        pass

    if not prompt.strip():
        prompt = "Hello"

    try:
        await context.bot.send_chat_action(chat.id, ChatAction.TYPING)
    except Exception:
        pass

    placeholder = await _safe_send(msg, context, _PLACEHOLDER)
    sent_message = placeholder

    last_edit = 0.0
    last_sent = ""
    final_text = ""
    try:
        async for partial in copilot.ask_stream(SYSTEM_PREFIX + prompt, history or []):
            final_text = partial
            now = time.monotonic()
            if not placeholder:
                continue
            if now - last_edit < _EDIT_MIN_INTERVAL:
                continue
            preview = format_ai_answer(partial) + " ▍"
            if preview == last_sent:
                continue
            last_sent = preview
            last_edit = now
            await _safe_edit(placeholder, preview)
    except asyncio.TimeoutError:
        try:
            final_text = await asyncio.wait_for(
                copilot.ask(SYSTEM_PREFIX + prompt, history or []), timeout=60
            )
        except Exception:
            if placeholder:
                await _safe_edit(placeholder, "⏱ Copilot timed out. Please try again.")
            else:
                await _safe_send(msg, context, "⏱ Copilot timed out. Please try again.")
            return False
    except Exception as e:
        try:
            final_text = await asyncio.wait_for(
                copilot.ask(SYSTEM_PREFIX + prompt, history or []), timeout=60
            )
        except Exception:
            if placeholder:
                await _safe_edit(
                    placeholder,
                    "Copilot is temporarily unavailable. Please try again shortly.",
                )
            else:
                await _safe_send(
                    msg,
                    context,
                    "Copilot is temporarily unavailable. Please try again shortly.",
                )
            try:
                await db.log("ERROR", sender.id if sender else 0, "guest", str(e)[:400])
            except Exception:
                pass
            return False

    if not final_text:
        try:
            final_text = await asyncio.wait_for(
                copilot.ask(SYSTEM_PREFIX + prompt, history or []), timeout=60
            )
        except Exception:
            if placeholder:
                await _safe_edit(placeholder, "Copilot returned no content.")
            else:
                await _safe_send(msg, context, "Copilot returned no content.")
            return False

    body = format_ai_answer(final_text)
    if len(body) <= _MAX_LEN:
        if placeholder:
            await _safe_edit(placeholder, body)
            sent_message = placeholder
        else:
            sent_message = await _safe_send(msg, context, body)
    else:
        if placeholder:
            await _safe_edit(placeholder, body[:_MAX_LEN])
            sent_message = placeholder
        else:
            sent_message = await _safe_send(msg, context, body[:_MAX_LEN])
        for i in range(_MAX_LEN, len(body), _MAX_LEN):
            try:
                await _safe_send(msg, context, body[i:i + _MAX_LEN])
            except Exception:
                break

    try:
        if sender:
            await db.log("INFO", sender.id, "guest", prompt[:200])
    except Exception:
        pass

    try:
        if sent_message:
            hist = list(history or [])
            hist.append({"q": prompt, "a": (final_text or "")[:4000]})
            hist = hist[-10:]
            state = json.dumps(hist)
            new_root = root_id or sent_message.message_id
            await db.save_session(chat.id, new_root, "guest", state)
            if sent_message.message_id != new_root:
                await db.save_session(chat.id, sent_message.message_id, "guest", state)
    except Exception:
        pass

    return True


async def continue_session(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return False
    rep = msg.reply_to_message
    history, root_id = await _load_guest_history(chat.id, rep.message_id if rep else None)
    await _run_guest(update, context, text.strip(), history=history, root_id=root_id)
    return True


async def _handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return
    text = (msg.text or msg.caption or "").strip()
    if not text or text.startswith(("/", ".")):
        return

    sender = msg.from_user
    if sender and sender.id == context.bot.id:
        return
    # Block bot-to-bot auto-replies.
    if sender and sender.is_bot:
        return

    bot_user = (context.bot.username or "").lstrip("@")
    chat = update.effective_chat
    if not chat:
        return
    rep = msg.reply_to_message
    mentioned = _bot_mentioned(text, bot_user)
    replied_to_bot = bool(rep and rep.from_user and rep.from_user.id == context.bot.id)
    replied_to_other_bot = bool(rep and rep.from_user and rep.from_user.is_bot and rep.from_user.id != context.bot.id)

    # Guest mode triggers ONLY on explicit @mention.
    # Replying to our own bot's AI message must fall through to the
    # normal on_text handler (inbox-style conversational AI),
    # not be hijacked by the guest/Copilot handler.
    if not mentioned:
        return
    # Never auto-reply to another bot's message.
    if replied_to_other_bot:
        return

    prompt = _strip_mention(text, bot_user) if mentioned else text
    if rep and not replied_to_bot:
        rep_text = (rep.text or rep.caption or "").strip()
        if rep_text:
            prompt = f"[Replied message]:\n{rep_text[:1500]}\n\n[Question]:\n{prompt}"
    history, root_id = await _load_guest_history(chat.id, rep.message_id if replied_to_bot else None)
    await _run_guest(update, context, prompt, history=history, root_id=root_id)

    raise ApplicationHandlerStop


def register(app: Application):
    flt = (filters.TEXT | filters.CAPTION) & ~filters.COMMAND
    app.add_handler(MessageHandler(flt, _handle), group=-2)