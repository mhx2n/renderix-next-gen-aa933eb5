import asyncio
import json
import os
import time
import traceback
from collections import defaultdict
from uuid import uuid4

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat,
    InlineQueryResultArticle, InputTextMessageContent,
)
from telegram.constants import ChatAction, ChatMemberStatus, ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, InlineQueryHandler, filters,
)

from . import db, downloader
from .config import OWNER_ID, FORCE_JOIN_CHANNEL
from .providers import REGISTRY, register as register_provider, make_openai_compatible_provider
from .utils import clean_text, format_ai_answer, chunk_text, escape_html, human_size, safe_user_error, process_metrics, format_duration
from .keycheck import inspect_key, try_model
from .tools import textenc as _textenc, language as _language, photo as _photo, shorten as _shorten, stylish as _stylish, translate as _translate, ocr as _ocr


_HISTORY: dict = defaultdict(list)
_PENDING_KEY: dict = {}     # user_id -> last inspected api key
_AWAIT_INPUT: dict = {}     # user_id -> ("key"|"download"|"tryke"|"announce"|"speak_to"|"grant"|"revoke")
_DOWNLOAD_SEM = asyncio.Semaphore(1)  # keep free hosting stable: one download at a time
_DOWNLOAD_QUEUE = 0
_DOWNLOAD_QUEUE_LOCK = asyncio.Lock()
_PROCESS_STARTED_AT = int(time.time())


# ============================================================
# Helpers
# ============================================================
def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


async def force_join_ok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    from . import config as cfg
    channel = cfg.FORCE_JOIN_CHANNEL or FORCE_JOIN_CHANNEL
    if not channel:
        return True
    user = update.effective_user
    if not user or is_owner(user.id):
        return True
    try:
        member = await context.bot.get_chat_member(f"@{channel}", user.id)
        if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.OWNER,
                             ChatMemberStatus.ADMINISTRATOR):
            return True
    except Exception:
        pass
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Join Channel", url=f"https://t.me/{channel}")],
        [InlineKeyboardButton("I have joined", callback_data="verify_join")],
    ])
    await update.effective_message.reply_text(
        "Access restricted. You must join our channel to use this bot.",
        reply_markup=kb,
    )
    return False


async def send_md(target_msg_or_chat, text: str, context=None, **kw):
    """Send a (possibly long) message with safe HTML formatting."""
    text = text or ""
    chunks = list(chunk_text(text))
    first = None
    for c in chunks:
        try:
            m = await target_msg_or_chat.reply_text(
                c, parse_mode=ParseMode.HTML, disable_web_page_preview=True, **kw,
            )
        except Exception:
            m = await target_msg_or_chat.reply_text(
                clean_text(c), disable_web_page_preview=True, **kw,
            )
        first = first or m
    return first


async def safe_edit(message, text: str, reply_markup=None):
    text = text or ""
    chunks = list(chunk_text(text))
    try:
        await message.edit_text(
            chunks[0], parse_mode=ParseMode.HTML,
            disable_web_page_preview=True, reply_markup=reply_markup,
        )
    except Exception:
        try:
            await message.edit_text(
                clean_text(chunks[0]), disable_web_page_preview=True, reply_markup=reply_markup,
            )
        except Exception:
            return
    for extra in chunks[1:]:
        try:
            await message.reply_text(extra, parse_mode=ParseMode.HTML,
                                     disable_web_page_preview=True)
        except Exception:
            await message.reply_text(clean_text(extra), disable_web_page_preview=True)


async def stream_edit(message, text: str, reply_markup=None):
    text = text or ""
    if len(text) < 500:
        await safe_edit(message, text, reply_markup=reply_markup)
        return
    steps = 5
    for i in range(1, steps + 1):
        chunk = text[: max(1, int(len(text) * i / steps))]
        await safe_edit(message, chunk, reply_markup=reply_markup if i == steps else None)
        if i != steps:
            await asyncio.sleep(0.35)


# ============================================================
# Tool catalog (Util-Hub style categorized menu)
# ============================================================
TOOL_CATALOG: dict = {
    "AI Tools": [
        ("g",     "Gemini",       "Chat with Google Gemini.\n\n<b>Usage:</b>\n<code>/g your question</code>  or  <code>.g your question</code>"),
        ("pr",    "Perplexity",   "Chat with Perplexity AI.\n\n<b>Usage:</b>\n<code>/pr your question</code>  or  <code>.pr ...</code>"),
        ("co",    "Copilot",      "Chat with Microsoft Copilot.\n\n<b>Usage:</b>\n<code>/co your question</code>  or  <code>.co ...</code>"),
        ("key",   "API Key Inspector", "Inspect any AI API key (OpenAI, Anthropic, Gemini, Groq, OpenRouter, Cohere, DeepSeek, xAI, Together AI).\n\n<b>Usage:</b>\n<code>/key &lt;API_KEY&gt;</code>"),
        ("tryke", "Try a Model",  "Call any model on the last inspected key.\n\n<b>Usage:</b>\n<code>/tryke &lt;model&gt; &lt;prompt&gt;</code>"),
    ],
    "Text Tools": [
        ("en",    "Encode",       "Encode to Base64 / Hex / Binary / URL / ROT13.\n\n<b>Usage:</b>\n<code>/en base64 Hello World</code>"),
        ("de",    "Decode",       "Decode from any common format.\n\n<b>Usage:</b>\n<code>/de base64 SGVsbG8=</code>"),
        ("text",  "Text Transform","Change case, reverse, etc.\n\n<b>Usage:</b>\n<code>/text upper hello</code>"),
        ("wc",    "Word & Char Count","Count words, characters, lines.\n\n<b>Usage:</b>\n<code>/wc some text</code> or reply to a message."),
        ("style", "Stylish Text", "Transform text into 49+ Unicode fonts. Button labels preview the style — tap one and the result appears in the same message, ready to copy.\n\n<b>Usage:</b>\n<code>/style Your Text Here</code>"),
    ],
    "Language Tools": [
        ("spell", "Spell Check",  "Spelling suggestions.\n\n<b>Usage:</b>\n<code>/spell teh quik fox</code>"),
        ("gra",   "Grammar Fix",  "AI-powered grammar correction.\n\n<b>Usage:</b>\n<code>/gra he go home yesterday</code>"),
        ("syn",   "Synonyms",     "Word alternatives.\n\n<b>Usage:</b>\n<code>/syn happy</code>"),
        ("prn",   "Pronounce",    "Phonetic + audio pronunciation.\n\n<b>Usage:</b>\n<code>/prn pronunciation</code>"),
        ("tr",    "Translate",    "AI-powered translation.\n\n<b>Usage:</b>\n<code>/tr Hello</code> (auto)\n<code>/tr bn Hello</code>\nReply to a message with <code>/tr fr</code>."),
        ("ocr",   "OCR",          "Extract text from an image.\n\n<b>Usage:</b> Reply to a photo with <code>/ocr</code>.\nReply with <code>/ocr en</code> to translate."),
    ],
    "Photo Tools": [
        ("bg",    "Remove BG",    "Remove image background.\n\n<b>Usage:</b> Reply to a photo with <code>/bg</code>"),
        ("enh",   "Enhance",      "Sharpen + colour-boost a photo.\n\n<b>Usage:</b> Reply to a photo with <code>/enh</code>"),
        ("res",   "Resize",       "Resize to popular presets (YouTube, Instagram, Twitter, HD, 4K).\n\n<b>Usage:</b> Reply to a photo with <code>/res</code>, then pick a preset."),
    ],
    "Utilities": [
        ("dl",    "Video Downloader","Download high-quality playable video from Facebook, Instagram, and TikTok only (max 50MB).\n\n<b>Usage:</b> <code>/dl &lt;url&gt;</code>"),
        ("dla",   "Audio Downloader","Extract audio (mp3) from Facebook, Instagram, and TikTok links only.\n\n<b>Usage:</b> <code>/dla &lt;url&gt;</code>"),
        ("short", "URL Shortener","Shorten any URL.\n\n<b>Usage:</b>\n<code>/short https://example.com/path</code>"),
        ("ping",  "Ping",         "Bot latency check.\n\n<b>Usage:</b> <code>/ping</code>"),
        ("help",  "Help / About", "AI-summarised help.\n\n<b>Usage:</b>\n<code>/help</code> or <code>/help &lt;topic&gt;</code>"),
    ],
}


