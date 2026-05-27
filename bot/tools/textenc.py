"""Text & Encoding Tools — /en /de /text /wc
Premium inline-button driven UI. HTML-safe. Zero external deps.
"""
from __future__ import annotations
import base64
import codecs
import html
import urllib.parse
from uuid import uuid4

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ---------------- in-memory payload cache (small, OK on Render free) ----------
_CACHE: dict[str, str] = {}
_CACHE_ORDER: list[str] = []
_CACHE_MAX = 500


def _stash(text: str) -> str:
    tid = uuid4().hex[:10]
    _CACHE[tid] = text
    _CACHE_ORDER.append(tid)
    if len(_CACHE_ORDER) > _CACHE_MAX:
        old = _CACHE_ORDER.pop(0)
        _CACHE.pop(old, None)
    return tid


def _get(tid: str) -> str | None:
    return _CACHE.get(tid)


# ---------------- helpers ----------------------------------------------------
def _arg_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    msg = update.effective_message
    if context.args:
        return " ".join(context.args).strip()
    if msg.reply_to_message and (msg.reply_to_message.text or msg.reply_to_message.caption):
        return (msg.reply_to_message.text or msg.reply_to_message.caption).strip()
    return ""


def _frame(title: str, body: str) -> str:
    line = "━" * 18
    return f"<b>{title}</b>\n{line}\n{body}"


# ---------------- ENCODERS ---------------------------------------------------
def _enc_base32(s: str) -> str: return base64.b32encode(s.encode()).decode()
def _enc_base64(s: str) -> str: return base64.b64encode(s.encode()).decode()
def _enc_base85(s: str) -> str: return base64.b85encode(s.encode()).decode()
def _enc_ascii85(s: str) -> str: return base64.a85encode(s.encode()).decode()
def _enc_binary(s: str) -> str: return " ".join(f"{b:08b}" for b in s.encode())
def _enc_hex(s: str) -> str: return s.encode().hex()
def _enc_octal(s: str) -> str: return " ".join(f"{b:o}" for b in s.encode())
def _enc_unicode(s: str) -> str: return " ".join(f"U+{ord(c):04X}" for c in s)
def _enc_rot13(s: str) -> str: return codecs.encode(s, "rot_13")
def _enc_url(s: str) -> str: return urllib.parse.quote(s, safe="")
def _enc_morse(s: str) -> str:
    table = {
        "A":".-","B":"-...","C":"-.-.","D":"-..","E":".","F":"..-.","G":"--.","H":"....",
        "I":"..","J":".---","K":"-.-","L":".-..","M":"--","N":"-.","O":"---","P":".--.",
        "Q":"--.-","R":".-.","S":"...","T":"-","U":"..-","V":"...-","W":".--","X":"-..-",
        "Y":"-.--","Z":"--..","0":"-----","1":".----","2":"..---","3":"...--","4":"....-",
        "5":".....","6":"-....","7":"--...","8":"---..","9":"----.",
        ".":".-.-.-",",":"--..--","?":"..--..","'":".----.","!":"-.-.--","/":"-..-.",
        "(":"-.--.",")":"-.--.-","&":".-...",":":"---...",";":"-.-.-.",
        "=":"-...-","+":".-.-.","-":"-....-","_":"..--.-","\"":".-..-.",
        "$":"...-..-","@":".--.-.",
    }
    return " ".join(table.get(c.upper(), "?") for c in s if c != " ").replace("? ", " / ")


ENCODERS = [
    ("Base64",  "b64", _enc_base64),
    ("Base32",  "b32", _enc_base32),
    ("Base85",  "b85", _enc_base85),
    ("ASCII85", "a85", _enc_ascii85),
    ("Hex",     "hex", _enc_hex),
    ("Binary",  "bin", _enc_binary),
    ("Octal",   "oct", _enc_octal),
    ("Unicode", "uni", _enc_unicode),
    ("ROT13",   "rot", _enc_rot13),
    ("URL",     "url", _enc_url),
    ("Morse",   "mor", _enc_morse),
]


