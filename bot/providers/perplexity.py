"""Perplexity scrape provider. Verbatim port of user's working prplexity.py."""
import asyncio
import json
import re
import time
import uuid

import requests


def scrape_fresh_session():
    session = requests.Session()

    url = 'https://www.perplexity.ai'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; Redmi 8A Dual Build/QKQ1.191014.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.7499.34 Mobile Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'sec-ch-ua-platform': '"Android"',
        'sec-ch-ua': '"Android WebView";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        'sec-ch-ua-mobile': '?1',
        'sec-fetch-site': 'none',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-dest': 'document',
        'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
        'priority': 'u=0, i',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
    }

    response = session.get(url, headers=headers, timeout=30)
    html = response.text

    cookies = {c.name: c.value for c in session.cookies}

    visitor_id = cookies.get('pplx.visitor-id', str(uuid.uuid4()))
    session_id = cookies.get('pplx.session-id', str(uuid.uuid4()))

    version_match = re.search(r'"version":"([\d.]+)"', html)
    version = version_match.group(1) if version_match else '2.18'

    csrf_match = re.search(r'csrf-token["\']?\s*[:=]\s*["\']([^"\']+)', html)
    csrf_token = csrf_match.group(1) if csrf_match else f'{uuid.uuid4().hex}%7C{uuid.uuid4().hex}'

    api_url_match = re.search(r'"apiUrl":"([^"]+)"', html)
    api_url = api_url_match.group(1) if api_url_match else 'https://www.perplexity.ai/rest/sse/perplexity_ask'

    return {
        'session': session,
        'cookies': cookies,
        'visitor_id': visitor_id,
        'session_id': session_id,
        'version': version,
        'csrf_token': csrf_token,
        'api_url': api_url,
        'timestamp': int(time.time()),
    }


def parse_response(full_response):
    answer_text = ''
    for line in full_response.strip().split('\n'):
        if not line.startswith('data: '):
            continue
        json_str = line[6:].strip()
        if not json_str or json_str == '{}':
            continue
        try:
            data = json.loads(json_str)
            if 'text' in data and data.get('step_type') == 'FINAL':
                try:
                    steps = json.loads(data['text'])
                    if isinstance(steps, list):
                        for step in steps:
                            if step.get('step_type') == 'FINAL':
                                answer_str = step.get('content', {}).get('answer', '')
                                if answer_str:
                                    answer_data = json.loads(answer_str)
                                    answer_text = answer_data.get('answer', '')
                                    break
                except Exception:
                    pass
            if 'blocks' in data and not answer_text:
                for block in data['blocks']:
                    if block.get('intended_usage') in ['ask_text_0_markdown', 'ask_text']:
                        mb = block.get('markdown_block', {})
                        if mb.get('answer'):
                            answer_text = mb['answer']
                            break
        except Exception:
            continue
    return answer_text.strip()


