"""Photo editing tools — /bg /enh /res
Uses Pillow (always-on). Background removal uses optional REMOVEBG_KEY
(remove.bg API, 50 free / month). Daily per-user quota: 10/tool.

All operations are CPU-light (Pillow only) so they run on Render free tier.
"""
from __future__ import annotations
import asyncio
import io
import os
from typing import Optional

import aiohttp
from PIL import Image, ImageEnhance, ImageFilter, UnidentifiedImageError
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)

from .. import db
from ..utils import safe_user_error

DAILY_LIMIT = 10
MAX_BYTES = 10 * 1024 * 1024  # 10 MB input cap


# ---------------- helpers ----------------------------------------------------
def _frame(title: str, body: str) -> str:
    return f"<b>{title}</b>\n━━━━━━━━━━━━━━━━━━\n{body}"


async def _get_replied_image_bytes(update: Update, context: ContextTypes.DEFAULT_TYPE
                                   ) -> Optional[bytes]:
    """Reply-target may be a photo or an image document."""
    src = update.effective_message.reply_to_message or update.effective_message
    file_id = None
    if src.photo:
        file_id = src.photo[-1].file_id
    elif src.document and (src.document.mime_type or "").startswith("image/"):
        if src.document.file_size and src.document.file_size > MAX_BYTES:
            return None
        file_id = src.document.file_id
    if not file_id:
        return None
    f = await context.bot.get_file(file_id)
    buf = io.BytesIO()
    await f.download_to_memory(buf)
    return buf.getvalue()


async def _quota_or_reply(update: Update, tool: str) -> bool:
    uid = update.effective_user.id
    ok, used = await db.quota_check_and_inc(uid, tool, DAILY_LIMIT)
    if not ok:
        await update.effective_message.reply_text(
            _frame("Daily limit reached",
                   f"You have used <b>{used}/{DAILY_LIMIT}</b> for <code>{tool}</code> today.\n"
                   f"<i>Try again after 00:00 UTC.</i>"),
            parse_mode=ParseMode.HTML)
        return False
    return True


# ---------------- /bg --------------------------------------------------------
async def _removebg_api(img_bytes: bytes, api_key: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        data = aiohttp.FormData()
        data.add_field("image_file", img_bytes, filename="in.png",
                       content_type="application/octet-stream")
        data.add_field("size", "auto")
        async with s.post(
            "https://api.remove.bg/v1.0/removebg",
            headers={"X-Api-Key": api_key}, data=data) as r:
            if r.status != 200:
                err = (await r.text())[:300]
                raise RuntimeError(f"remove.bg HTTP {r.status}: {err}")
            return await r.read()


async def cmd_bg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    img = await _get_replied_image_bytes(update, context)
    if not img:
        await msg.reply_text("Reply to a photo with <code>/bg</code> to remove its background.",
                             parse_mode=ParseMode.HTML); return
    api_key = (os.getenv("REMOVEBG_KEY") or "").strip()
    if not api_key:
        await msg.reply_text(_frame("Background Removal",
            "<i>Background removal is not configured.</i>\n"
            "Owner must set <code>REMOVEBG_KEY</code> env var "
            "(get a free key at <b>remove.bg</b> — 50 images/month free)."),
            parse_mode=ParseMode.HTML); return

    if not await _quota_or_reply(update, "bg"): return
    await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_PHOTO)
    try:
        out = await _removebg_api(img, api_key)
    except Exception:
        await msg.reply_text(_frame("Background Removal", safe_user_error("Background removal")),
                             parse_mode=ParseMode.HTML); return
    await msg.reply_document(document=io.BytesIO(out), filename="no-bg.png",
                             caption="<b>Background removed</b>", parse_mode=ParseMode.HTML)


# ---------------- /enh -------------------------------------------------------
def _enhance(img_bytes: bytes) -> bytes:
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    # subtle, photo-friendly enhancement
    im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=140, threshold=3))
    im = ImageEnhance.Contrast(im).enhance(1.12)
    im = ImageEnhance.Color(im).enhance(1.15)
    im = ImageEnhance.Brightness(im).enhance(1.04)
    im = ImageEnhance.Sharpness(im).enhance(1.3)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


async def cmd_enh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    img = await _get_replied_image_bytes(update, context)
    if not img:
        await msg.reply_text("Reply to a photo with <code>/enh</code> to enhance it.",
                             parse_mode=ParseMode.HTML); return
    if not await _quota_or_reply(update, "enh"): return
    await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_PHOTO)
    try:
        out = await asyncio.to_thread(_enhance, img)
    except UnidentifiedImageError:
        await msg.reply_text("That doesn't look like a valid image."); return
    except Exception:
        await msg.reply_text(safe_user_error("Enhancement")); return
    await msg.reply_document(document=io.BytesIO(out), filename="enhanced.jpg",
                             caption="<b>Photo enhanced</b>", parse_mode=ParseMode.HTML)