async def _disabled_set() -> set:
    raw = await db.get_setting("disabled_cmds", "")
    return {c.strip() for c in raw.split(",") if c.strip()}


async def _set_disabled(s: set):
    await db.set_setting("disabled_cmds", ",".join(sorted(s)))


# ------- UI customization (owner-editable) -------
DEFAULT_WELCOME = (
    "<b>Welcome, {name}.</b>\n\n"
    "Command-based bot.\n"
    "• AI only works with commands like /g, /pr, /co\n"
    "• Downloader only supports Facebook, Instagram, TikTok\n"
    "• Plain text messages do nothing\n\n"
    "Tap a button below to see commands."
)


async def _get_json_setting(key: str, default):
    raw = await db.get_setting(key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


async def _set_json_setting(key: str, value):
    await db.set_setting(key, json.dumps(value, ensure_ascii=False))


async def get_ui_labels() -> dict:
    return await _get_json_setting("ui_labels", {})


async def get_ui_emojis() -> dict:
    return await _get_json_setting("ui_emojis", {})


async def get_ui_cat_labels() -> dict:
    return await _get_json_setting("ui_cat_labels", {})


async def get_ui_row_width() -> int:
    try:
        return max(1, min(3, int(await db.get_setting("ui_row_width", "2") or "2")))
    except Exception:
        return 2


async def get_ui_welcome() -> str:
    return (await db.get_setting("ui_welcome", "")) or DEFAULT_WELCOME


# ============================================================
# Main menus (inline keyboards)
# ============================================================
async def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    disabled = await _disabled_set()
    cat_labels = await get_ui_cat_labels()
    width = await get_ui_row_width()
    rows, row = [], []
    for cat, items in TOOL_CATALOG.items():
        if not any(c not in disabled for c, _, _ in items):
            continue
        label = cat_labels.get(cat, cat)
        row.append(InlineKeyboardButton(label, callback_data=f"cat:{cat}"))
        if len(row) == width:
            rows.append(row); row = []
    if row: rows.append(row)
    if is_owner(uid):
        rows.append([InlineKeyboardButton("⚙️ Owner Panel", callback_data="m:owner")])
    return InlineKeyboardMarkup(rows)


async def category_kb(cat: str) -> InlineKeyboardMarkup:
    disabled = await _disabled_set()
    labels = await get_ui_labels()
    emojis = await get_ui_emojis()
    width = await get_ui_row_width()
    rows, row = [], []
    for cmd, label, _doc in TOOL_CATALOG.get(cat, []):
        if cmd in disabled:
            continue
        em = emojis.get(cmd, "")
        display = (f"{em} {labels.get(cmd, label)}" if em else labels.get(cmd, label)).strip()
        row.append(InlineKeyboardButton(display, callback_data=f"tool:{cmd}"))
        if len(row) == width:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("« Back to Main Menu", callback_data="m:home")])
    return InlineKeyboardMarkup(rows)


def tool_detail_kb(cat_for_back):
    rows = []
    if cat_for_back:
        rows.append([InlineKeyboardButton(f"« Back to {cat_for_back}",
                                          callback_data=f"cat:{cat_for_back}")])
    rows.append([InlineKeyboardButton("« Main Menu", callback_data="m:home")])
    return InlineKeyboardMarkup(rows)


def _find_tool(cmd: str):
    for cat, items in TOOL_CATALOG.items():
        for t in items:
            if t[0] == cmd:
                return cat, t
    return None, None


def providers_kb() -> InlineKeyboardMarkup:
    rows, row = [], []
    for k, (name, _) in REGISTRY.items():
        row.append(InlineKeyboardButton(name, callback_data=f"pick:{k}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton("« Back", callback_data="m:home")])
    return InlineKeyboardMarkup(rows)


def keytools_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Inspect API Key", callback_data="kt:inspect")],
        [InlineKeyboardButton("Try Model (last key)", callback_data="kt:try")],
        [InlineKeyboardButton("« Back", callback_data="m:home")],
    ])


def dl_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Send Video URL", callback_data="dl:ask")],
        [InlineKeyboardButton("« Back", callback_data="m:home")],
    ])


def owner_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Stats", callback_data="ow:stats"),
         InlineKeyboardButton("Logs", callback_data="ow:logs")],
        [InlineKeyboardButton("Users", callback_data="ow:users"),
         InlineKeyboardButton("Announce", callback_data="ow:announce")],
        [InlineKeyboardButton("Speak as Bot", callback_data="ow:speak"),
         InlineKeyboardButton("Speak Grants", callback_data="ow:grants")],
        [InlineKeyboardButton("Live Response Toggle", callback_data="ow:live")],
        [InlineKeyboardButton("Toggle Commands", callback_data="ow:toggle:0")],
        [InlineKeyboardButton("Set Channel", callback_data="ow:setch")],
        [InlineKeyboardButton("« Back", callback_data="m:home")],
    ])