def _ask_sync(prompt: str) -> str:
    scraped = scrape_fresh_session()
    session = scraped['session']
    base_cookies = scraped['cookies']
    visitor_id = scraped['visitor_id']
    session_id = scraped['session_id']
    version = scraped['version']
    csrf_token = scraped['csrf_token']
    api_url = scraped['api_url']
    current_time = scraped['timestamp']

    frontend_uuid = str(uuid.uuid4())
    backend_uuid = str(uuid.uuid4())
    read_write_token = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    payload = {
        "params": {
            "last_backend_uuid": backend_uuid,
            "read_write_token": read_write_token,
            "attachments": [],
            "language": "en-US",
            "timezone": "Asia/Dhaka",
            "search_focus": "internet",
            "sources": ["web"],
            "frontend_uuid": frontend_uuid,
            "mode": "concise",
            "model_preference": "turbo",
            "is_related_query": False,
            "is_sponsored": False,
            "prompt_source": "user",
            "query_source": "followup",
            "is_incognito": False,
            "time_from_first_type": 1485.7000000178814,
            "local_search_enabled": False,
            "use_schematized_api": True,
            "send_back_text_in_streaming_api": False,
            "supported_block_use_cases": [
                "answer_modes", "media_items", "knowledge_cards",
                "inline_entity_cards", "place_widgets", "finance_widgets",
                "prediction_market_widgets", "sports_widgets",
                "flight_status_widgets", "news_widgets", "shopping_widgets",
                "jobs_widgets", "search_result_widgets", "inline_images",
                "inline_assets", "placeholder_cards", "diff_blocks",
                "inline_knowledge_cards", "entity_group_v2",
                "refinement_filters", "canvas_mode", "maps_preview",
                "answer_tabs", "price_comparison_widgets", "preserve_latex",
                "in_context_suggestions",
            ],
            "client_coordinates": None,
            "mentions": [],
            "skip_search_enabled": True,
            "is_nav_suggestions_disabled": False,
            "followup_source": "link",
            "source": "mweb",
            "always_search_override": False,
            "override_no_search": False,
            "should_ask_for_mcp_tool_confirmation": True,
            "supported_features": ["browser_agent_permission_banner_v1.1"],
            "version": version,
        },
        "query_str": prompt,
    }

    additional_cookies = {
        'pplx.visitor-id': visitor_id,
        'pplx.session-id': session_id,
        'next-auth.csrf-token': csrf_token,
        'next-auth.callback-url': 'https%3A%2F%2Fwww.perplexity.ai%2Fapi%2Fauth%2Fsignin-callback%3Fredirect%3Dhttps%253A%252F%252Fwww.perplexity.ai',
        'pplx.mweb-splash-page-dismissed': 'true',
        'pplx.la-status': 'allowed',
        '__ps_r': '_',
        '__ps_sr': '_',
        '__ps_fva': str(current_time * 1000),
        '_fbp': f'fb.1.{current_time}.{uuid.uuid4().hex}',
        'pplx.metadata': json.dumps({
            "qc": 2, "qcu": 0, "qcm": 0, "qcc": 0, "qcco": 0, "qccol": 0,
            "qcdr": 0, "qcs": 0, "qcd": 0, "hli": False, "hcga": False,
            "hcds": False, "hso": False, "hfo": False, "hsco": False,
            "hfco": False, "hsma": False, "hdc": False,
            "fqa": current_time * 1000, "lqa": current_time * 1000,
        }),
    }

    all_cookies = {**base_cookies, **additional_cookies}

    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; Redmi 8A Dual Build/QKQ1.191014.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.7499.34 Mobile Safari/537.36',
        'Accept': 'text/event-stream',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Content-Type': 'application/json',
        'x-request-id': request_id,
        'sec-ch-ua-platform': '"Android"',
        'sec-ch-ua': '"Android WebView";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        'sec-ch-ua-mobile': '?1',
        'x-perplexity-request-reason': 'perplexity-query-state-provider',
        'origin': 'https://www.perplexity.ai',
        'x-requested-with': 'mark.via.gp',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-mode': 'cors',
        'sec-fetch-dest': 'empty',
        'referer': 'https://www.perplexity.ai/search/hi-lMwqBQEoQRKoNpRoTe6QRA',
        'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
        'priority': 'u=1, i',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
    }

    # Match reference behavior: only attach x-csrf-token when the token is
    # NOT our locally-generated fallback shape (`<hex>%7C<hex>`).
    if csrf_token and '%7C' not in csrf_token:
        headers['x-csrf-token'] = csrf_token

    time.sleep(0.5)

    response = session.post(
        api_url, json=payload, headers=headers,
        cookies=all_cookies, timeout=120,
    )

    if response.status_code != 200:
        body = (response.text or "")[:240]
        raise RuntimeError(f"Perplexity HTTP {response.status_code}: {body}")

    answer = parse_response(response.text)
    if not answer:
        raise RuntimeError("Empty response from Perplexity")
    return answer


def _ask_with_retry(prompt: str, attempts: int = 3) -> str:
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            return _ask_sync(prompt)
        except RuntimeError as e:
            last_err = e
            msg = str(e)
            # Retry on Cloudflare/anti-bot 403 + transient 5xx with a fresh session.
            if "HTTP 403" in msg or "HTTP 5" in msg or "Empty response" in msg:
                time.sleep(1.2 * (i + 1))
                continue
            raise
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (i + 1))
    assert last_err is not None
    raise last_err


async def ask(prompt: str, history: list) -> str:
    if history:
        ctx = "\n".join(f"User: {h['q']}\nAssistant: {h['a']}" for h in history[-3:])
        prompt = f"Previous context:\n{ctx}\n\nNew question: {prompt}"
    return await asyncio.to_thread(_ask_with_retry, prompt)
