"""Extra utility tools: /info, /m2t, /time, /vnote.

Each function returns plain Python values / paths. The handlers in
`bot/handlers.py` wire them up to Telegram messages.
"""
from __future__ import annotations

import asyncio
import calendar
import io
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ---------------------------------------------------------------------------
# /time  — country code -> timezone map (popular subset; covers ~all common
# requests). Falls back to friendly error for unknown codes.
# ---------------------------------------------------------------------------
COUNTRY_TZ: dict[str, tuple[str, str, str]] = {
    # code : (display name, flag emoji, IANA timezone)
    "bd": ("Bangladesh", "🇧🇩", "Asia/Dhaka"),
    "in": ("India", "🇮🇳", "Asia/Kolkata"),
    "pk": ("Pakistan", "🇵🇰", "Asia/Karachi"),
    "lk": ("Sri Lanka", "🇱🇰", "Asia/Colombo"),
    "np": ("Nepal", "🇳🇵", "Asia/Kathmandu"),
    "bt": ("Bhutan", "🇧🇹", "Asia/Thimphu"),
    "mv": ("Maldives", "🇲🇻", "Indian/Maldives"),
    "af": ("Afghanistan", "🇦🇫", "Asia/Kabul"),
    "us": ("United States", "🇺🇸", "America/New_York"),
    "ca": ("Canada", "🇨🇦", "America/Toronto"),
    "mx": ("Mexico", "🇲🇽", "America/Mexico_City"),
    "br": ("Brazil", "🇧🇷", "America/Sao_Paulo"),
    "ar": ("Argentina", "🇦🇷", "America/Argentina/Buenos_Aires"),
    "cl": ("Chile", "🇨🇱", "America/Santiago"),
    "co": ("Colombia", "🇨🇴", "America/Bogota"),
    "pe": ("Peru", "🇵🇪", "America/Lima"),
    "ve": ("Venezuela", "🇻🇪", "America/Caracas"),
    "uk": ("United Kingdom", "🇬🇧", "Europe/London"),
    "gb": ("United Kingdom", "🇬🇧", "Europe/London"),
    "ie": ("Ireland", "🇮🇪", "Europe/Dublin"),
    "fr": ("France", "🇫🇷", "Europe/Paris"),
    "de": ("Germany", "🇩🇪", "Europe/Berlin"),
    "es": ("Spain", "🇪🇸", "Europe/Madrid"),
    "pt": ("Portugal", "🇵🇹", "Europe/Lisbon"),
    "it": ("Italy", "🇮🇹", "Europe/Rome"),
    "nl": ("Netherlands", "🇳🇱", "Europe/Amsterdam"),
    "be": ("Belgium", "🇧🇪", "Europe/Brussels"),
    "ch": ("Switzerland", "🇨🇭", "Europe/Zurich"),
    "at": ("Austria", "🇦🇹", "Europe/Vienna"),
    "se": ("Sweden", "🇸🇪", "Europe/Stockholm"),
    "no": ("Norway", "🇳🇴", "Europe/Oslo"),
    "fi": ("Finland", "🇫🇮", "Europe/Helsinki"),
    "dk": ("Denmark", "🇩🇰", "Europe/Copenhagen"),
    "pl": ("Poland", "🇵🇱", "Europe/Warsaw"),
    "cz": ("Czech Republic", "🇨🇿", "Europe/Prague"),
    "hu": ("Hungary", "🇭🇺", "Europe/Budapest"),
    "ro": ("Romania", "🇷🇴", "Europe/Bucharest"),
    "gr": ("Greece", "🇬🇷", "Europe/Athens"),
    "ua": ("Ukraine", "🇺🇦", "Europe/Kyiv"),
    "ru": ("Russia", "🇷🇺", "Europe/Moscow"),
    "tr": ("Turkey", "🇹🇷", "Europe/Istanbul"),
    "il": ("Israel", "🇮🇱", "Asia/Jerusalem"),
    "sa": ("Saudi Arabia", "🇸🇦", "Asia/Riyadh"),
    "ae": ("UAE", "🇦🇪", "Asia/Dubai"),
    "qa": ("Qatar", "🇶🇦", "Asia/Qatar"),
    "kw": ("Kuwait", "🇰🇼", "Asia/Kuwait"),
    "om": ("Oman", "🇴🇲", "Asia/Muscat"),
    "ir": ("Iran", "🇮🇷", "Asia/Tehran"),
    "iq": ("Iraq", "🇮🇶", "Asia/Baghdad"),
    "jo": ("Jordan", "🇯🇴", "Asia/Amman"),
    "lb": ("Lebanon", "🇱🇧", "Asia/Beirut"),
    "sy": ("Syria", "🇸🇾", "Asia/Damascus"),
    "eg": ("Egypt", "🇪🇬", "Africa/Cairo"),
    "ma": ("Morocco", "🇲🇦", "Africa/Casablanca"),
    "dz": ("Algeria", "🇩🇿", "Africa/Algiers"),
    "tn": ("Tunisia", "🇹🇳", "Africa/Tunis"),
    "ng": ("Nigeria", "🇳🇬", "Africa/Lagos"),
    "ke": ("Kenya", "🇰🇪", "Africa/Nairobi"),
    "za": ("South Africa", "🇿🇦", "Africa/Johannesburg"),
    "et": ("Ethiopia", "🇪🇹", "Africa/Addis_Ababa"),
    "gh": ("Ghana", "🇬🇭", "Africa/Accra"),
    "cn": ("China", "🇨🇳", "Asia/Shanghai"),
    "hk": ("Hong Kong", "🇭🇰", "Asia/Hong_Kong"),
    "tw": ("Taiwan", "🇹🇼", "Asia/Taipei"),
    "jp": ("Japan", "🇯🇵", "Asia/Tokyo"),
    "kr": ("South Korea", "🇰🇷", "Asia/Seoul"),
    "kp": ("North Korea", "🇰🇵", "Asia/Pyongyang"),
    "mn": ("Mongolia", "🇲🇳", "Asia/Ulaanbaatar"),
    "vn": ("Vietnam", "🇻🇳", "Asia/Ho_Chi_Minh"),
    "th": ("Thailand", "🇹🇭", "Asia/Bangkok"),
    "my": ("Malaysia", "🇲🇾", "Asia/Kuala_Lumpur"),
    "sg": ("Singapore", "🇸🇬", "Asia/Singapore"),
    "id": ("Indonesia", "🇮🇩", "Asia/Jakarta"),
    "ph": ("Philippines", "🇵🇭", "Asia/Manila"),
    "kh": ("Cambodia", "🇰🇭", "Asia/Phnom_Penh"),
    "la": ("Laos", "🇱🇦", "Asia/Vientiane"),
    "mm": ("Myanmar", "🇲🇲", "Asia/Yangon"),
    "au": ("Australia", "🇦🇺", "Australia/Sydney"),
    "nz": ("New Zealand", "🇳🇿", "Pacific/Auckland"),
    "fj": ("Fiji", "🇫🇯", "Pacific/Fiji"),
}