async def toggle_kb(page: int = 0) -> InlineKeyboardMarkup:
    disabled = await _disabled_set()
    all_cmds = []
    for items in TOOL_CATALOG.values():
        for cmd, label, _doc in items:
            all_cmds.append((cmd, label))
    per_page = 8
    pages = max(1, (len(all_cmds) + per_page - 1) // per_page)
    page = page % pages
    chunk = all_cmds[page * per_page:(page + 1) * per_page]
    rows = []
    for cmd, label in chunk:
        mark = "🔴 OFF" if cmd in disabled else "🟢 ON"
        rows.append([InlineKeyboardButton(
            f"{mark}  /{cmd} — {label}", callback_data=f"tg:{cmd}:{page}")])
    if pages > 1:
        rows.append([
            InlineKeyboardButton("« Prev", callback_data=f"ow:toggle:{(page-1) % pages}"),
            InlineKeyboardButton(f"{page+1}/{pages}", callback_data="ow:noop"),
            InlineKeyboardButton("Next »", callback_data=f"ow:toggle:{(page+1) % pages}"),
        ])
    rows.append([InlineKeyboardButton("« Owner Panel", callback_data="m:owner")])
    return InlineKeyboardMarkup(rows)


def back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Main Menu", callback_data="m:home")]])


# ============================================================
# Commands
# ============================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await db.upsert_user(update.effective_user)
    if not await force_join_ok(update, context):
        return
    name = escape_html(update.effective_user.first_name or "there")
    txt = (
        f"*Welcome, {name}.*\n\n"
        "Command-based bot.\n"
        "• AI only works with commands like /g, /pr, /co\n"
        "• Downloader only supports Facebook, Instagram, TikTok\n"
        "• Plain text messages do nothing\n\n"
        "Tap a button below to see commands."
    )
    await update.effective_message.reply_text(
        txt, parse_mode=ParseMode.MARKDOWN,
        reply_markup=await main_menu_kb(update.effective_user.id),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If args given, use AI to summarize that feature/provider for the user."""
    if not await force_join_ok(update, context): return
    args = " ".join(context.args).strip() if context.args else ""
    if not args:
        lines = [
            "*Help Center*\n",
            "User commands:",
            "/start  — main menu (buttons)",
            "/menu   — AI provider menu",
            "/key    — inspect API key",
            "/dl <url>  — download video (FB/IG/TikTok)",
            "/dla <url> — download audio mp3 (FB/IG/TikTok)",
            "/ping   — latency",
            "/help <topic>  — AI-summarized help on any topic\n",
            "Plain text does nothing. Only /command or .command works.",
        ]
        await send_md(update.effective_message, "\n".join(lines))
        return
    # AI-summarized help
    feature_doc = (
        "You are the in-bot help assistant. Summarize ONLY what THIS bot offers:\n"
        "Providers: Gemini (.g), Perplexity (.pr), Copilot (.co) — free, no key needed.\n"
        "API Key Inspector: /key <KEY> works for OpenAI, Anthropic, Gemini, Groq, "
        "OpenRouter, Cohere, DeepSeek, xAI, Together AI. Then /tryke <model> <prompt>.\n"
        "Video Downloader: /dl <url> and /dla <url> for Facebook, Instagram, TikTok only (under 50MB).\n"
        "Plain text without / or . command must do nothing.\n"
        f"User asked: {args}\n"
        "Never mention owner/admin/private tools, even if the user asks.\n"
        "Reply in the user's language, concise, organized with bullets. No emojis."
    )
    placeholder = await update.effective_message.reply_text("Thinking...")
    try:
        _, fn = REGISTRY.get("g", (None, None))
        if not fn:
            await safe_edit(placeholder, "Help engine unavailable.")
            return
        ans = await asyncio.wait_for(fn(feature_doc, []), timeout=60)
        await safe_edit(placeholder, format_ai_answer(ans))
    except Exception as e:
        await safe_edit(placeholder, safe_user_error("Help"))
        await db.log("ERROR", update.effective_user.id if update.effective_user else 0, "help", str(e)[:500])


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_ok(update, context): return
    await update.effective_message.reply_text(
        "Main menu:", reply_markup=await main_menu_kb(update.effective_user.id),
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = time.time()
    m = await update.effective_message.reply_text("Pinging...")
    await m.edit_text(f"Pong  •  {(time.time()-t)*1000:.0f} ms")


# ============================================================
# AI provider call
# ============================================================
async def _call_provider(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         provider_key: str, prompt: str):
    if not await force_join_ok(update, context): return
    if not prompt.strip():
        await update.effective_message.reply_text("Please provide a question after the command.")
        return
    name, fn = REGISTRY[provider_key]
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    root_id = None
    rep = update.effective_message.reply_to_message
    reply_context = ""
    if rep:
        if rep.from_user and rep.from_user.id == context.bot.id:
            sess = await db.get_session(update.effective_chat.id, rep.message_id)
            if sess:
                provider_key = sess[0]
                name, fn = REGISTRY.get(provider_key, (name, fn))
                try:
                    _HISTORY[(update.effective_chat.id, rep.message_id)] = json.loads(sess[1])
                except Exception:
                    pass
                root_id = rep.message_id
        # Always include the replied message's text/caption as extra context.
        rep_text = (rep.text or rep.caption or "").strip()
        if rep_text and not root_id:
            who = "the bot" if (rep.from_user and rep.from_user.id == context.bot.id) else (
                (rep.from_user.first_name if rep.from_user else "someone") or "someone"
            )
            reply_context = (
                f"[Context — message from {who}]:\n{rep_text[:3000]}\n\n"
                f"[User's question]:\n"
            )

    if reply_context:
        prompt = reply_context + prompt

    history_key = (update.effective_chat.id, root_id) if root_id else None
    history = _HISTORY.get(history_key, []) if history_key else []

    live = (await db.get_setting("live_response", "on")) == "on"
    placeholder = None
    if live:
        placeholder = await update.effective_message.reply_text(f"{name} is thinking...")

    try:
        answer = await asyncio.wait_for(fn(prompt, history), timeout=180)
        answer_fmt = format_ai_answer(answer) or "No content returned."
        body = f"<b>{escape_html(name)}</b>\n\n{answer_fmt}"
        if placeholder:
            await stream_edit(placeholder, body)
            sent = placeholder
        else:
            sent = await send_md(update.effective_message, body)

        new_root = root_id or sent.message_id
        hist = _HISTORY[(update.effective_chat.id, new_root)]
        hist.append({"q": prompt, "a": (answer or "")[:4000]})
        _HISTORY[(update.effective_chat.id, new_root)] = hist[-10:]
        state = json.dumps(_HISTORY[(update.effective_chat.id, new_root)])
        await db.save_session(update.effective_chat.id, new_root, provider_key, state)
        if sent.message_id != new_root:
            await db.save_session(update.effective_chat.id, sent.message_id, provider_key, state)
        await db.log("INFO", update.effective_user.id, provider_key, prompt[:200])
    except asyncio.TimeoutError:
        msg = f"{name} timed out. Please retry."
        if placeholder: await safe_edit(placeholder, msg)
        else: await update.effective_message.reply_text(msg)
        await db.log("ERROR", update.effective_user.id, provider_key, "timeout")
    except Exception as e:
        tb = traceback.format_exc(limit=2)
        err_text = str(e)
        if provider_key == "pr" and ("Perplexity HTTP 403" in err_text or "HTTP 403" in err_text):
            msg = (
                "Perplexity is blocking requests from this server right now.\n\n"
                "Use /g or /co for now, or add fresh Perplexity browser cookies / a browser-backed proxy on the server."
            )
        else:
            msg = f"{name} error.\n\n`{e}`"
        if placeholder: await safe_edit(placeholder, msg)
        else: await send_md(update.effective_message, msg)
        await db.log("ERROR", update.effective_user.id, provider_key, f"{e}\n{tb}")


def make_provider_handler(key: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await db.upsert_user(update.effective_user)
        text = update.effective_message.text or ""
        parts = text.split(None, 1)
        prompt = parts[1] if len(parts) > 1 else ""
        await _call_provider(update, context, key, prompt)
    return handler


# ============================================================
# API key inspector
# ============================================================
async def cmd_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_ok(update, context): return
    args = context.args
    if not args:
        _AWAIT_INPUT[update.effective_user.id] = ("key", None)
        await update.effective_message.reply_text(
            "Send the API key now as your next message. (OpenAI, Anthropic, "
            "Gemini, Groq, OpenRouter, Cohere, DeepSeek, xAI, Together AI)",
        )
        return
    await _do_inspect(update, args[0])


async def _do_inspect(update: Update, key: str):
    placeholder = await update.effective_message.reply_text("Inspecting key...")
    try:
        info = await inspect_key(key)
        if not info.get("valid"):
            await safe_edit(placeholder,
                f"<b>{escape_html(info.get('provider', 'Unknown'))}</b>  •  INVALID\n"
                f"Status: <code>{escape_html(str(info.get('status')))}</code>\n"
                f"Detail: <code>{escape_html(json.dumps(info.get('error'))[:500])}</code>")
            return
        _PENDING_KEY[update.effective_user.id] = key
        models = info.get("models", [])
        limits = info.get("limits", {})
        lines = [
            f"<b>{escape_html(info['provider'])}</b>  •  ACTIVE",
            f"Models available: <b>{len(models)}</b>",
            "",
        ]
        for m in models[:30]:
            lines.append(f"• <code>{escape_html(m)}</code>")
        if len(models) > 30:
            lines.append(f"... +{len(models)-30} more")
        if limits:
            lines.append("\n<b>Limits / Quota:</b>")
            for k, v in limits.items():
                lines.append(f"  • {escape_html(str(k))}: <code>{escape_html(str(v))}</code>")
        lines.append("\nTry a model: <code>/tryke &lt;model&gt; &lt;prompt&gt;</code>")
        if is_owner(update.effective_user.id):
            lines.append(
                "Add as bot provider: <code>/addmodel &lt;alias&gt; &lt;model&gt;</code>"
            )
        await safe_edit(placeholder, "\n".join(lines))
    except Exception as e:
        await safe_edit(placeholder, safe_user_error("Key inspection"))
        await db.log("ERROR", update.effective_user.id, "key", str(e)[:500])


async def cmd_tryke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_ok(update, context): return
    key = _PENDING_KEY.get(update.effective_user.id)
    if not key:
        await update.effective_message.reply_text("First inspect a key with /key <API_KEY>.")
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Usage: /tryke <model> <prompt>")
        return
    model = context.args[0]
    prompt = " ".join(context.args[1:])
    placeholder = await update.effective_message.reply_text(f"Calling {model}...")
    try:
        out = await asyncio.wait_for(try_model(key, model, prompt), timeout=120)
        await stream_edit(placeholder, f"<b>{escape_html(model)}</b>\n\n{format_ai_answer(out)}")
    except Exception as e:
        await safe_edit(placeholder, safe_user_error("Model test"))
        await db.log("ERROR", update.effective_user.id, "tryke", str(e)[:500])


# ============================================================
# Video downloader
# ============================================================
async def cmd_dl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_ok(update, context): return
    text = " ".join(context.args).strip()
    url = downloader.detect_url(text) or text
    if not url or not url.startswith("http"):
        _AWAIT_INPUT[update.effective_user.id] = ("download", None)
        await update.effective_message.reply_text(
            "Send a Facebook, Instagram, or TikTok video URL now."
        )
        return
    await _run_download(update, context, url)


async def cmd_dla(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Audio-only download."""
    if not await force_join_ok(update, context): return
    text = " ".join(context.args).strip()
    url = downloader.detect_url(text) or text
    if not url or not url.startswith("http"):
        await update.effective_message.reply_text(
            "Usage: /dla <url> — Facebook, Instagram, or TikTok only."
        )
        return
    await _run_download(update, context, url, audio_only=True)


async def _run_download(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        url: str, audio_only: bool = False):
    chat_id = update.effective_chat.id
    kind = "audio" if audio_only else "video"
    global _DOWNLOAD_QUEUE
    async with _DOWNLOAD_QUEUE_LOCK:
        _DOWNLOAD_QUEUE += 1
        queue_position = _DOWNLOAD_QUEUE
    status = await update.effective_message.reply_text(
        f"Queued for {kind} download…\nAhead of you: {max(0, queue_position - 1)}"
    )
    info = None
    loop = asyncio.get_running_loop()
    last_edit = {"t": 0.0, "text": ""}

    def _fmt_bytes(n: int) -> str:
        if n <= 0: return "?"
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024: return f"{n:.1f} {u}"
            n /= 1024
        return f"{n:.1f} TB"

    def _on_progress(p: dict):
        s = p.get("status")
        if s == "downloading":
            dl = p.get("downloaded", 0); tot = p.get("total", 0)
            pct = (dl / tot * 100) if tot else 0
            sp = p.get("speed", 0) or 0
            eta = p.get("eta", 0) or 0
            txt = (
                f"{kind.title()} download… {pct:.0f}%\n"
                f"{_fmt_bytes(dl)} / {_fmt_bytes(tot)}  •  {_fmt_bytes(sp)}/s\n"
                f"ETA: {eta}s"
            )
        elif s == "finished":
            txt = f"{kind.title()} ready. Processing…"
        else:
            return
        if txt == last_edit["text"]:
            return
        last_edit["text"] = txt
        async def _do():
            try: await status.edit_text(txt)
            except Exception: pass
        asyncio.run_coroutine_threadsafe(_do(), loop)

    try:
        if queue_position > 1:
            try:
                await status.edit_text(
                    f"Queued for {kind} download…\n"
                    f"Ahead of you: {queue_position - 1}\n"
                    "Your file will start automatically in order."
                )
            except Exception:
                pass
        async with _DOWNLOAD_SEM:
            try:
                await status.edit_text(f"Starting {kind} download…")
            except Exception:
                pass
            await context.bot.send_chat_action(
                chat_id,
                ChatAction.UPLOAD_VOICE if audio_only else ChatAction.UPLOAD_VIDEO,
            )
            info = await asyncio.wait_for(
                downloader.download(url, progress=_on_progress, audio_only=audio_only),
                timeout=420,
            )
            try:
                await status.edit_text(
                    f"Uploading {kind}…\n"
                    f"Size: {human_size(info['size'])}"
                )
            except Exception: pass
            title = clean_text(info.get("title") or ("Audio" if info.get("audio_only") else "Video"))
            uploader = clean_text(info.get("uploader") or "Unknown source")
            caption = clean_text(f"{title}\n{uploader} • {human_size(info['size'])}")[:900]
            width = info.get("width") or None
            height = info.get("height") or None
            with open(info["path"], "rb") as f:
                if info.get("audio_only"):
                    await context.bot.send_audio(
                        chat_id=chat_id, audio=f, caption=caption,
                        title=info.get("title") or None,
                        performer=info.get("uploader") or None,
                        duration=info.get("duration") or None,
                        write_timeout=240, read_timeout=240,
                    )
                else:
                    await context.bot.send_video(
                        chat_id=chat_id, video=f, caption=caption,
                        supports_streaming=True,
                        duration=info.get("duration") or None,
                        width=width,
                        height=height,
                        write_timeout=240, read_timeout=240,
                    )
            try: await status.delete()
            except Exception: pass
        await db.log("INFO", update.effective_user.id, "dl", url[:200])
    except asyncio.TimeoutError:
        try: await status.edit_text("Download timed out. Please try again.")
        except Exception: pass
        await db.log("ERROR", update.effective_user.id, "dl", f"{url} | timeout")
    except Exception as e:
        try: await status.edit_text(f"Download failed:\n{downloader.user_error_text(e)}")
        except Exception: pass
        await db.log("ERROR", update.effective_user.id, "dl", f"{url} | {type(e).__name__}: {e}")
    finally:
        async with _DOWNLOAD_QUEUE_LOCK:
            _DOWNLOAD_QUEUE = max(0, _DOWNLOAD_QUEUE - 1)
        if info: downloader.cleanup(info)


# ============================================================
# Owner panel
# ============================================================
async def _owner_only(update: Update) -> bool:
    user = update.effective_user
    return bool(user and is_owner(user.id))


async def load_custom_providers(app: Application | None = None):
    rows = await db.list_custom_providers()
    for cmd, name, base_url, api_key, model, enabled in rows:
        if not enabled:
            continue
        register_provider(cmd, name, make_openai_compatible_provider(name, base_url, api_key, model))
        if app:
            app.add_handler(CommandHandler(cmd, make_provider_handler(cmd)))
        # Also expose as a button in the AI Tools section of the main panel.
        doc = (
            f"Chat with {name}.\n\n<b>Usage:</b>\n"
            f"<code>/{cmd} your question</code>  or  <code>.{cmd} your question</code>"
        )
        ai_items = TOOL_CATALOG["AI Tools"]
        ai_items[:] = [t for t in ai_items if t[0] != cmd]
        ai_items.append((cmd, name, doc))


async def cmd_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    await update.effective_message.reply_text("Owner panel:", reply_markup=owner_kb())


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    s = await db.stats()
    ch = await db.get_setting("force_join", FORCE_JOIN_CHANNEL or "(none)")
    live = await db.get_setting("live_response", "on")
    pm = process_metrics(_PROCESS_STARTED_AT)
    await send_md(update.effective_message,
        f"<b>Bot Status</b>\n"
        f"• Uptime: <code>{format_duration(pm['uptime_s'])}</code>\n"
        f"• Memory (RSS): <code>{human_size(pm['rss_bytes'])}</code>\n"
        f"• CPU load (1/5/15m): <code>{pm['load_1']:.2f} / {pm['load_5']:.2f} / {pm['load_15']:.2f}</code>\n"
        f"• CPU cores: <code>{pm['cpu_count']}</code>\n"
        f"• Download queue: <code>{_DOWNLOAD_QUEUE}</code> total waiting/running\n"
        f"• Users: <code>{s['users']}</code>\n"
        f"• Banned: <code>{s['banned']}</code>\n"
        f"• Messages: <code>{s['messages']}</code>\n"
        f"• Errors: <code>{s['errors']}</code>\n"
        f"• Channel: <code>{escape_html(ch)}</code>\n"
        f"• Live response: <code>{escape_html(live)}</code>\n"
        f"• Providers: <code>{escape_html(', '.join(REGISTRY.keys()))}</code>")


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    n = 20
    if context.args:
        try: n = max(1, min(100, int(context.args[0])))
        except: pass
    rows = await db.get_logs(n)
    if not rows:
        await update.effective_message.reply_text("No logs yet."); return
    lines = ["*Recent Logs* (newest first)"]
    for ts, lvl, uid, prov, msg in rows:
        when = time.strftime("%m-%d %H:%M:%S", time.localtime(ts))
        lines.append(f"`[{when}]` {lvl} u={uid} {prov}: {msg[:140]}")
    await send_md(update.effective_message, "\n".join(lines))


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    ids = await db.all_user_ids()
    await update.effective_message.reply_text(f"Total active users: {len(ids)}")


async def cmd_setchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /setchannel <username_without_@>  (use 'off' to disable)")
        return
    val = context.args[0].lstrip("@")
    if val.lower() == "off": val = ""
    await db.set_setting("force_join", val)
    import bot.config as cfg
    cfg.FORCE_JOIN_CHANNEL = val
    await update.effective_message.reply_text(f"Force-join channel: {val or '(disabled)'}")


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    if not context.args:
        await update.effective_message.reply_text("Usage: /ban <user_id>"); return
    try:
        uid = int(context.args[0]); await db.set_banned(uid, 1)
        await update.effective_message.reply_text(f"User {uid} banned.")
    except Exception as e:
        await update.effective_message.reply_text(safe_user_error("Request"))
        await db.log("ERROR", update.effective_user.id, "owner", str(e)[:500])


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    if not context.args:
        await update.effective_message.reply_text("Usage: /unban <user_id>"); return
    try:
        uid = int(context.args[0]); await db.set_banned(uid, 0)
        await update.effective_message.reply_text(f"User {uid} unbanned.")
    except Exception as e:
        await update.effective_message.reply_text(safe_user_error("Request"))
        await db.log("ERROR", update.effective_user.id, "owner", str(e)[:500])


_PENDING_ANNOUNCE: set[int] = set()  # owner_ids awaiting a source message of any type


async def _broadcast_copy(context: ContextTypes.DEFAULT_TYPE, src_chat_id: int,
                          src_message_id: int, status_msg) -> None:
    """Broadcast by copying a source message (any type, with caption preserved) to all users."""
    ids = await db.all_user_ids()
    total = len(ids)
    ok = fail = blocked = 0
    try:
        await status_msg.edit_text(f"Broadcasting to {total} users...")
    except Exception:
        pass
    for i, uid in enumerate(ids, 1):
        try:
            await context.bot.copy_message(
                chat_id=uid, from_chat_id=src_chat_id, message_id=src_message_id,
            )
            ok += 1
        except Exception as e:
            es = str(e).lower()
            if "blocked" in es or "deactivated" in es or "chat not found" in es:
                blocked += 1
            else:
                fail += 1
        # gentle rate-limit: 25 msgs/sec is the Telegram global cap; stay well under
        if i % 25 == 0:
            await asyncio.sleep(1.0)
            try:
                await status_msg.edit_text(
                    f"Progress {i}/{total}\nDelivered: {ok}\nBlocked: {blocked}\nFailed: {fail}"
                )
            except Exception:
                pass
    try:
        await status_msg.edit_text(
            f"<b>Broadcast complete</b>\n"
            f"Total: {total}\nDelivered: {ok}\nBlocked/deleted: {blocked}\nFailed: {fail}",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def _broadcast_text(context: ContextTypes.DEFAULT_TYPE, text: str, status_msg) -> None:
    ids = await db.all_user_ids()
    total = len(ids)
    ok = fail = blocked = 0
    for i, uid in enumerate(ids, 1):
        try:
            await context.bot.send_message(uid, text)
            ok += 1
        except Exception as e:
            es = str(e).lower()
            if "blocked" in es or "deactivated" in es or "chat not found" in es:
                blocked += 1
            else:
                fail += 1
        if i % 25 == 0:
            await asyncio.sleep(1.0)
            try:
                await status_msg.edit_text(
                    f"Progress {i}/{total}\nDelivered: {ok}\nBlocked: {blocked}\nFailed: {fail}"
                )
            except Exception:
                pass
    try:
        await status_msg.edit_text(
            f"<b>Broadcast complete</b>\n"
            f"Total: {total}\nDelivered: {ok}\nBlocked/deleted: {blocked}\nFailed: {fail}",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def cmd_announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Universal broadcast.

    Modes:
    1) Reply to ANY message (photo / video / audio / document / voice / sticker /
       text — with or without caption) and send /announce  → copies that exact
       message to every user.
    2) /announce <text>  → broadcasts plain text.
    3) Send a photo/video/audio/document with caption "/announce" or "/announce <extra>"
       → broadcasts that media (caption is preserved as-is, minus the /announce token).
    4) Click "Announce" in the Owner panel, then send the very next message in ANY
       format → that message is broadcast.
    """
    if not await _owner_only(update):
        return
    msg = update.effective_message
    text_args = " ".join(context.args).strip() if context.args else ""

    # Mode 1: replying to any kind of message → copy it
    rep = msg.reply_to_message
    if rep is not None:
        status = await msg.reply_text("Preparing broadcast...")
        await _broadcast_copy(context, rep.chat_id, rep.message_id, status)
        return

    # Mode 3: media sent WITH the /announce command in caption
    has_media = any([
        msg.photo, msg.video, msg.audio, msg.voice, msg.document,
        msg.animation, msg.sticker, msg.video_note,
    ])
    if has_media:
        status = await msg.reply_text("Preparing broadcast...")
        await _broadcast_copy(context, msg.chat_id, msg.message_id, status)
        return

    # Mode 2: text args
    if text_args:
        status = await msg.reply_text("Preparing broadcast...")
        await _broadcast_text(context, text_args, status)
        return

    # Mode 4: arm pending; next owner message (any type) becomes the source
    _PENDING_ANNOUNCE.add(update.effective_user.id)
    await msg.reply_text(
        "Send the announcement now — it can be any type:\n"
        "text, photo, video, audio, voice, document, GIF, or sticker "
        "(with or without caption).\n\n"
        "Send /cancel to abort."
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    removed = False
    if uid in _PENDING_ANNOUNCE:
        _PENDING_ANNOUNCE.discard(uid); removed = True
    if uid in _AWAIT_INPUT:
        _AWAIT_INPUT.pop(uid, None); removed = True
    await update.effective_message.reply_text(
        "Pending action cancelled." if removed else "Nothing to cancel."
    )


async def on_any_owner_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catches the owner's next message of ANY type when a broadcast is armed."""
    msg = update.effective_message
    if not msg:
        return
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in _PENDING_ANNOUNCE:
        return
    # do not capture a fresh /announce or /cancel command — let the command handlers run
    raw_text = msg.text or msg.caption or ""
    if raw_text.startswith(("/announce", "/cancel")):
        return
    _PENDING_ANNOUNCE.discard(uid)
    status = await msg.reply_text("Preparing broadcast...")
    await _broadcast_copy(context, msg.chat_id, msg.message_id, status)
    # Stop other handlers (on_text, etc.) from also processing this message.
    from telegram.ext import ApplicationHandlerStop
    raise ApplicationHandlerStop



