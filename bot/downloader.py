"""Advanced async video downloader using yt-dlp.

Highlights
----------
* Per-task temp dir, concurrency-safe.
* Smart format ladder — try compact mp4 first, fall back gracefully without
  blowing past Telegram's upload cap.
* Pre-flight size probe (no download) → skip impossible files early.
* Live progress callbacks (% + size + speed + ETA) for the bot's status edit.
* Multi-client YouTube extractor (tv_embedded → ios → mweb → web_safari)
  to bypass the most common bot-checks; cookies file supported.
* Resilient error mapping → friendly user-facing text.
"""
import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
import yt_dlp

# Telegram bot upload cap (~50 MB for regular bots)
MAX_BYTES = 49 * 1024 * 1024
_VIDEO_PROBE_TIMEOUT = 20

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_SUPPORTED_HOSTS = (
    "facebook.com",
    "fb.watch",
    "instagram.com",
    "tiktok.com",
)

_UA_IOS = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
    "Mobile/15E148 Safari/604.1"
)

_UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def detect_url(text: str) -> Optional[str]:
    if not text:
        return None
    m = URL_RE.search(text)
    if not m:
        return None
    url = m.group(0).rstrip(").,]>")
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in _SUPPORTED_HOSTS):
        return None
    return url


def _cookies_path() -> Optional[str]:
    cookies = os.getenv("YT_COOKIES_FILE", "").strip()
    if not cookies:
        default = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "youtube_cookies.txt",
        )
        if os.path.exists(default):
            cookies = default
    return cookies if cookies and os.path.exists(cookies) else None


def platform_from_url(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith("tiktok.com"):
        return "tiktok"
    if host.endswith("instagram.com"):
        return "instagram"
    if host.endswith("facebook.com") or host == "fb.watch":
        return "facebook"
    return "generic"


def _normalize_url(url: str) -> str:
    low = (url or "").lower()
    needs_expand = any([
        "vt.tiktok.com/" in low,
        "vm.tiktok.com/" in low,
        "fb.watch/" in low,
        "facebook.com/share/" in low,
        "facebook.com/reel/" in low,
        "instagram.com/share/" in low,
    ])
    if not needs_expand:
        return url
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": _UA_DESKTOP,
                "Referer": "https://www.google.com/",
            },
            timeout=15,
            allow_redirects=True,
        )
        final_url = (resp.url or "").strip()
        if final_url and final_url.startswith("http"):
            return final_url
    except Exception:
        pass
    return url


def _extract_share_target(url: str) -> str:
    """Best-effort expansion for short/share links before yt-dlp touches them."""
    normalized = _normalize_url(url)
    low = (normalized or "").lower()
    if not any(marker in low for marker in (
        "facebook.com/share/",
        "facebook.com/reel/",
        "facebook.com/reels/",
        "fb.watch/",
        "instagram.com/share/",
        "instagram.com/reel/",
        "instagram.com/reels/",
    )):
        return normalized
    try:
        # HEAD first — faster, avoids hanging on huge HTML bodies.
        try:
            resp = requests.head(
                normalized,
                headers={
                    "User-Agent": _UA_IOS,
                    "Referer": "https://www.facebook.com/",
                },
                timeout=10,
                allow_redirects=True,
            )
        except Exception:
            resp = requests.get(
                normalized,
                headers={
                    "User-Agent": _UA_IOS,
                    "Referer": "https://www.facebook.com/",
                },
                timeout=10,
                allow_redirects=True,
                stream=True,
            )
            try:
                resp.close()
            except Exception:
                pass
        final_url = (resp.url or "").strip()
        content_type = (resp.headers.get("content-type") or "").lower()
        if resp.status_code < 400 and final_url.startswith("http") and "text/html" not in content_type:
            return final_url
        if resp.status_code < 400 and final_url.startswith("http") and final_url != normalized:
            return final_url
    except Exception:
        pass
    return normalized