_MONTHS = ["January","February","March","April","May","June",
           "July","August","September","October","November","December"]


def resolve_country(code: str) -> tuple[str, str, ZoneInfo] | None:
    c = (code or "").strip().lower()
    info = COUNTRY_TZ.get(c)
    if not info:
        return None
    name, flag, tz_name = info
    try:
        return name, flag, ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return None


def build_time_card(code: str, year: int | None = None, month: int | None = None) -> tuple[str, list[list[tuple[str, str]]]] | None:
    """Returns (text, keyboard_rows) where keyboard_rows is a list of rows,
    each row a list of (button_label, callback_data) tuples.

    callback_data is "noop" for the day cells (they are display-only)
    and "time:<code>:<y>:<m>:<delta>" for nav buttons.
    """
    resolved = resolve_country(code)
    if not resolved:
        return None
    name, flag, tz = resolved
    now = datetime.now(tz)
    y = year or now.year
    m = month or now.month
    # Header text — kept simple, no images required.
    text = (
        f"{flag} <b>{name}</b>\n"
        f"🕒 <code>{now.strftime('%I:%M:%S %p')}</code>\n"
        f"📅 <code>{now.strftime('%d %B, %Y  %A')}</code>"
    )
    # Calendar grid (Monday-first like the screenshot).
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdayscalendar(y, m)
    rows: list[list[tuple[str, str]]] = []
    header_label = f"{_MONTHS[m-1]} {y} 🟢" if (y == now.year and m == now.month) else f"{_MONTHS[m-1]} {y}"
    today_label = now.strftime("%d %B, %Y") if (y == now.year and m == now.month) else f"{calendar.monthrange(y, m)[1]} {_MONTHS[m-1]}, {y}"
    rows.append([(header_label, "noop"), (today_label, "noop")])
    rows.append([(d, "noop") for d in ["Mo","Tu","We","Th","Fr","Sa","Su"]])
    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append((" ", "noop"))
            elif day == now.day and y == now.year and m == now.month:
                row.append((f"·{day}·", "noop"))
            else:
                row.append((str(day), "noop"))
        rows.append(row)
    # Nav row
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    rows.append([
        ("‹", f"time:{code}:{py}:{pm}"),
        ("›", f"time:{code}:{ny}:{nm}"),
    ])
    return text, rows


# ---------------------------------------------------------------------------
# /m2t — text -> .txt file
# ---------------------------------------------------------------------------
def build_text_file(text: str) -> tuple[bytes, dict]:
    data = (text or "").encode("utf-8", errors="replace")
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    stats = {
        "messages": 1,
        "lines": max(1, lines),
        "characters": len(text or ""),
        "bytes": len(data),
    }
    return data, stats


# ---------------------------------------------------------------------------
# /vnote — square circular video note
# ---------------------------------------------------------------------------
_VNOTE_MAX_BYTES = 8 * 1024 * 1024  # Telegram practical limit for video notes
_VNOTE_MAX_SECONDS = 60
_VNOTE_SIZE = 384  # square px; Telegram standard 240/384/512


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


async def make_video_note(src_path: str) -> str:
    """Convert an arbitrary video into a Telegram-friendly square clip.

    Returns the path to the produced mp4. Raises RuntimeError on failure.
    """
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg not available on server")
    out = tempfile.NamedTemporaryFile(prefix="vnote_", suffix=".mp4", delete=False)
    out.close()
    # Square center-crop, scale to _VNOTE_SIZE, cap duration, drop audio bitrate.
    vf = (
        f"crop='min(iw\\,ih)':'min(iw\\,ih)',"
        f"scale={_VNOTE_SIZE}:{_VNOTE_SIZE}:flags=lanczos,"
        f"format=yuv420p"
    )
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-t", str(_VNOTE_MAX_SECONDS),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "64k", "-ac", "1",
        out.name,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        try: os.unlink(out.name)
        except Exception: pass
        raise RuntimeError((err or b"").decode("utf-8", "ignore")[-400:] or "ffmpeg failed")
    return out.name