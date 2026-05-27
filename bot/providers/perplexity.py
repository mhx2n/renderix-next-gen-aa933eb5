"""Perplexity scrape provider. Adapted from user's prplexity.py."""
import asyncio
import json
import os
import re
import time
import uuid

import requests


def _scrape():
    s = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; Redmi 8A) AppleWebKit/537.36 Chrome/143.0.7499.34 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    }
    r = s.get("https://www.perplexity.ai", headers=headers, timeout=30)
    html = r.text
    cookies = {c.name: c.value for c in s.cookies}
    visitor = cookies.get("pplx.visitor-id", str(uuid.uuid4()))
    sess_id = cookies.get("pplx.session-id", str(uuid.uuid4()))
    m = re.search(r'"version":"([\d.]+)"', html)
    version = m.group(1) if m else "2.18"
    m = re.search(r'csrf-token["\']?\s*[:=]\s*["\']([^"\']+)', html)
    csrf = m.group(1) if m else f"{uuid.uuid4().hex}%7C{uuid.uuid4().hex}"
    m = re.search(r'"apiUrl":"([^"]+)"', html)
    api_url = m.group(1) if m else "https://www.perplexity.ai/rest/sse/perplexity_ask"
    return {
        "session": s, "cookies": cookies, "visitor": visitor, "sid": sess_id,
        "version": version, "csrf": csrf, "api_url": api_url, "ts": int(time.time()),
    }


def _parse(text: str) -> str:
    answer = ""
    for line in text.strip().split("\n"):
        if not line.startswith("data: "):
            continue
        js = line[6:].strip()
        if not js or js == "{}":
            continue
        try:
            data = json.loads(js)
            if "text" in data and data.get("step_type") == "FINAL":
                try:
                    steps = json.loads(data["text"])
                    if isinstance(steps, list):
                        for step in steps:
                            if step.get("step_type") == "FINAL":
                                ans = step.get("content", {}).get("answer", "")
                                if ans:
                                    answer = json.loads(ans).get("answer", "")
                                    break
                except Exception:
                    pass
            if "blocks" in data and not answer:
                for b in data["blocks"]:
                    if b.get("intended_usage") in ("ask_text_0_markdown", "ask_text"):
                        mb = b.get("markdown_block", {})
                        if mb.get("answer"):
                            answer = mb["answer"]
                            break
        except Exception:
            continue
    return answer.strip()


def _ask_sync(prompt: str) -> str:
    sc = _scrape()
    current_time = sc["ts"]
    payload = {
        "params": {
            "last_backend_uuid": str(uuid.uuid4()),
            "read_write_token": str(uuid.uuid4()),
            "attachments": [], "language": "en-US", "timezone": "Asia/Dhaka",
            "search_focus": "internet", "sources": ["web"],
            "frontend_uuid": str(uuid.uuid4()), "mode": "concise",
            "model_preference": "turbo", "is_related_query": False,
            "is_sponsored": False, "prompt_source": "user",
            "query_source": "followup", "is_incognito": False,
            "local_search_enabled": False, "use_schematized_api": True,
            "send_back_text_in_streaming_api": False,
            "supported_block_use_cases": [
                "answer_modes", "media_items", "knowledge_cards", "inline_entity_cards",
                "place_widgets", "finance_widgets", "prediction_market_widgets",
                "sports_widgets", "flight_status_widgets", "news_widgets",
                "shopping_widgets", "jobs_widgets", "search_result_widgets",
                "inline_images", "inline_assets", "placeholder_cards", "diff_blocks",
                "inline_knowledge_cards", "entity_group_v2", "refinement_filters",
                "canvas_mode", "maps_preview", "answer_tabs", "price_comparison_widgets",
                "preserve_latex", "in_context_suggestions",
            ],
            "client_coordinates": None, "mentions": [],
            "skip_search_enabled": True, "is_nav_suggestions_disabled": False,
            "followup_source": "link", "source": "mweb",
            "always_search_override": False, "override_no_search": False,
            "should_ask_for_mcp_tool_confirmation": True,
            "supported_features": ["browser_agent_permission_banner_v1.1"],
            "version": sc["version"],
        },
        "query_str": prompt,
    }
    extra = {
        "pplx.visitor-id": sc["visitor"], "pplx.session-id": sc["sid"],
        "next-auth.csrf-token": sc["csrf"], "pplx.mweb-splash-page-dismissed": "true",
        "next-auth.callback-url": "https%3A%2F%2Fwww.perplexity.ai%2Fapi%2Fauth%2Fsignin-callback%3Fredirect%3Dhttps%253A%252F%252Fwww.perplexity.ai",
        "pplx.la-status": "allowed",
        "__ps_r": "_",
        "__ps_sr": "_",
        "__ps_fva": str(current_time * 1000),
        "_fbp": f"fb.1.{current_time}.{uuid.uuid4().hex}",
        "pplx.metadata": json.dumps({
            "qc": 2, "qcu": 0, "qcm": 0, "qcc": 0, "qcco": 0, "qccol": 0,
            "qcdr": 0, "qcs": 0, "qcd": 0, "hli": False, "hcga": False,
            "hcds": False, "hso": False, "hfo": False, "hsco": False,
            "hfco": False, "hsma": False, "hdc": False,
            "fqa": current_time * 1000, "lqa": current_time * 1000,
        }),
    }
    cookies = {**sc["cookies"], **extra}
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; Redmi 8A) AppleWebKit/537.36 Chrome/143.0.7499.34 Mobile Safari/537.36",
        "Accept": "text/event-stream", "Accept-Encoding": "gzip, deflate, br, zstd",
        "Content-Type": "application/json",
        "x-request-id": str(uuid.uuid4()),
        "sec-ch-ua-platform": '"Android"',
        "sec-ch-ua": '"Android WebView";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "x-perplexity-request-reason": "perplexity-query-state-provider",
        "x-requested-with": "mark.via.gp",
        "Origin": "https://www.perplexity.ai",
        "Referer": "https://www.perplexity.ai/search/hi-lMwqBQEoQRKoNpRoTe6QRA",
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        "priority": "u=1, i",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if sc["csrf"]:
        headers["x-csrf-token"] = sc["csrf"]
    delay = float(os.getenv("PERPLEXITY_REQUEST_DELAY", "0.5") or 0.5)
    if delay > 0:
        time.sleep(delay)
    r = sc["session"].post(sc["api_url"], json=payload, headers=headers, cookies=cookies, timeout=120)
    if r.status_code != 200:
        body = (r.text or "")[:240]
        raise RuntimeError(f"Perplexity HTTP {r.status_code}: {body}")
    ans = _parse(r.text)
    if not ans:
        raise RuntimeError("Empty response from Perplexity")
    return ans


async def ask(prompt: str, history: list) -> str:
    if history:
        ctx = "\n".join(f"User: {h['q']}\nAssistant: {h['a']}" for h in history[-3:])
        prompt = f"Previous context:\n{ctx}\n\nNew question: {prompt}"
    return await asyncio.to_thread(_ask_sync, prompt)