def _run_ffmpeg(args: list[str], timeout: int = 240) -> None:
    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(err or "ffmpeg failed")


def _looks_like_html(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(2048).lower()
    except Exception:
        return False
    return any(marker in head for marker in (
        b"<!doctype html",
        b"<html",
        b"<head",
        b"<body",
        b"facebook helps you connect",
    ))


def _probe_media(path: str) -> dict:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,pix_fmt",
            "-of", "default=noprint_wrappers=1:nokey=0",
            path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=_VIDEO_PROBE_TIMEOUT,
        check=False,
    )
    data: dict[str, str | int] = {}
    if proc.returncode != 0:
        return data
    for line in proc.stdout.decode("utf-8", errors="ignore").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k in {"width", "height"}:
            try:
                data[k] = int(v)
            except Exception:
                pass
        else:
            data[k] = v.strip()
    return data


def _ensure_telegram_media(path: str, audio_only: bool) -> str:
    """Convert/remux media to Telegram-friendly formats when needed."""
    if not path or not os.path.exists(path):
        return path
    if _looks_like_html(path):
        raise RuntimeError(
            "Downloaded page instead of media stream. The site likely returned a login/consent page."
        )

    base, ext = os.path.splitext(path)
    ext = ext.lower()

    if audio_only:
        if ext == ".mp3":
            return path
        target = base + ".mp3"
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", path,
            "-vn", "-c:a", "libmp3lame", "-b:a", "128k",
            target,
        ], timeout=180)
    else:
        meta = _probe_media(path)
        needs_full_transcode = (
            ext not in {".mp4", ".m4v", ".mov"}
            or meta.get("codec_name") != "h264"
            or meta.get("pix_fmt") != "yuv420p"
            or not meta.get("width")
            or not meta.get("height")
        )
        if needs_full_transcode:
            target = base + ".fixed.mp4"
            _run_ffmpeg([
                "ffmpeg", "-y", "-i", path,
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                "-pix_fmt", "yuv420p",
                "-profile:v", "baseline",
                "-level", "3.1",
                "-c:a", "aac", "-ac", "2", "-b:a", "128k",
                "-movflags", "+faststart",
                target,
            ], timeout=300)
        else:
            target = base + ".tg.mp4"
            try:
                _run_ffmpeg([
                    "ffmpeg", "-y", "-i", path,
                    "-map", "0:v:0", "-map", "0:a:0?",
                    "-c:v", "copy",
                    "-c:a", "aac", "-ac", "2", "-b:a", "128k",
                    "-movflags", "+faststart",
                    target,
                ], timeout=240)
            except Exception:
                target = base + ".recode.mp4"
                _run_ffmpeg([
                    "ffmpeg", "-y", "-i", path,
                    "-map", "0:v:0", "-map", "0:a:0?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                    "-pix_fmt", "yuv420p",
                    "-profile:v", "baseline",
                    "-level", "3.1",
                    "-c:a", "aac", "-ac", "2", "-b:a", "128k",
                    "-movflags", "+faststart",
                    target,
                ], timeout=300)

    if not os.path.exists(target):
        return path
    if os.path.getsize(target) > MAX_BYTES:
        try:
            os.remove(target)
        except Exception:
            pass
        raise RuntimeError("Converted file is still too large for Telegram.")
    try:
        if os.path.abspath(target) != os.path.abspath(path):
            os.remove(path)
    except Exception:
        pass
    return target


def _ydl_base(url: str) -> dict:
    opts: dict = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "concurrent_fragment_downloads": 1,
        "merge_output_format": "mp4",
        "http_headers": {
            "User-Agent": _UA_DESKTOP,
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_retries": 2,
    }
    platform = platform_from_url(url)
    if platform == "tiktok":
        opts["http_headers"].update({
            "Referer": "https://www.tiktok.com/",
            "Origin": "https://www.tiktok.com",
        })
    elif platform in {"facebook", "instagram"}:
        opts["http_headers"].update({
            "Referer": f"https://www.{platform}.com/",
        })
        opts["format_sort"] = [
            "hasvid",
            "quality",
            "res",
            "fps",
            "vcodec:h264",
            "acodec:aac",
            "ext:mp4:m4a",
        ]
    return opts