# ---------------- DECODERS ---------------------------------------------------
def _dec_base64(s: str) -> str: return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")
def _dec_base32(s: str) -> str: return base64.b32decode(s.upper() + "=" * (-len(s) % 8)).decode("utf-8", "replace")
def _dec_base85(s: str) -> str: return base64.b85decode(s).decode("utf-8", "replace")
def _dec_ascii85(s: str) -> str: return base64.a85decode(s).decode("utf-8", "replace")
def _dec_hex(s: str) -> str: return bytes.fromhex(s.replace(" ", "")).decode("utf-8", "replace")
def _dec_binary(s: str) -> str:
    parts = [p for p in s.split() if p]
    return bytes(int(p, 2) for p in parts).decode("utf-8", "replace")
def _dec_octal(s: str) -> str:
    return bytes(int(p, 8) for p in s.split() if p).decode("utf-8", "replace")
def _dec_unicode(s: str) -> str:
    out = []
    for tok in s.replace(",", " ").split():
        t = tok.upper().replace("U+", "").replace("0X", "")
        out.append(chr(int(t, 16)))
    return "".join(out)
def _dec_rot13(s: str) -> str: return codecs.decode(s, "rot_13")
def _dec_url(s: str) -> str: return urllib.parse.unquote(s)


DECODERS = [
    ("Base64",  "b64", _dec_base64),
    ("Base32",  "b32", _dec_base32),
    ("Base85",  "b85", _dec_base85),
    ("ASCII85", "a85", _dec_ascii85),
    ("Hex",     "hex", _dec_hex),
    ("Binary",  "bin", _dec_binary),
    ("Octal",   "oct", _dec_octal),
    ("Unicode", "uni", _dec_unicode),
    ("ROT13",   "rot", _dec_rot13),
    ("URL",     "url", _dec_url),
]


# ---------------- TRANSFORMS -------------------------------------------------
def _t_upper(s: str) -> str: return s.upper()
def _t_lower(s: str) -> str: return s.lower()
def _t_cap(s: str) -> str: return s.capitalize()
def _t_title(s: str) -> str: return s.title()
def _t_reverse(s: str) -> str: return s[::-1]
def _t_swap(s: str) -> str: return s.swapcase()
def _t_alt(s: str) -> str:
    out = []
    up = False
    for c in s:
        out.append(c.upper() if up else c.lower())
        if c.isalpha(): up = not up
    return "".join(out)


TRANSFORMS = [
    ("UPPERCASE", "up",  _t_upper),
    ("lowercase", "lo",  _t_lower),
    ("Capitalize","cap", _t_cap),
    ("Title Case","ti",  _t_title),
    ("Reverse",   "rev", _t_reverse),
    ("sWAPcASE",  "sw",  _t_swap),
    ("aLtCaSe",   "alt", _t_alt),
]


