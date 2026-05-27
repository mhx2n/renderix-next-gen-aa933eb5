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

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# We rely on yt-dlp's 1000+ supported sites. Any http(s) URL is accepted;
# yt-dlp will reject truly unsupported ones with a clear error.

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
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if host.endswith("tiktok.com"):
        return "tiktok"
    if host.endswith("instagram.com"):
        return "instagram"
    if host.endswith("facebook.com") or host == "fb.watch":
        return "facebook"
    if host.endswith("twitter.com") or host == "x.com":
        return "twitter"
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


def _ensure_telegram_media(path: str, audio_only: bool) -> str:
    """Convert/remux media to Telegram-friendly formats when needed."""
    if not path or not os.path.exists(path):
        return path

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
        if ext in {".mp4", ".m4v", ".mov"}:
            try:
                faststart = base + ".tg.mp4"
                _run_ffmpeg([
                    "ffmpeg", "-y", "-i", path,
                    "-c", "copy", "-movflags", "+faststart",
                    faststart,
                ], timeout=180)
                target = faststart
            except Exception:
                return path
        else:
            target = base + ".mp4"
            _run_ffmpeg([
                "ffmpeg", "-y", "-i", path,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-c:a", "aac", "-b:a", "128k",
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
    }
    platform = platform_from_url(url)
    if platform == "youtube":
        opts["http_headers"]["User-Agent"] = _UA_IOS
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["tv_embedded", "ios", "mweb", "web_safari"],
                "player_skip": ["configs"],
            },
        }
        cookies = _cookies_path()
        if cookies:
            opts["cookiefile"] = cookies
    elif platform == "tiktok":
        opts["http_headers"].update({
            "Referer": "https://www.tiktok.com/",
            "Origin": "https://www.tiktok.com",
        })
    return opts


# Format ladder (descending preference). Each tier stays under MAX_BYTES.
_FORMAT_LADDER = [
    # Tier 1: best compact mp4 under cap
    f"bv*[ext=mp4][filesize<{MAX_BYTES}]+ba[ext=m4a]/"
    f"b[ext=mp4][filesize<{MAX_BYTES}]",
    # Tier 2: any combo under cap
    f"bv*[filesize<{MAX_BYTES}]+ba/"
    f"b[filesize<{MAX_BYTES}]",
    # Tier 3: capped height
    "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/"
    "bv*[height<=720]+ba/b[height<=720]",
    # Tier 4: even smaller
    "bv*[height<=480]+ba/b[height<=480]/best[height<=480]",
    # Tier 5: anything that works
    "bv*+ba/b",
]

# Audio-only ladder
_AUDIO_LADDER = [
    f"ba[ext=m4a][filesize<{MAX_BYTES}]/ba[filesize<{MAX_BYTES}]",
    "bestaudio[ext=m4a]/bestaudio/best",
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
    url = _normalize_url(url)
    outtmpl = os.path.join(workdir, "%(id).40s.%(ext)s")
    platform = platform_from_url(url)

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
    hook = _make_progress_hook(progress)
    ladder = _AUDIO_LADDER if audio_only else _FORMAT_LADDER

    for tier_idx, fmt in enumerate(ladder):
        opts = _ydl_base(url)
        opts["outtmpl"] = outtmpl
        opts["format"] = fmt
        opts["format_sort"] = ["+size", "+br", "+res", "+fps"]
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
                return {
                    "path": path,
                    "size": size,
                    "title": (info.get("title") or "")[:200],
                    "uploader": info.get("uploader") or info.get("channel") or "",
                    "duration": info.get("duration") or 0,
                    "ext": os.path.splitext(path)[1].lstrip("."),
                    "thumbnail": info.get("thumbnail"),
                    "webpage_url": info.get("webpage_url") or url,
                    "audio_only": audio_only,
                }
        except yt_dlp.utils.DownloadError as e:
            last_err = e
            msg = str(e).lower()
            if "max-filesize" in msg or "file is larger" in msg \
               or "requested format is not available" in msg:
                continue
            # bot-check / auth → no point hammering further tiers
            if "sign in to confirm" in msg or "login required" in msg \
               or "private" in msg or "age" in msg:
                raise
            continue
        except Exception as e:
            last_err = e
            continue

    # TikTok: last-ditch fallback to tikwm if yt-dlp failed completely.
    if platform == "tiktok":
        tik = _tikwm_download(url, workdir, audio_only)
        if tik:
            return tik

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
    if ("sign in to confirm" in low or "confirm you" in low) and platform == "youtube":
        return (
            "YouTube is asking for sign-in verification on the server.\n"
            "Owner fix: export fresh YouTube cookies into youtube_cookies.txt "
            "from a real browser session, then redeploy/restart the bot."
        )
    if platform == "tiktok":
        if "unable to extract webpage video data" in low or "empty media response" in low:
            return (
                "TikTok blocked this short link or did not expose the video stream right now.\n"
                "Try opening the link once in a browser, copy the full TikTok video URL, then send that link again."
            )
        if "login required" in low or "private" in low or "status code 403" in low or "forbidden" in low:
            return (
                "This TikTok post is restricted from the server right now.\n"
                "Try a public full video link, or refresh TikTok-access cookies/headers on the server."
            )
    if "login required" in low or "private" in low:
        return "This post is private or requires login."
    if "age" in low and "restricted" in low:
        return "Age-restricted content cannot be downloaded without login."
    if "too long" in low or "too large" in low or "max " in low:
        return (
            "This video is too large for Telegram's 50 MB bot upload limit. "
            "Try a shorter clip or a lower-quality source."
        )
    if "converted file is still too large" in low:
        return "The video could be prepared, but it is still too large to send in Telegram."
    if "unable to extract" in low or "empty media response" in low:
        return "This link did not expose a downloadable video stream."
    if "timed out" in low or "timeout" in low:
        return "The remote site took too long to respond. Please try again."
    if "unsupported url" in low:
        return "This site is not supported."
    return "Download failed. Please try another link or try again later."


def cleanup(info: dict):
    wd = info.get("_workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