# Format ladder (descending preference). Each tier stays under MAX_BYTES.
_FORMAT_LADDER = [
    f"best[ext=mp4][filesize<=?{MAX_BYTES}][filesize_approx<=?{MAX_BYTES}]",
    f"best[filesize<=?{MAX_BYTES}][filesize_approx<=?{MAX_BYTES}]",
    "best[ext=mp4]/best",
    "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
    "bv*+ba/b",
    "best",
]

# Audio-only ladder
_AUDIO_LADDER = [
    f"ba[ext=m4a][filesize<=?{MAX_BYTES}][filesize_approx<=?{MAX_BYTES}]/"
    f"ba[filesize<=?{MAX_BYTES}][filesize_approx<=?{MAX_BYTES}]",
    "bestaudio[ext=m4a]/bestaudio/best",
    "best",
]


def _make_progress_hook(cb: Optional[Callable[[dict], None]]):
    if not cb:
        return None
    last = {"t": 0.0}

    def hook(d: dict):
        try:
            now = time.time()
            if d.get("status") == "downloading":
                if now - last["t"] < 1.2:  # throttle
                    return
                last["t"] = now
            cb({
                "status": d.get("status"),
                "downloaded": d.get("downloaded_bytes") or 0,
                "total": d.get("total_bytes") or d.get("total_bytes_estimate") or 0,
                "speed": d.get("speed") or 0,
                "eta": d.get("eta") or 0,
            })
        except Exception:
            pass
    return hook


def _probe(url: str) -> dict:
    """Extract metadata without downloading — used to skip oversized files."""
    url = _normalize_url(url)
    opts = _ydl_base(url)
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return info or {}


def _pick_best_size(info: dict) -> int:
    fmts = info.get("formats") or []
    best = 0
    for f in fmts:
        sz = f.get("filesize") or f.get("filesize_approx") or 0
        if sz and sz < MAX_BYTES and sz > best:
            best = sz
    return best


def _tikwm_download(url: str, workdir: str, audio_only: bool) -> Optional[dict]:
    """Reliable TikTok fallback via the public tikwm.com endpoint.
    Returns info dict on success, None on failure.
    """
    try:
        r = requests.post(
            "https://www.tikwm.com/api/",
            data={"url": url, "hd": "1"},
            headers={"User-Agent": _UA_DESKTOP},
            timeout=25,
        )
        j = r.json()
        if r.status_code != 200 or j.get("code") != 0:
            return None
        d = j.get("data") or {}
        if audio_only:
            media = d.get("music")
            ext = "mp3"
        else:
            media = d.get("hdplay") or d.get("play") or d.get("wmplay")
            ext = "mp4"
        if not media:
            return None
        if media.startswith("/"):
            media = "https://www.tikwm.com" + media
        path = os.path.join(workdir, f"tiktok_{int(time.time())}.{ext}")
        with requests.get(media, stream=True, timeout=120,
                          headers={"User-Agent": _UA_DESKTOP,
                                   "Referer": "https://www.tikwm.com/"}) as resp:
            resp.raise_for_status()
            total = 0
            with open(path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 15):
                    if not chunk:
                        continue
                    f.write(chunk)
                    total += len(chunk)
                    if total > MAX_BYTES:
                        f.close()
                        os.remove(path)
                        return None
        size = os.path.getsize(path)
        if size == 0:
            os.remove(path)
            return None
        return {
            "path": path,
            "size": size,
            "title": (d.get("title") or "TikTok")[:200],
            "uploader": ((d.get("author") or {}).get("nickname") or "TikTok"),
            "duration": int(d.get("duration") or 0),
            "ext": ext,
            "thumbnail": d.get("cover"),
            "webpage_url": url,
            "audio_only": audio_only,
        }
    except Exception:
        return None


