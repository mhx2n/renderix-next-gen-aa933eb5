"""Language Tools — /spell /gra /syn /prn
Uses free APIs (datamuse, dictionaryapi.dev) + bundled providers AI fallback.
No paid keys required for basic operation.
"""
from __future__ import annotations
import html
import re

import aiohttp
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from ..providers import REGISTRY

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)


def _arg_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    msg = update.effective_message
    if context.args:
        return " ".join(context.args).strip()
    if msg.reply_to_message and (msg.reply_to_message.text or msg.reply_to_message.caption):
        return (msg.reply_to_message.text or msg.reply_to_message.caption).strip()
    return ""


def _frame(title: str, body: str) -> str:
    return f"<b>{title}</b>\n━━━━━━━━━━━━━━━━━━\n{body}"


async def _ai_answer(prompt: str) -> str | None:
    """Try Gemini → any registered provider for a single-turn answer."""
    for key in ("g", "pr", "co"):
        meta = REGISTRY.get(key)
        if meta:
            try:
                return (await meta[1](prompt, []))[:1500].strip()
            except Exception:
                continue
    for _, (_, fn) in REGISTRY.items():
        try:
            return (await fn(prompt, []))[:1500].strip()
        except Exception:
            continue
    return None


# ---------------- /spell -----------------------------------------------------
async def cmd_spell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    word = _arg_text(update, context)
    if not word:
        await update.effective_message.reply_text(
            "Usage: <code>/spell teh</code>", parse_mode=ParseMode.HTML); return
    target = word.split()[0] if " " not in word.strip() else word
    # datamuse `sp` — spelling suggestions (free, no key)
    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as s:
            async with s.get(f"https://api.datamuse.com/sug?s={target}&max=5") as r:
                data = await r.json(content_type=None)
    except Exception as e:
        data = []
    if data:
        top = data[0].get("word", target)
        alts = ", ".join(d.get("word", "") for d in data[:5])
        body = (f"<b>Input:</b> <code>{html.escape(target)}</code>\n"
                f"<b>Corrected:</b> <code>{html.escape(top)}</code>\n"
                f"<b>Suggestions:</b> <code>{html.escape(alts)}</code>")
    else:
        body = (f"<b>Input:</b> <code>{html.escape(target)}</code>\n"
                f"<i>No suggestions found — word may already be correct.</i>")
    await update.effective_message.reply_text(_frame("Spell Check", body),
                                              parse_mode=ParseMode.HTML)


# ---------------- /gra -------------------------------------------------------
async def cmd_gra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sentence = _arg_text(update, context)
    if not sentence:
        await update.effective_message.reply_text(
            "Usage: <code>/gra I has a book</code>", parse_mode=ParseMode.HTML); return
    msg = await update.effective_message.reply_text(
        _frame("Grammar Fix", "<i>Analysing…</i>"), parse_mode=ParseMode.HTML)
    prompt = (
        "You are a precise English grammar corrector. Rewrite the user's sentence with correct "
        "grammar, punctuation and natural phrasing. Respond with ONLY the corrected sentence, "
        "no quotes, no commentary.\n\nSentence:\n" + sentence
    )
    fixed = await _ai_answer(prompt)
    if not fixed:
        try:
            await msg.edit_text(_frame("Grammar Fix",
                "<i>No AI provider is currently available. Owner must register one.</i>"),
                parse_mode=ParseMode.HTML)
        except Exception: pass
        return
    body = (f"<b>Original:</b>\n<code>{html.escape(sentence)}</code>\n\n"
            f"<b>Corrected:</b>\n<code>{html.escape(fixed)}</code>")
    try:
        await msg.edit_text(_frame("Grammar Fix", body), parse_mode=ParseMode.HTML)
    except Exception:
        await update.effective_message.reply_text(_frame("Grammar Fix", body),
                                                  parse_mode=ParseMode.HTML)


# ---------------- /syn -------------------------------------------------------
async def cmd_syn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    word = _arg_text(update, context)
    if not word:
        await update.effective_message.reply_text(
            "Usage: <code>/syn happy</code>", parse_mode=ParseMode.HTML); return
    target = word.split()[0]
    syns, ants = [], []
    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as s:
            async with s.get(f"https://api.datamuse.com/words?rel_syn={target}&max=12") as r:
                syns = [d.get("word", "") for d in (await r.json(content_type=None))]
            async with s.get(f"https://api.datamuse.com/words?rel_ant={target}&max=12") as r:
                ants = [d.get("word", "") for d in (await r.json(content_type=None))]
    except Exception:
        pass
    syn_t = ", ".join(syns) if syns else "—"
    ant_t = ", ".join(ants) if ants else "—"
    body = (f"<b>Word:</b> <code>{html.escape(target)}</code>\n\n"
            f"<b>Synonyms:</b> <code>{html.escape(syn_t)}</code>\n\n"
            f"<b>Antonyms:</b> <code>{html.escape(ant_t)}</code>")
    await update.effective_message.reply_text(_frame("Synonyms / Antonyms", body),
                                              parse_mode=ParseMode.HTML)


# ---------------- /prn -------------------------------------------------------
async def cmd_prn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    word = _arg_text(update, context)
    if not word:
        await update.effective_message.reply_text(
            "Usage: <code>/prn epitome</code>", parse_mode=ParseMode.HTML); return
    target = word.split()[0]
    phonetic, audio_url, meaning = "", "", ""
    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as s:
            async with s.get(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{target}") as r:
                data = await r.json(content_type=None)
        if isinstance(data, list) and data:
            entry = data[0]
            phonetic = entry.get("phonetic", "") or ""
            for p in entry.get("phonetics", []) or []:
                if not phonetic and p.get("text"):
                    phonetic = p["text"]
                if not audio_url and p.get("audio"):
                    audio_url = p["audio"]
            for m in entry.get("meanings", []) or []:
                defs = m.get("definitions") or []
                if defs:
                    meaning = defs[0].get("definition", "")
                    break
    except Exception:
        pass

    body_lines = [f"<b>Word:</b> <code>{html.escape(target)}</code>"]
    if phonetic:
        body_lines.append(f"<b>Phonetic:</b> <code>{html.escape(phonetic)}</code>")
    if meaning:
        body_lines.append(f"<b>Meaning:</b> {html.escape(meaning)}")
    if not phonetic and not meaning:
        body_lines.append("<i>No pronunciation found for that word.</i>")
    await update.effective_message.reply_text(_frame("Pronunciation", "\n".join(body_lines)),
                                              parse_mode=ParseMode.HTML)
    if audio_url:
        try:
            if audio_url.startswith("//"):
                audio_url = "https:" + audio_url
            await update.effective_message.reply_audio(
                audio=audio_url, title=target, performer="Pronunciation")
        except Exception:
            await update.effective_message.reply_text(
                f"Audio: {audio_url}", disable_web_page_preview=True)


# ---------------- registration ----------------------------------------------
def register(app: Application):
    app.add_handler(CommandHandler("spell", cmd_spell))
    app.add_handler(CommandHandler("gra",   cmd_gra))
    app.add_handler(CommandHandler("syn",   cmd_syn))
    app.add_handler(CommandHandler("prn",   cmd_prn))
