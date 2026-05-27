"""URL shortener tool — /short <url>

Tries multiple free shortener services in order:
  is.gd  ->  tinyurl  ->  da.gd  ->  clck.ru

No API key required. Reply-to-message supported.
"""
import re
import aiohttp
from urllib.parse import quote_plus
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def _extract_url(text: str) -> str | None:
    if not text:
        return None
    m = _URL_RE.search(text)
    if m:
        return m.group(0).rstrip(").,;]")
    t = text.strip()
    if t and not t.startswith("http"):
        # try prepending https
        if "." in t and " " not in t:
            return "https://" + t
    return None


async def _isgd(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(
            f"https://is.gd/create.php?format=simple&url={quote_plus(url)}",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            t = (await r.text()).strip()
            if r.status == 200 and t.startswith("http"):
                return t
    except Exception:
        return None
    return None


async def _tinyurl(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(
            f"https://tinyurl.com/api-create.php?url={quote_plus(url)}",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            t = (await r.text()).strip()
            if r.status == 200 and t.startswith("http"):
                return t
    except Exception:
        return None
    return None


async def _dagd(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(
            f"https://da.gd/s?url={quote_plus(url)}",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            t = (await r.text()).strip()
            if r.status == 200 and t.startswith("http"):
                return t
    except Exception:
        return None
    return None


async def _clck(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(
            f"https://clck.ru/--?url={quote_plus(url)}",
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            t = (await r.text()).strip()
            if r.status == 200 and t.startswith("http"):
                return t
    except Exception:
        return None
    return None


_PROVIDERS = [
    ("is.gd",    _isgd),
    ("TinyURL",  _tinyurl),
    ("da.gd",    _dagd),
    ("clck.ru",  _clck),
]


async def cmd_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = " ".join(context.args).strip() if context.args else ""
    if not text and msg.reply_to_message:
        text = (msg.reply_to_message.text or msg.reply_to_message.caption or "").strip()
    url = _extract_url(text)
    if not url:
        await msg.reply_text(
            "Usage: <code>/short &lt;url&gt;</code>\n"
            "Or reply to a message that contains a URL.",
            parse_mode=ParseMode.HTML,
        )
        return

    status = await msg.reply_text("Shortening...")
    results: list[tuple[str, str]] = []
    async with aiohttp.ClientSession() as session:
        for name, fn in _PROVIDERS:
            short = await fn(session, url)
            if short:
                results.append((name, short))

    if not results:
        try:
            await status.edit_text("All shortener services failed. Try again later.")
        except Exception:
            pass
        return

    lines = ["<b>Short URLs</b>", f"<i>Original:</i> <code>{url[:120]}</code>", ""]
    for name, s in results:
        lines.append(f"• <b>{name}</b> — <code>{s}</code>")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Copy {name}", url=s)] for name, s in results[:4]
    ])
    try:
        await status.edit_text(
            "\n".join(lines), parse_mode=ParseMode.HTML,
            disable_web_page_preview=True, reply_markup=kb,
        )
    except Exception:
        await status.edit_text("\n".join(lines))


def register(app: Application):
    app.add_handler(CommandHandler("short", cmd_short))
