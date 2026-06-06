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
    MessageHandler, filters,
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
    # 1) Denoise first so sharpening doesn't amplify grain.
    #    MedianFilter(3) removes salt-and-pepper noise without blurring edges much.
    im = im.filter(ImageFilter.MedianFilter(size=3))
    #    SMOOTH softens remaining luminance noise (very mild).
    im = im.filter(ImageFilter.SMOOTH)
    # 2) Strong, edge-aware sharpen (UnsharpMask is noise-tolerant due to threshold).
    im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=4))
    im = im.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=2))
    # 3) Subtle tonal/colour boost.
    im = ImageEnhance.Contrast(im).enhance(1.15)
    im = ImageEnhance.Color(im).enhance(1.18)
    im = ImageEnhance.Brightness(im).enhance(1.04)
    im = ImageEnhance.Sharpness(im).enhance(1.45)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=93, optimize=True)
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
# user_id -> True while waiting for "WxH" custom-size input
_RES_CUSTOM_PENDING: dict[int, bool] = {}

MAX_DIM = 8000  # safety cap (pixels per side)


def _parse_dims(text: str):
    """Accept '800x600', '800X600', '800*600', '800 600', '800,600'."""
    if not text:
        return None
    import re as _re
    m = _re.search(r"(\d{2,5})\s*[x×*, ]\s*(\d{2,5})", text.strip().lower())
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if w < 16 or h < 16 or w > MAX_DIM or h > MAX_DIM:
        return None
    return w, h


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


def _resize_exact(img_bytes: bytes, w: int, h: int) -> bytes:
    """Resize to exact W×H, no crop. Pure scale — preserves whole image."""
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    im = im.resize((w, h), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=93, optimize=True)
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
    rows.append([
        InlineKeyboardButton("✏️ Custom Size", callback_data="res:custom"),
        InlineKeyboardButton("Close", callback_data="res:close"),
    ])
    return InlineKeyboardMarkup(rows)


async def cmd_res(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    img = await _get_replied_image_bytes(update, context)
    if not img:
        await msg.reply_text(
            "Reply to a photo with <code>/res</code> to resize it.\n"
            "Custom size: <code>/res 800x600</code>",
            parse_mode=ParseMode.HTML); return
    _RES_CACHE[update.effective_user.id] = img

    # Inline custom dims: /res 800x600
    args_text = " ".join(context.args or []) if context.args else ""
    dims = _parse_dims(args_text)
    if dims:
        w, h = dims
        uid = update.effective_user.id
        ok, used = await db.quota_check_and_inc(uid, "res", DAILY_LIMIT)
        if not ok:
            await msg.reply_text(_frame("Daily limit reached",
                f"Used <b>{used}/{DAILY_LIMIT}</b> resizes today."),
                parse_mode=ParseMode.HTML); return
        await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_PHOTO)
        try:
            out = await asyncio.to_thread(_resize_exact, img, w, h)
        except Exception:
            await msg.reply_text(safe_user_error("Resize")); return
        await msg.reply_document(
            document=io.BytesIO(out), filename=f"resized_{w}x{h}.jpg",
            caption=f"<b>Resized:</b> <code>{w}×{h}</code>",
            parse_mode=ParseMode.HTML)
        _RES_CACHE.pop(uid, None)
        return

    await msg.reply_text(
        _frame("Resize Image",
               "Choose a preset, or tap <b>Custom Size</b> for any W×H.\n"
               "Shortcut: <code>/res 800x600</code>"),
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
        _RES_CACHE.pop(uid, None)
        _RES_CUSTOM_PENDING.pop(uid, None)
        return
    if data == "res:custom":
        if uid not in _RES_CACHE:
            try: await q.edit_message_text("Session expired. Send /res again.")
            except Exception: pass
            return
        _RES_CUSTOM_PENDING[uid] = True
        try:
            await q.edit_message_text(
                _frame("Custom Size",
                       "Send your dimensions as <code>WIDTHxHEIGHT</code>\n"
                       "Example: <code>800x600</code>  •  <code>1920x1080</code>\n"
                       "Range: 16 – 8000 px.\n"
                       "<i>Send /cancel to abort.</i>"),
                parse_mode=ParseMode.HTML)
        except Exception:
            pass
        return
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


# ---------------- custom-size text follow-up ---------------------------------
async def on_res_custom_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.from_user:
        return
    uid = msg.from_user.id
    if not _RES_CUSTOM_PENDING.get(uid):
        return
    text = (msg.text or "").strip()
    if text.lower() in ("/cancel", "cancel"):
        _RES_CUSTOM_PENDING.pop(uid, None)
        await msg.reply_text("Cancelled.")
        return
    dims = _parse_dims(text)
    if not dims:
        await msg.reply_text(
            "Invalid size. Send like <code>800x600</code> (16–8000 px).",
            parse_mode=ParseMode.HTML)
        return
    img = _RES_CACHE.get(uid)
    if not img:
        _RES_CUSTOM_PENDING.pop(uid, None)
        await msg.reply_text("Session expired. Send /res again.")
        return
    w, h = dims
    ok, used = await db.quota_check_and_inc(uid, "res", DAILY_LIMIT)
    if not ok:
        _RES_CUSTOM_PENDING.pop(uid, None)
        await msg.reply_text(_frame("Daily limit reached",
            f"Used <b>{used}/{DAILY_LIMIT}</b> resizes today."),
            parse_mode=ParseMode.HTML); return
    await context.bot.send_chat_action(msg.chat_id, ChatAction.UPLOAD_PHOTO)
    try:
        out = await asyncio.to_thread(_resize_exact, img, w, h)
    except Exception:
        await msg.reply_text(safe_user_error("Resize")); return
    await msg.reply_document(
        document=io.BytesIO(out), filename=f"resized_{w}x{h}.jpg",
        caption=f"<b>Resized:</b> <code>{w}×{h}</code>",
        parse_mode=ParseMode.HTML)
    _RES_CACHE.pop(uid, None)
    _RES_CUSTOM_PENDING.pop(uid, None)


# ---------------- registration ----------------------------------------------
def register(app: Application):
    app.add_handler(CommandHandler("bg",  cmd_bg))
    app.add_handler(CommandHandler("enh", cmd_enh))
    app.add_handler(CommandHandler("res", cmd_res))
    app.add_handler(CallbackQueryHandler(on_res_callback, pattern=r"^res:"))
    # Custom-size follow-up. Group 1 so it never blocks other handlers.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_res_custom_message),
        group=1,
    )
