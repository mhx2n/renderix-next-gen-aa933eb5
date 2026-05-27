"""Gemini web-scrape provider (no API key needed). Adapted from user's Gemini3.py."""
import asyncio
import json
import re
import time
import uuid
from datetime import datetime

import requests
from bs4 import BeautifulSoup


def _extract_snlm0e(html: str):
    patterns = [
        r'"SNlM0e":"([^"]+)"', r"'SNlM0e':'([^']+)'",
        r'"FdrFJe":"([^"]+)"', r"'FdrFJe':'([^']+)'",
        r'"cfb2h":"([^"]+)"',
    ]
    for p in patterns:
        m = re.search(p, html, re.IGNORECASE)
        if m and len(m.group(1)) > 20:
            return m.group(1)
    return None


def _extract_params(html: str):
    params = {}
    m = re.search(r'"bl":"([^"]+)"', html)
    params["bl"] = m.group(1) if m else "boq_assistant-bard-web-server_20251217.07_p5"
    m = re.search(r'"fsid":"([^"]+)"', html) or re.search(r'f\.sid["\']?\s*[:=]\s*["\']?([^"\'&\s]+)', html)
    params["fsid"] = m.group(1) if m else str(-1 * int(time.time() * 1000))
    m = re.search(r'_reqid["\']?\s*[:=]\s*["\']?(\d+)', html)
    params["reqid"] = int(m.group(1)) if m else int(time.time() * 1000) % 1000000
    return params


def _scrape_session():
    s = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = s.get("https://gemini.google.com/app", headers=headers, timeout=30)
    html = r.text
    snlm0e = _extract_snlm0e(html)
    if not snlm0e:
        soup = BeautifulSoup(html, "html.parser")
        for sc in soup.find_all("script"):
            if sc.string and ("SNlM0e" in sc.string or "FdrFJe" in sc.string):
                snlm0e = _extract_snlm0e(sc.string)
                if snlm0e:
                    break
    if not snlm0e:
        return None
    p = _extract_params(html)
    return {"session": s, "snlm0e": snlm0e, **p, "cookies": {c.name: c.value for c in s.cookies}}


def _build_payload(prompt: str, snlm0e: str):
    escaped = prompt.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    data = [
        [escaped, 0, None, None, None, None, 0],
        ["en-US"],
        ["", "", "", None, None, None, None, None, None, ""],
        snlm0e, uuid.uuid4().hex, None, [0], 1, None, None, 1, 0,
        None, None, None, None, None, [[0]], 0, None, None, None, None,
        None, None, None, None, 1, None, None, [4], None, None, None,
        None, None, None, None, None, None, None, [2], None, None, None,
        None, None, None, None, None, None, None, None, 0, None, None,
        None, None, None, str(uuid.uuid4()).upper(), None, [],
    ]
    payload_str = json.dumps(data, separators=(",", ":"))
    esc = payload_str.replace("\\", "\\\\").replace('"', '\\"')
    return {"f.req": f'[null,"{esc}"]', "": ""}


def _parse(text: str):
    full = ""
    for line in text.strip().split("\n"):
        if not line or line.startswith(")]}'") or line.isdigit():
            continue
        try:
            data = json.loads(line)
            if isinstance(data, list) and data and data[0][0] == "wrb.fr" and len(data[0]) > 2:
                inner = json.loads(data[0][2])
                if isinstance(inner, list) and len(inner) > 4:
                    arr = inner[4]
                    if arr and isinstance(arr[0], list) and len(arr[0]) > 1:
                        rid = arr[0][0]
                        if isinstance(rid, str) and rid.startswith("rc_"):
                            txt_arr = arr[0][1]
                            if txt_arr and isinstance(txt_arr[0], str) and len(txt_arr[0]) > len(full):
                                full = txt_arr[0]
        except Exception:
            continue
    if full:
        full = full.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    return full or None


def _ask_sync(prompt: str) -> str:
    scraped = _scrape_session()
    if not scraped:
        raise RuntimeError("Failed to establish Gemini session")
    url = (
        "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate"
        f"?bl={scraped['bl']}&f.sid={scraped['fsid']}&hl=en-US&_reqid={scraped['reqid']}&rt=c"
    )
    cookie_str = "; ".join(f"{k}={v}" for k, v in scraped["cookies"].items())
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
        "Cookie": cookie_str,
        "x-same-domain": "1",
    }
    r = scraped["session"].post(url, data=_build_payload(prompt, scraped["snlm0e"]), headers=headers, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {r.status_code}")
    out = _parse(r.text)
    if not out:
        raise RuntimeError("Empty response from Gemini")
    return out


async def ask(prompt: str, history: list) -> str:
    # Fold history into the prompt for context continuity.
    if history:
        ctx = "\n".join(f"User: {h['q']}\nAssistant: {h['a']}" for h in history[-4:])
        prompt = f"{ctx}\nUser: {prompt}\nAssistant:"
    return await asyncio.to_thread(_ask_sync, prompt)
