"""Shared Mistral API helper: key storage, daily-limit, chat + vision calls."""
from __future__ import annotations

import os
import aiohttp
from .. import db

CHAT_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_DAILY_LIMIT = 30
TEXT_MODEL = "mistral-small-latest"
VISION_MODEL = "pixtral-12b-2409"


async def get_key() -> str:
    k = await db.get_setting("mistral_key", "")
    if k:
        return k
    return os.getenv("MISTRAL_API_KEY", "")


async def set_key(value: str) -> None:
    await db.set_setting("mistral_key", value or "")


async def get_daily_limit() -> int:
    raw = await db.get_setting("mistral_daily_limit", str(DEFAULT_DAILY_LIMIT))
    try:
        return max(0, int(raw))
    except Exception:
        return DEFAULT_DAILY_LIMIT


async def set_daily_limit(n: int) -> None:
    await db.set_setting("mistral_daily_limit", str(max(0, int(n))))


async def check_quota(user_id: int, tool: str) -> tuple[bool, int, int]:
    """Returns (allowed, used_after, limit). tool e.g. 'tr', 'ocr'."""
    limit = await get_daily_limit()
    if limit == 0:
        return False, 0, 0
    ok, used = await db.quota_check_and_inc(user_id, f"mistral_{tool}", limit)
    return ok, used, limit


async def chat(messages: list[dict], model: str | None = None, max_tokens: int = 1024,
               temperature: float = 0.2, timeout: int = 60) -> str:
    key = await get_key()
    if not key:
        raise RuntimeError("AI engine key not configured. Owner must set it via /mkey.")
    payload = {
        "model": model or TEXT_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_cfg) as s:
        async with s.post(CHAT_URL, json=payload, headers=headers) as r:
            data = await r.json(content_type=None)
            if r.status >= 400:
                msg = data.get("message") or data.get("error") or str(data)[:300]
                raise RuntimeError(f"AI engine error [{r.status}]: {msg}")
            return data["choices"][0]["message"]["content"]


async def vision_extract(image_bytes: bytes, prompt: str, mime: str = "image/jpeg",
                         timeout: int = 90) -> str:
    import base64
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    msg = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": data_url},
        ],
    }]
    return await chat(msg, model=VISION_MODEL, max_tokens=2048, temperature=0.0, timeout=timeout)