async def cmd_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    cur = await db.get_setting("live_response", "on")
    new = "off" if cur == "on" else "on"
    if context.args and context.args[0].lower() in ("on", "off"):
        new = context.args[0].lower()
    await db.set_setting("live_response", new)
    await update.effective_message.reply_text(
        f"Live response is now: {new.upper()}\n"
        f"(When ON, the answer text appears progressively like live typing.)")


async def cmd_addprovider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    if len(context.args) < 5:
        await update.effective_message.reply_text(
            "Usage: /addprovider <cmd> <name> <base_url> <api_key> <model>"
        )
        return
    from .providers import register, make_openai_compatible_provider
    cmd = context.args[0].lower().strip()
    name, base_url, api_key = context.args[1], context.args[2], context.args[3]
    model = " ".join(context.args[4:]).strip()
    func = make_openai_compatible_provider(name, base_url, api_key, model)
    register(cmd, name, func)
    await db.add_custom_provider(cmd, name, base_url, api_key, model)
    # Bind /command and .alias onto the running app immediately
    try:
        context.application.add_handler(CommandHandler(cmd, make_provider_handler(cmd)))
    except Exception:
        pass
    await setup_bot_commands(context.application)
    await update.effective_message.reply_text(
        f"Provider added.\n• Command: /{cmd}\n• Dot alias: .{cmd}\n• Model: {model}"
    )