# ---------------- /res -------------------------------------------------------
RESIZE_PRESETS = [
    ("YouTube Thumbnail", "yt",  1280, 720),
    ("YouTube Banner",    "ytb", 2560, 1440),
    ("Instagram Post",    "ig",  1080, 1080),
    ("Instagram Story",   "igs", 1080, 1920),
    ("Instagram Portrait","igp", 1080, 1350),
    ("LinkedIn Banner",   "li",  1584,  396),
    ("LinkedIn Post",     "lip", 1200,  627),
    ("Facebook Cover",    "fb",  820,   312),
    ("X / Twitter Header","tw",  1500,  500),
    ("X / Twitter Post",  "twp", 1200,  675),
    ("Telegram Sticker",  "tg",  512,   512),
    ("HD 1920x1080",      "hd",  1920, 1080),
    ("4K 3840x2160",      "uhd", 3840, 2160),
    ("Square 1024",       "sq",  1024, 1024),
]

# user_id -> raw image bytes (small RAM cache, single-use)
_RES_CACHE: dict[int, bytes] = {}


def _resize(img_bytes: bytes, w: int, h: int) -> bytes:
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    # smart fit-crop centered
    src_w, src_h = im.size
    src_ratio = src_w / src_h
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        new_h = src_h
        new_w = int(src_h * dst_ratio)
        x = (src_w - new_w) // 2
        im = im.crop((x, 0, x + new_w, new_h))
    else:
        new_w = src_w
        new_h = int(src_w / dst_ratio)
        y = (src_h - new_h) // 2
        im = im.crop((0, y, new_w, y + new_h))
    im = im.resize((w, h), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


_RES_PER_PAGE = 6


def _res_kb(page: int = 0) -> InlineKeyboardMarkup:
    total = len(RESIZE_PRESETS)
    pages = (total + _RES_PER_PAGE - 1) // _RES_PER_PAGE
    page = page % pages
    start = page * _RES_PER_PAGE
    chunk = RESIZE_PRESETS[start:start + _RES_PER_PAGE]
    rows = []
    for label, key, w, h in chunk:
        rows.append([InlineKeyboardButton(
            f"{label} ({w}×{h})", callback_data=f"res:s:{key}")])
    if pages > 1:
        rows.append([
            InlineKeyboardButton("« Prev", callback_data=f"res:p:{(page-1) % pages}"),
            InlineKeyboardButton(f"{page+1}/{pages}", callback_data="res:noop"),
            InlineKeyboardButton("Next »", callback_data=f"res:p:{(page+1) % pages}"),
        ])
    rows.append([InlineKeyboardButton("Close", callback_data="res:close")])
    return InlineKeyboardMarkup(rows)


async def cmd_res(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    img = await _get_replied_image_bytes(update, context)
    if not img:
        await msg.reply_text("Reply to a photo with <code>/res</code> to resize it.",
                             parse_mode=ParseMode.HTML); return
    _RES_CACHE[update.effective_user.id] = img
    await msg.reply_text(
        _frame("Resize Image", "Choose a target size:"),
        parse_mode=ParseMode.HTML, reply_markup=_res_kb(0))


async def on_res_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try: await q.answer()
    except Exception: pass
    data = q.data or ""
    uid = q.from_user.id
    if data == "res:noop":
        return
    if data == "res:close":
        try: await q.message.delete()
        except Exception: pass
        _RES_CACHE.pop(uid, None); return
    if data.startswith("res:p:"):
        try: page = int(data.split(":")[2])
        except Exception: page = 0
        try: await q.edit_message_reply_markup(reply_markup=_res_kb(page))
        except Exception: pass
        return
    # res:s:<key>  (legacy res:<key> still tolerated)
    key = data.split(":")[-1]
    preset = next(((w, h, lbl) for lbl, k, w, h in RESIZE_PRESETS if k == key), None)
    if not preset:
        return
    w, h, lbl = preset
    img = _RES_CACHE.get(uid)
    if not img:
        try: await q.edit_message_text("Session expired. Send /res again.")
        except Exception: pass
        return
    ok, used = await db.quota_check_and_inc(uid, "res", DAILY_LIMIT)
    if not ok:
        try:
            await q.edit_message_text(_frame("Daily limit reached",
                f"Used <b>{used}/{DAILY_LIMIT}</b> resizes today."),
                parse_mode=ParseMode.HTML)
        except Exception: pass
        return
    try:
        out = await asyncio.to_thread(_resize, img, w, h)
    except Exception:
        try: await q.edit_message_text(safe_user_error("Resize"))
        except Exception: pass
        return
    await context.bot.send_chat_action(q.message.chat_id, ChatAction.UPLOAD_PHOTO)
    await q.message.reply_document(
        document=io.BytesIO(out), filename=f"resized_{w}x{h}.jpg",
        caption=f"<b>Resized:</b> {lbl} — <code>{w}×{h}</code>",
        parse_mode=ParseMode.HTML)


# ---------------- registration ----------------------------------------------
def register(app: Application):
    app.add_handler(CommandHandler("bg",  cmd_bg))
    app.add_handler(CommandHandler("enh", cmd_enh))
    app.add_handler(CommandHandler("res", cmd_res))
    app.add_handler(CallbackQueryHandler(on_res_callback, pattern=r"^res:"))