# ---------------- Keyboards --------------------------------------------------
def _kb(items: list[tuple[str, str, callable]], kind: str, tid: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for label, key, _ in items:
        row.append(InlineKeyboardButton(label, callback_data=f"tx:{kind}:{key}:{tid}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("Close", callback_data=f"tx:close::{tid}")])
    return InlineKeyboardMarkup(rows)


# ---------------- Commands ---------------------------------------------------
async def cmd_en(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = _arg_text(update, context)
    if not txt:
        await update.effective_message.reply_text(
            "Usage: <code>/en your text</code>  (or reply to a message)",
            parse_mode=ParseMode.HTML); return
    tid = _stash(txt)
    body = (f"<b>Source:</b> <code>{html.escape(txt[:300])}</code>\n"
            f"Choose an <b>encoding</b> format:")
    await update.effective_message.reply_text(
        _frame("Encoder", body), parse_mode=ParseMode.HTML,
        reply_markup=_kb(ENCODERS, "e", tid))


async def cmd_de(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = _arg_text(update, context)
    if not txt:
        await update.effective_message.reply_text(
            "Usage: <code>/de encoded_text</code>  (or reply to a message)",
            parse_mode=ParseMode.HTML); return
    tid = _stash(txt)
    body = (f"<b>Source:</b> <code>{html.escape(txt[:300])}</code>\n"
            f"Choose a <b>decoding</b> format:")
    await update.effective_message.reply_text(
        _frame("Decoder", body), parse_mode=ParseMode.HTML,
        reply_markup=_kb(DECODERS, "d", tid))


async def cmd_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = _arg_text(update, context)
    if not txt:
        await update.effective_message.reply_text(
            "Usage: <code>/text your text</code>  (or reply to a message)",
            parse_mode=ParseMode.HTML); return
    tid = _stash(txt)
    body = (f"<b>Source:</b> <code>{html.escape(txt[:300])}</code>\n"
            f"Choose a <b>transformation</b>:")
    await update.effective_message.reply_text(
        _frame("Text Transform", body), parse_mode=ParseMode.HTML,
        reply_markup=_kb(TRANSFORMS, "t", tid))


async def cmd_wc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = _arg_text(update, context)
    if not txt:
        await update.effective_message.reply_text(
            "Usage: <code>/wc your text</code>  (or reply to a message)",
            parse_mode=ParseMode.HTML); return
    words = len(txt.split())
    chars = len(txt)
    chars_ns = len(txt.replace(" ", "").replace("\n", ""))
    lines = txt.count("\n") + 1
    body = (f"<b>Words:</b> <code>{words}</code>\n"
            f"<b>Characters:</b> <code>{chars}</code>\n"
            f"<b>Characters (no spaces):</b> <code>{chars_ns}</code>\n"
            f"<b>Lines:</b> <code>{lines}</code>")
    await update.effective_message.reply_text(
        _frame("Word Count", body), parse_mode=ParseMode.HTML)


# ---------------- Callback ---------------------------------------------------
async def on_tx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass
    data = q.data or ""
    parts = data.split(":", 3)
    if len(parts) < 4 or parts[0] != "tx":
        return
    _, kind, key, tid = parts

    if kind == "close":
        try: await q.message.delete()
        except Exception: pass
        return

    src = _get(tid)
    if src is None:
        try: await q.edit_message_text("Session expired. Run the command again.")
        except Exception: pass
        return

    table = {"e": (ENCODERS, "Encoded"), "d": (DECODERS, "Decoded"), "t": (TRANSFORMS, "Transformed")}
    if kind not in table:
        return
    items, title = table[kind]
    func = next((f for lbl, k, f in items if k == key), None)
    label = next((lbl for lbl, k, _ in items if k == key), key)
    if not func:
        return
    try:
        out = func(src)
    except Exception as e:
        out = "[conversion failed]"
    body = (f"<b>Format:</b> <code>{html.escape(label)}</code>\n"
            f"<b>Source:</b> <code>{html.escape(src[:200])}</code>\n\n"
            f"<b>Result:</b>\n<pre>{html.escape(out[:3500])}</pre>")
    try:
        await q.edit_message_text(_frame(title, body), parse_mode=ParseMode.HTML,
                                  reply_markup=_kb(items, kind, tid))
    except Exception:
        await q.message.reply_text(_frame(title, body), parse_mode=ParseMode.HTML)


# ---------------- Registration ----------------------------------------------
def register(app: Application):
    app.add_handler(CommandHandler("en",   cmd_en))
    app.add_handler(CommandHandler("de",   cmd_de))
    app.add_handler(CommandHandler("text", cmd_text))
    app.add_handler(CommandHandler("wc",   cmd_wc))
    app.add_handler(CallbackQueryHandler(on_tx_callback, pattern=r"^tx:"))


COMMANDS_HELP = (
    "<b>Text and Encoding Tools</b>\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "<b>/en</b> &lt;text&gt; — Encode (Base64, Base32, Base85, ASCII85, Hex, Binary, "
    "Octal, Unicode, ROT13, URL, Morse)\n"
    "<b>/de</b> &lt;text&gt; — Decode from any of the above formats\n"
    "<b>/text</b> &lt;text&gt; — UPPER/lower/Title/Reverse/sWAP/aLt case\n"
    "<b>/wc</b> &lt;text&gt; — Word, char, and line count\n\n"
    "<i>Tip: reply to any message with the command to apply it to that text.</i>"
)