def _sync_download(url: str, workdir: str, progress: Optional[Callable] = None,
                   audio_only: bool = False) -> dict:
    url = _extract_share_target(url)
    outtmpl = os.path.join(workdir, "%(id).40s.%(ext)s")
    platform = platform_from_url(url)
    if platform == "generic":
        raise RuntimeError("[generic] Only Facebook, Instagram, and TikTok links are allowed.")

    # TikTok: try tikwm.com first — it bypasses most yt-dlp issues.
    if platform == "tiktok":
        tik = _tikwm_download(url, workdir, audio_only)
        if tik:
            return tik

    # Pre-flight probe (non-fatal if it fails — some sites block extraction-only).
    try:
        probe = _probe(url)
        dur = probe.get("duration") or 0
        # crude bitrate floor — 1 hour @ 128kbps is already ~57MB; warn early
        if dur and dur > 7200:
            raise RuntimeError("Video too long to fit Telegram's 50 MB limit.")
    except RuntimeError:
        raise
    except Exception:
        pass  # tolerate probe failure

    last_err: Optional[Exception] = None
    raw_errors: list[str] = []
    hook = _make_progress_hook(progress)
    ladder = _AUDIO_LADDER if audio_only else _FORMAT_LADDER

    for tier_idx, fmt in enumerate(ladder):
        opts = _ydl_base(url)
        opts["outtmpl"] = outtmpl
        opts["format"] = fmt
        if tier_idx < max(0, len(ladder) - 2):
            opts["max_filesize"] = MAX_BYTES
        if hook:
            opts["progress_hooks"] = [hook]
        if audio_only:
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if "entries" in info:
                    info = info["entries"][0]
                path = ydl.prepare_filename(info)
                if not os.path.exists(path):
                    base, _ = os.path.splitext(path)
                    for ext in (".mp3", ".m4a", ".mp4", ".mkv", ".webm", ".mov", ".opus", ".ogg"):
                        if os.path.exists(base + ext):
                            path = base + ext
                            break
                if not os.path.exists(path):
                    raise RuntimeError("Downloaded file vanished.")
                path = _ensure_telegram_media(path, audio_only=audio_only)
                size = os.path.getsize(path)
                if size == 0:
                    raise RuntimeError("Empty download.")
                if size > MAX_BYTES:
                    # Try next, smaller tier.
                    try: os.remove(path)
                    except Exception: pass
                    last_err = RuntimeError(
                        f"Too large at tier {tier_idx+1} ({size/1024/1024:.1f} MB)."
                    )
                    continue
                meta = _probe_media(path) if not audio_only else {}
                return {
                    "path": path,
                    "size": size,
                    "title": (info.get("title") or "")[:200],
                    "uploader": info.get("uploader") or info.get("channel") or "",
                    "duration": info.get("duration") or 0,
                    "ext": os.path.splitext(path)[1].lstrip("."),
                    "width": meta.get("width") or info.get("width") or 0,
                    "height": meta.get("height") or info.get("height") or 0,
                    "thumbnail": info.get("thumbnail"),
                    "webpage_url": info.get("webpage_url") or url,
                    "audio_only": audio_only,
                }
        except yt_dlp.utils.DownloadError as e:
            last_err = e
            msg = str(e).lower()
            raw_errors.append(str(e)[:500])
            if "max-filesize" in msg or "file is larger" in msg \
               or "requested format is not available" in msg:
                continue
            if platform in {"facebook", "instagram"} and (
                "cannot parse data" in msg
                or "no video formats found" in msg
                or "requested format is not available" in msg
            ):
                raise RuntimeError(
                    f"[{platform}] {platform.title()} blocked or hid the reel/video stream for this server. "
                    "Open the link once in a browser and resend the final public reel URL. "
                    f"If it still fails, the owner must provide fresh {platform.title()} cookies to yt-dlp."
                )
            # bot-check / auth → no point hammering further tiers
            if "sign in to confirm" in msg or "login required" in msg \
               or "private" in msg or "age" in msg:
                raise
            continue
        except Exception as e:
            last_err = e
            raw_errors.append(str(e)[:500])
            continue

    # TikTok: last-ditch fallback to tikwm if yt-dlp failed completely.
    if platform == "tiktok":
        tik = _tikwm_download(url, workdir, audio_only)
        if tik:
            return tik

    # Final universal fallback: let yt-dlp choose whatever single best stream exists,
    # then normalize it for Telegram with ffmpeg.
    try:
        opts = _ydl_base(url)
        opts["outtmpl"] = outtmpl
        opts["format"] = "best"
        opts.pop("max_filesize", None)
        if hook:
            opts["progress_hooks"] = [hook]
        if audio_only:
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if "entries" in info:
                info = info["entries"][0]
            path = ydl.prepare_filename(info)
            if not os.path.exists(path):
                base, _ = os.path.splitext(path)
                for ext in (".mp3", ".m4a", ".mp4", ".mkv", ".webm", ".mov", ".opus", ".ogg"):
                    if os.path.exists(base + ext):
                        path = base + ext
                        break
            if not os.path.exists(path):
                raise RuntimeError("Downloaded file vanished.")
            path = _ensure_telegram_media(path, audio_only=audio_only)
            size = os.path.getsize(path)
            if size > MAX_BYTES:
                raise RuntimeError("Converted file is still too large for Telegram.")
            meta = _probe_media(path) if not audio_only else {}
            return {
                "path": path,
                "size": size,
                "title": (info.get("title") or "")[:200],
                "uploader": info.get("uploader") or info.get("channel") or "",
                "duration": info.get("duration") or 0,
                "ext": os.path.splitext(path)[1].lstrip("."),
                "width": meta.get("width") or info.get("width") or 0,
                "height": meta.get("height") or info.get("height") or 0,
                "thumbnail": info.get("thumbnail"),
                "webpage_url": info.get("webpage_url") or url,
                "audio_only": audio_only,
            }
    except Exception as e:
        last_err = e

    if last_err:
        raise RuntimeError(f"[{platform}] {last_err}")
    raise RuntimeError(f"[{platform}] Download failed after all fallbacks.")