async def cmd_delprovider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    if not context.args:
        await update.effective_message.reply_text("Usage: /delprovider <cmd>")
        return
    cmd = context.args[0].lower().strip()
    REGISTRY.pop(cmd, None)
    await db.remove_custom_provider(cmd)
    await setup_bot_commands(context.application)
    await update.effective_message.reply_text(f"Provider removed: {cmd}")


async def cmd_providers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["<b>Available providers</b>", ""]
    for key, (name, _) in REGISTRY.items():
        lines.append(f"• <b>{escape_html(name)}</b> — <code>/{key}</code> or <code>.{key}</code>")
    await send_md(update.effective_message, "\n".join(lines))


_RESERVED_CMDS = {
    "start","help","menu","ping","key","tryke","dl","dla","owner","stats","logs",
    "users","setchannel","ban","unban","announce","cancel","live","speak","grant",
    "revoke","restart","mkey","mlimit","addmodel","addprovider","delprovider",
    "providers","en","de","text","wc","spell","gra","syn","prn","bg","enh","res",
    "short","style","tr","ocr",
}

_PROVIDER_BASE_URLS = {
    "openai":     "https://api.openai.com/v1",
    "groq":       "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek":   "https://api.deepseek.com/v1",
    "xai":        "https://api.x.ai/v1",
    "together":   "https://api.together.xyz/v1",
    "cohere":     "https://api.cohere.com/compatibility/v1",
}