async def download(url: str, progress: Optional[Callable] = None,
                   audio_only: bool = False) -> dict:
    """Download a video or audio. Returns dict with path/size/title or raises."""
    workdir = tempfile.mkdtemp(prefix="dl_")
    try:
        info = await asyncio.to_thread(_sync_download, url, workdir, progress, audio_only)
        if info["size"] > MAX_BYTES:
            try: os.remove(info["path"])
            except Exception: pass
            raise RuntimeError(
                f"File too large for Telegram ({info['size']/1024/1024:.1f} MB). "
                f"Max {MAX_BYTES/1024/1024:.0f} MB."
            )
        info["_workdir"] = workdir
        return info
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def user_error_text(err: Exception) -> str:
    msg = str(err or "Download failed").strip()
    low = msg.lower()
    platform = "generic"
    m = re.match(r"^\[([a-z0-9_:-]+)\]\s*(.*)$", msg, flags=re.IGNORECASE)
    if m:
        platform = m.group(1).lower()
        low = m.group(2).lower()
    # Keep a couple of useful, neutral cases — everything else collapses to the
    # single clean "no downloadable video" message in bold English.
    if "too long" in low or "too large" in low or "max " in low or "converted file is still too large" in low:
        return "<b>File is too large to send on Telegram ❌</b>"
    if "only facebook, instagram, and tiktok links are allowed" in low or "unsupported url" in low:
        return "<b>Only Facebook, Instagram and TikTok links are supported ❌</b>"
    if "timed out" in low or "timeout" in low:
        return "<b>The site took too long to respond. Please try again ❌</b>"
    return "<b>No downloadable video was found ❌</b>"


def cleanup(info: dict):
    wd = info.get("_workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