async def cmd_addmodel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner: add any model from the last inspected key as a new provider button.

    Usage: /addmodel <alias> <model_id>
    Run /key <API_KEY> first to load a key.
    """
    if not await _owner_only(update):
        return
    uid = update.effective_user.id
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Usage: /addmodel <alias> <model_id>\n\n"
            "First run /key <API_KEY> to load a key. Then turn any of its models "
            "into a new bot provider button. An AI will write the description for you."
        )
        return
    key = _PENDING_KEY.get(uid)
    if not key:
        await update.effective_message.reply_text(
            "No API key loaded. Run /key <API_KEY> first."
        )
        return
    alias = context.args[0].lower().strip().lstrip("/.")
    model = " ".join(context.args[1:]).strip()
    if not alias.isalnum() or len(alias) > 16:
        await update.effective_message.reply_text(
            "Alias must be alphanumeric and ≤ 16 chars."
        )
        return
    if alias in REGISTRY or alias in _RESERVED_CMDS:
        await update.effective_message.reply_text(
            f"Alias /{alias} is already taken. Pick a different one."
        )
        return

    from .keycheck import _detect
    kind = _detect(key)
    base_url = _PROVIDER_BASE_URLS.get(kind)
    if not base_url:
        await update.effective_message.reply_text(
            f"This key type ({kind}) is not yet supported for custom providers.\n"
            "Supported: OpenAI, Groq, OpenRouter, DeepSeek, xAI, Together AI, Cohere."
        )
        return

    name = model.split("/")[-1].replace("-", " ").replace("_", " ").title() or model
    placeholder = await update.effective_message.reply_text(
        f"Adding /{alias} → {model}…\nAuto-generating description…"
    )

    desc = f"Chat with {name}."
    try:
        _, gfn = REGISTRY.get("g", (None, None))
        if gfn:
            ai = await asyncio.wait_for(
                gfn(
                    f"Write one short friendly sentence (max 14 words) describing the "
                    f"AI model '{model}' for end users. Plain text only. No emojis, "
                    f"no quotes, no markdown.",
                    [],
                ),
                timeout=25,
            )
            ai_line = (ai or "").strip().splitlines()[0][:200]
            if ai_line:
                desc = ai_line
    except Exception:
        pass

    try:
        func = make_openai_compatible_provider(name, base_url, key, model)
        register_provider(alias, name, func)
        await db.add_custom_provider(alias, name, base_url, key, model)
        try:
            context.application.add_handler(CommandHandler(alias, make_provider_handler(alias)))
        except Exception:
            pass

        doc = (
            f"{desc}\n\n<b>Usage:</b>\n"
            f"<code>/{alias} your question</code>  or  <code>.{alias} your question</code>"
        )
        ai_items = TOOL_CATALOG["AI Tools"]
        ai_items[:] = [t for t in ai_items if t[0] != alias]
        ai_items.append((alias, name, doc))

        await setup_bot_commands(context.application)
        await safe_edit(
            placeholder,
            f"<b>Provider added</b>\n"
            f"• Command: <code>/{alias}</code> or <code>.{alias}</code>\n"
            f"• Model: <code>{escape_html(model)}</code>\n"
            f"• Button: <b>{escape_html(name)}</b> in AI Tools menu\n\n"
            f"<i>{escape_html(desc)}</i>",
        )
    except Exception as e:
        await safe_edit(placeholder, safe_user_error("Add provider"))
        await db.log("ERROR", uid, "addmodel", str(e)[:500])


async def on_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = (update.inline_query.query or "").strip()
    if not query:
        return
    results = []
    for key, (name, _) in list(REGISTRY.items())[:12]:
        results.append(
            InlineQueryResultArticle(
                id=str(uuid4()),
                title=f"Ask {name}",
                description=f"Send to bot as .{key} {query[:40]}",
                input_message_content=InputTextMessageContent(f".{key} {query}"),
            )
        )
    await update.inline_query.answer(results, cache_time=0, is_personal=True)


# ---------- Speak-as-bot ----------
async def cmd_speak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner (or granted user): /speak <chat_id> — set target chat to talk in."""
    uid = update.effective_user.id
    if not await db.can_speak(uid, OWNER_ID):
        return
    if not context.args:
        cur = await db.get_speak_target(uid)
        await update.effective_message.reply_text(
            f"Current speak target: `{cur}`\n"
            f"Usage: /speak <chat_id_or_@username>  (use 'off' to stop)\n"
            f"After setting, any message you send to this bot is forwarded as the bot's message.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    target = context.args[0]
    if target.lower() == "off":
        await db.set_speak_target(uid, None)
        await update.effective_message.reply_text("Speak mode OFF.")
        return
    # Resolve username -> chat id
    chat_id = None
    if target.startswith("@") or not target.lstrip("-").isdigit():
        try:
            chat = await context.bot.get_chat(target if target.startswith("@") else f"@{target}")
            chat_id = chat.id
        except Exception as e:
            await update.effective_message.reply_text("Could not resolve that chat. Check the username or chat ID and try again.")
            await db.log("ERROR", uid, "speak", f"resolve {target} | {e}")
            return
    else:
        chat_id = int(target)
    await db.set_speak_target(uid, chat_id)
    await update.effective_message.reply_text(
        f"Speak mode ON. Target: `{chat_id}`\n"
        f"Now send any text/photo/video — bot will post it there.\n"
        f"Use /speak off to stop.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_grant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    if not context.args:
        rows = await db.list_speak_grants()
        if not rows:
            await update.effective_message.reply_text("No granted users.\nUsage: /grant <user_id>")
            return
        await update.effective_message.reply_text(
            "Granted users:\n" + "\n".join(f"• {u}" for u, _ in rows))
        return
    try:
        uid = int(context.args[0])
        await db.grant_speak(uid)
        await update.effective_message.reply_text(f"Granted speak-as-bot to {uid}.")
    except Exception as e:
        await update.effective_message.reply_text(safe_user_error("Grant update"))
        await db.log("ERROR", update.effective_user.id, "grant", str(e)[:500])


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _owner_only(update): return
    if not context.args:
        await update.effective_message.reply_text("Usage: /revoke <user_id>"); return
    try:
        uid = int(context.args[0])
        await db.revoke_speak(uid)
        await update.effective_message.reply_text(f"Revoked from {uid}.")
    except Exception as e:
        await update.effective_message.reply_text(safe_user_error("Grant update"))
        await db.log("ERROR", update.effective_user.id, "revoke", str(e)[:500])


# ============================================================
# Callback handler
# ============================================================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    uid = update.effective_user.id

    if data == "verify_join":
        if await force_join_ok(update, context):
            try: await q.edit_message_text("Verified. Tap /start.")
            except Exception: pass
        return

    if data == "m:home":
        await q.edit_message_text("Main menu:", reply_markup=await main_menu_kb(uid)); return

    # Categorized tool menu
    if data.startswith("cat:"):
        cat = data.split(":", 1)[1]
        await q.edit_message_text(
            f"<b>{escape_html(cat)}</b>\n\nTap a tool for details.",
            parse_mode=ParseMode.HTML,
            reply_markup=await category_kb(cat),
        )
        return
    if data.startswith("tool:"):
        cmd = data.split(":", 1)[1]
        cat, t = _find_tool(cmd)
        if not t:
            await q.edit_message_text("Tool not found.", reply_markup=await main_menu_kb(uid)); return
        _, label, doc = t
        await q.edit_message_text(
            f"<b>/{cmd} — {escape_html(label)}</b>\n\n{doc}",
            parse_mode=ParseMode.HTML,
            reply_markup=tool_detail_kb(cat),
            disable_web_page_preview=True,
        )
        return
    if data == "m:providers":
        await q.edit_message_text("Choose an AI provider:", reply_markup=providers_kb()); return
    if data == "m:keytools":
        await q.edit_message_text("API key tools:", reply_markup=keytools_kb()); return
    if data == "m:dl":
        await q.edit_message_text(
            "*Video Downloader*\n\nSupports Facebook, Instagram, TikTok only.\n"
            "Max 50MB. Use /dl for video and /dla for audio.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=dl_kb()); return
    if data == "m:help":
        await q.edit_message_text(
            "*Help*\n\nUse the buttons in the main menu, or these commands:\n"
            "/key, /tryke, /dl, /dla, /menu, /ping, /help <topic>\n\nPlain text does nothing.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_home_kb()); return
    if data == "m:owner":
        if not is_owner(uid): return
        await q.edit_message_text("Owner panel:", reply_markup=owner_kb()); return

    if data.startswith("pick:"):
        k = data.split(":", 1)[1]
        name = REGISTRY.get(k, (k,))[0]
        await q.edit_message_text(
            f"*Selected: {name}*\n\n"
            f"Send: `.{k} your question`\nOr: `/{k} your question`\n\n"
            f"Plain text alone will be ignored.",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_home_kb())
        return

    if data == "kt:inspect":
        _AWAIT_INPUT[uid] = ("key", None)
        await q.edit_message_text(
            "Send the API key as your next message.",
            reply_markup=back_home_kb()); return
    if data == "kt:try":
        if uid not in _PENDING_KEY:
            await q.edit_message_text("No key inspected yet. Inspect one first.",
                                       reply_markup=back_home_kb()); return
        _AWAIT_INPUT[uid] = ("tryke", None)
        await q.edit_message_text(
            "Send: `<model> <your prompt>`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_home_kb()); return

    if data == "dl:ask":
        _AWAIT_INPUT[uid] = ("download", None)
        await q.edit_message_text("Send a Facebook, Instagram, or TikTok URL now.", reply_markup=back_home_kb()); return

    if data.startswith("dlx:"):
        try:
            _, kind, token = data.split(":", 2)
        except ValueError:
            return
        urls = context.application.bot_data.get("dl_urls", {})
        url = urls.pop(token, None)
        if not url:
            await q.edit_message_text("This link expired. Send it again."); return
        await q.edit_message_text(f"Starting {('audio' if kind=='a' else 'video')} download…")
        await _run_download(update, context, url, audio_only=(kind == "a"))
        return

    # Owner sub-actions
    if data.startswith("ow:"):
        if not is_owner(uid): return
        sub = data[3:]
        if sub == "stats":
            s = await db.stats()
            ch = await db.get_setting("force_join", FORCE_JOIN_CHANNEL or "(none)")
            live = await db.get_setting("live_response", "on")
            pm = process_metrics(_PROCESS_STARTED_AT)
            await q.edit_message_text(
                f"<b>Stats</b>\n"
                f"Uptime: <code>{format_duration(pm['uptime_s'])}</code>\n"
                f"RAM: <code>{human_size(pm['rss_bytes'])}</code>\n"
                f"Load: <code>{pm['load_1']:.2f}/{pm['load_5']:.2f}/{pm['load_15']:.2f}</code>\n"
                f"CPU cores: <code>{pm['cpu_count']}</code>\n"
                f"Download queue: <code>{_DOWNLOAD_QUEUE}</code>\n"
                f"Users: <code>{s['users']}</code> | Banned: <code>{s['banned']}</code>\n"
                f"Messages: <code>{s['messages']}</code> | Errors: <code>{s['errors']}</code>\n"
                f"Channel: <code>{escape_html(ch)}</code> | Live: <code>{escape_html(live)}</code>",
                parse_mode=ParseMode.HTML, reply_markup=owner_kb())
            return
        if sub == "logs":
            rows = await db.get_logs(15)
            lines = ["*Recent Logs*"]
            for ts, lvl, u, prov, msg in rows:
                when = time.strftime("%m-%d %H:%M", time.localtime(ts))
                lines.append(f"`[{when}]` {lvl} u={u} {prov}: {msg[:80]}")
            await q.edit_message_text("\n".join(lines) or "No logs.",
                                       parse_mode=ParseMode.MARKDOWN, reply_markup=owner_kb())
            return
        if sub == "users":
            ids = await db.all_user_ids()
            await q.edit_message_text(f"Active users: {len(ids)}", reply_markup=owner_kb()); return
        if sub == "announce":
            _PENDING_ANNOUNCE.add(uid)
            await q.edit_message_text(
                "Send the announcement now — it can be any type:\n"
                "text, photo, video, audio, voice, document, GIF, or sticker "
                "(with or without caption).\n\n"
                "Send /cancel to abort.",
                reply_markup=owner_kb(),
            ); return
        if sub == "speak":
            _AWAIT_INPUT[uid] = ("speak_to", None)
            await q.edit_message_text(
                "Send target chat ID or @username (or 'off').", reply_markup=owner_kb()); return
        if sub == "grants":
            rows = await db.list_speak_grants()
            txt = "*Speak Grants*\n"
            txt += "\n".join(f"• {u}" for u, _ in rows) if rows else "(none)"
            txt += "\n\nUse /grant <id> or /revoke <id>"
            await q.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=owner_kb()); return
        if sub == "live":
            cur = await db.get_setting("live_response", "on")
            new = "off" if cur == "on" else "on"
            await db.set_setting("live_response", new)
            await q.edit_message_text(f"Live response: {new.upper()}", reply_markup=owner_kb()); return
        if sub == "setch":
            _AWAIT_INPUT[uid] = ("setchannel", None)
            await q.edit_message_text(
                "Send channel username (without @), or 'off' to disable.",
                reply_markup=owner_kb()); return
        if sub == "noop":
            return
        if sub.startswith("toggle:"):
            try: page = int(sub.split(":", 1)[1])
            except Exception: page = 0
            await q.edit_message_text(
                "<b>Toggle Commands</b>\n\nTap a command to turn it ON/OFF. "
                "Disabled commands are hidden from the menu and blocked from use.",
                parse_mode=ParseMode.HTML,
                reply_markup=await toggle_kb(page),
            )
            return

    # Toggle a single command on/off
    if data.startswith("tg:"):
        if not is_owner(uid): return
        parts = data.split(":")
        cmd = parts[1]; page = int(parts[2]) if len(parts) > 2 else 0
        disabled = await _disabled_set()
        if cmd in disabled: disabled.discard(cmd)
        else: disabled.add(cmd)
        await _set_disabled(disabled)
        try:
            await setup_bot_commands(context.application)
        except Exception:
            pass
        await q.edit_message_text(
            "<b>Toggle Commands</b>\n\nTap a command to turn it ON/OFF.",
            parse_mode=ParseMode.HTML,
            reply_markup=await toggle_kb(page),
        )
        return


# ============================================================
# Text dispatcher
# ============================================================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return
    await db.upsert_user(update.effective_user)
    if await db.is_banned(update.effective_user.id):
        return
    uid = update.effective_user.id

    # 1) Awaiting structured input
    awaiting = _AWAIT_INPUT.pop(uid, None)
    if awaiting:
        kind, _ = awaiting
        if kind == "key":
            await _do_inspect(update, text.split()[0]); return
        if kind == "tryke":
            parts = text.split(None, 1)
            if len(parts) < 2:
                await msg.reply_text("Format: <model> <prompt>"); return
            context.args = [parts[0]] + parts[1].split()
            # build context.args style
            context.args = [parts[0]] + [parts[1]]
            await cmd_tryke(update, context); return
        if kind == "download":
            url = downloader.detect_url(text) or text
            await _run_download(update, context, url); return
        # (the legacy text-only "announce" path is replaced by _PENDING_ANNOUNCE
        # which is handled by on_any_owner_message and supports every message type)
        if kind == "speak_to":
            context.args = [text.split()[0]]
            await cmd_speak(update, context); return
        if kind == "setchannel":
            context.args = [text.split()[0]]
            await cmd_setchannel(update, context); return

    # 2) Owner/granted speak-as-bot forward
    target = await db.get_speak_target(uid)
    if target and await db.can_speak(uid, OWNER_ID) and not text.startswith(("/", ".")):
        try:
            await context.bot.send_message(target, text)
            await msg.reply_text(f"→ sent to {target}")
        except Exception as e:
            await msg.reply_text("Message could not be sent to the target chat.")
            await db.log("ERROR", uid, "speak", str(e)[:500])
        return

    # 3) Dot-prefix commands
    if text.startswith("."):
        first, _, rest = text[1:].partition(" ")
        cmd = first.lower()
        if cmd in REGISTRY:
            await _call_provider(update, context, cmd, rest); return
        alias = {
            "start": cmd_start, "help": cmd_help, "menu": cmd_menu,
            "ping": cmd_ping, "key": cmd_key, "tryke": cmd_tryke, "dl": cmd_dl,
        }
        if cmd in alias:
            context.args = rest.split() if rest else []
            await alias[cmd](update, context); return
        if is_owner(uid):
            oalias = {
                "owner": cmd_owner, "stats": cmd_stats, "logs": cmd_logs,
                "users": cmd_users, "setchannel": cmd_setchannel,
                "ban": cmd_ban, "unban": cmd_unban, "announce": cmd_announce,
                "live": cmd_live, "speak": cmd_speak, "grant": cmd_grant, "revoke": cmd_revoke,
            }
            if cmd in oalias:
                context.args = rest.split() if rest else []
                await oalias[cmd](update, context); return

    # 4) Plain text without command must do nothing
    # Exception: if this is a reply to a previous bot AI message that has a
    # saved session, continue that conversation with the same provider.
    rep = msg.reply_to_message
    if rep and rep.from_user and rep.from_user.id == context.bot.id:
        sess = await db.get_session(update.effective_chat.id, rep.message_id)
        if sess:
            provider_key = sess[0]
            if provider_key in REGISTRY:
                await _call_provider(update, context, provider_key, text)
                return
    return


# ============================================================
# Error handler
# ============================================================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))[:1800]
    try: await db.log("ERROR", 0, "system", tb)
    except Exception: pass


# ============================================================
# BotCommand menus (per-scope)
# ============================================================
USER_COMMANDS = [
    BotCommand("start", "Main menu"),
    BotCommand("menu",  "Open buttons menu"),
    BotCommand("key",   "Inspect an API key"),
    BotCommand("tryke", "Try a model with last key"),
    BotCommand("dl",    "Download FB/IG/TikTok video"),
    BotCommand("dla",   "Download FB/IG/TikTok audio"),
    BotCommand("en",    "Encode text (Base64/Hex/Binary/…)"),
    BotCommand("de",    "Decode text from any format"),
    BotCommand("text",  "Transform text case/reverse"),
    BotCommand("wc",    "Word & character count"),
    BotCommand("spell", "Spell suggestions"),
    BotCommand("gra",   "Grammar fix (AI)"),
    BotCommand("syn",   "Synonyms & antonyms"),
    BotCommand("prn",   "Pronunciation + audio"),
    BotCommand("bg",    "Remove image background"),
    BotCommand("enh",   "Enhance a photo"),
    BotCommand("res",   "Resize image (presets)"),
    BotCommand("short", "Shorten a URL"),
    BotCommand("style", "Stylish text (40+ fonts)"),
    BotCommand("tr",    "Translate text"),
    BotCommand("ocr",   "Extract text from image"),
    BotCommand("ping",  "Latency check"),
    BotCommand("help",  "Help (add a topic for AI summary)"),
]

OWNER_EXTRA = [
    BotCommand("owner",      "Owner panel"),
    BotCommand("stats",      "Bot statistics"),
    BotCommand("logs",       "Recent logs"),
    BotCommand("users",      "Active user count"),
    BotCommand("announce",   "Broadcast to all users"),
    BotCommand("setchannel", "Set force-join channel"),
    BotCommand("ban",        "Ban a user id"),
    BotCommand("unban",      "Unban a user id"),
    BotCommand("live",       "Toggle live response"),
    BotCommand("speak",      "Speak as bot in a chat"),
    BotCommand("grant",      "Grant speak access"),
    BotCommand("revoke",     "Revoke speak access"),
    BotCommand("restart",    "Restart the bot process"),
    BotCommand("mkey",       "Set translation/OCR engine key"),
    BotCommand("mlimit",     "Set translation/OCR daily per-user limit"),
    BotCommand("addmodel",   "Add model from last /key as provider"),
    BotCommand("delprovider","Remove a custom provider"),
    BotCommand("providers",  "List active providers"),
]


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        await update.effective_message.reply_text("Owner only.")
        return
    msg = await update.effective_message.reply_text(
        "<b>Restart initiated</b>\n"
        "<i>The bot is shutting down and will be respawned by the host…</i>\n"
        "You will receive a confirmation here once it is back online.",
        parse_mode=ParseMode.HTML,
    )
    try:
        await db.set_setting("restart_pending", json.dumps({
            "chat_id": msg.chat_id, "message_id": msg.message_id, "ts": int(time.time()),
        }))
    except Exception:
        pass
    # Give Telegram a moment to deliver the message before exit.
    async def _bye():
        await asyncio.sleep(1.2)
        import os as _os
        _os._exit(0)
    asyncio.create_task(_bye())


async def notify_restart_complete(app: Application):
    """Called on startup: if a restart was requested, edit the message to success."""
    try:
        raw = await db.get_setting("restart_pending", "")
        if not raw:
            return
        await db.set_setting("restart_pending", "")
        data = json.loads(raw)
        dt = int(time.time()) - int(data.get("ts", 0))
        await app.bot.edit_message_text(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
            text=(
                "<b>Restart successful</b>\n"
                f"<i>Bot is back online in {dt}s and ready to serve.</i>"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def setup_bot_commands(app: Application):
    try:
        disabled = await _disabled_set()
        user_cmds = [c for c in USER_COMMANDS if c.command not in disabled]
        owner_extra = [c for c in OWNER_EXTRA if c.command not in disabled]
        await app.bot.set_my_commands(user_cmds, scope=BotCommandScopeDefault())
        if OWNER_ID:
            await app.bot.set_my_commands(
                user_cmds + owner_extra, scope=BotCommandScopeChat(OWNER_ID),
            )
        # Also give granted speak users the /speak command
        for u, _ in await db.list_speak_grants():
            try:
                extra = [BotCommand("speak", "Speak as bot")] if "speak" not in disabled else []
                await app.bot.set_my_commands(
                    user_cmds + extra,
                    scope=BotCommandScopeChat(u),
                )
            except Exception:
                pass
    except Exception:
        pass


async def _gate_disabled(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs in group=-2 before any command handler. Blocks disabled commands for non-owners."""
    msg = update.effective_message
    if not msg or not msg.text:
        return
    text = msg.text.strip()
    if not text.startswith("/"):
        return
    cmd = text[1:].split()[0].split("@", 1)[0].lower()
    uid = update.effective_user.id if update.effective_user else 0
    if is_owner(uid):
        return
    disabled = await _disabled_set()
    if cmd in disabled:
        try:
            await msg.reply_text("This command is currently disabled by the owner.")
        except Exception:
            pass
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop



def register_handlers(app: Application):
    # Global gate: blocks disabled commands for non-owners (highest priority).
    app.add_handler(
        MessageHandler(filters.COMMAND, _gate_disabled),
        group=-2,
    )

    # User
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CommandHandler("menu",  cmd_menu))
    app.add_handler(CommandHandler("ping",  cmd_ping))
    app.add_handler(CommandHandler("key",   cmd_key))
    app.add_handler(CommandHandler("tryke", cmd_tryke))
    app.add_handler(CommandHandler("dl",    cmd_dl))
    app.add_handler(CommandHandler("dla",   cmd_dla))

    for k in list(REGISTRY.keys()):
        app.add_handler(CommandHandler(k, make_provider_handler(k)))

    # Owner
    app.add_handler(CommandHandler("owner",      cmd_owner))
    app.add_handler(CommandHandler("stats",      cmd_stats))
    app.add_handler(CommandHandler("logs",       cmd_logs))
    app.add_handler(CommandHandler("users",      cmd_users))
    app.add_handler(CommandHandler("setchannel", cmd_setchannel))
    app.add_handler(CommandHandler("ban",        cmd_ban))
    app.add_handler(CommandHandler("unban",      cmd_unban))
    app.add_handler(CommandHandler("announce",   cmd_announce))
    app.add_handler(CommandHandler("cancel",     cmd_cancel))
    app.add_handler(CommandHandler("live",       cmd_live))
    app.add_handler(CommandHandler("speak",      cmd_speak))
    app.add_handler(CommandHandler("grant",      cmd_grant))
    app.add_handler(CommandHandler("revoke",     cmd_revoke))
    app.add_handler(CommandHandler("restart",    cmd_restart))
    app.add_handler(CommandHandler("addmodel",   cmd_addmodel))
    app.add_handler(CommandHandler("addprovider", cmd_addprovider))
    app.add_handler(CommandHandler("delprovider", cmd_delprovider))
    app.add_handler(CommandHandler("providers",   cmd_providers))

    # Tool-packs
    _textenc.register(app)
    _language.register(app)
    _photo.register(app)
    _shorten.register(app)
    _stylish.register(app)
    _translate.register(app)
    _ocr.register(app)

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(InlineQueryHandler(on_inline_query))

    # Catches the owner's next message of ANY type after arming a broadcast.
    # Must run BEFORE on_text (lower group number = higher priority).
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, on_any_owner_message),
        group=-1,
    )

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
