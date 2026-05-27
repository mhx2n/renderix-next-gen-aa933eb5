"""Inspect AI provider API keys: validity, models, limits, expiry."""
import asyncio
import aiohttp


async def _fetch_json(session, method, url, **kw):
    try:
        async with session.request(method, url, timeout=aiohttp.ClientTimeout(total=20), **kw) as r:
            txt = await r.text()
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = {"raw": txt[:400]}
            return r.status, data, dict(r.headers)
    except Exception as e:
        return 0, {"error": str(e)}, {}


def _detect(key: str) -> str:
    k = key.strip()
    if k.startswith("sk-or-"): return "openrouter"
    if k.startswith("sk-ant-"): return "anthropic"
    if k.startswith("gsk_"): return "groq"
    if k.startswith("xai-"): return "xai"
    if k.startswith("AIza"): return "gemini"
    if k.startswith("tgp_") or k.startswith("together_"): return "together"
    if k.startswith("nvapi-"): return "nvidia"
    if k.startswith("ds-") or "deepseek" in k.lower(): return "deepseek"
    if k.startswith("sk-proj-"): return "openai"
    # sk-... is ambiguous (OpenAI / DeepSeek / Mistral / Fireworks / Perplexity / etc.)
    if k.startswith("sk-"): return "ambiguous_sk"
    if k.startswith("co-"): return "cohere"
    return "unknown"


async def _openai(session, key):
    s, models, h = await _fetch_json(session, "GET", "https://api.openai.com/v1/models",
                                     headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "OpenAI", "valid": False, "status": s, "error": models}
    ids = [m["id"] for m in models.get("data", [])][:50]
    limits = {k: v for k, v in h.items() if "ratelimit" in k.lower() or "x-request-id" == k.lower()}
    return {"provider": "OpenAI", "valid": True, "models": ids, "limits": limits}


async def _anthropic(session, key):
    s, data, h = await _fetch_json(session, "GET", "https://api.anthropic.com/v1/models",
                                   headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    if s != 200:
        return {"provider": "Anthropic", "valid": False, "status": s, "error": data}
    ids = [m["id"] for m in data.get("data", [])]
    return {"provider": "Anthropic", "valid": True, "models": ids, "limits": {}}


async def _gemini(session, key):
    s, data, _ = await _fetch_json(session, "GET",
                                   f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
    if s != 200:
        return {"provider": "Google Gemini", "valid": False, "status": s, "error": data}
    ids = [m["name"].replace("models/", "") for m in data.get("models", [])][:60]
    return {"provider": "Google Gemini", "valid": True, "models": ids, "limits": {}}


async def _groq(session, key):
    s, data, h = await _fetch_json(session, "GET", "https://api.groq.com/openai/v1/models",
                                   headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "Groq", "valid": False, "status": s, "error": data}
    ids = [m["id"] for m in data.get("data", [])]
    return {"provider": "Groq", "valid": True, "models": ids, "limits": {}}


async def _openrouter(session, key):
    s, data, _ = await _fetch_json(session, "GET", "https://openrouter.ai/api/v1/auth/key",
                                   headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "OpenRouter", "valid": False, "status": s, "error": data}
    d = data.get("data", {})
    s2, models, _ = await _fetch_json(session, "GET", "https://openrouter.ai/api/v1/models",
                                      headers={"Authorization": f"Bearer {key}"})
    ids = [m["id"] for m in (models.get("data", []) if isinstance(models, dict) else [])][:80]
    return {
        "provider": "OpenRouter", "valid": True, "models": ids,
        "limits": {
            "label": d.get("label"),
            "limit": d.get("limit"),
            "usage": d.get("usage"),
            "limit_remaining": d.get("limit_remaining"),
            "is_free_tier": d.get("is_free_tier"),
            "rate_limit": d.get("rate_limit"),
        },
    }


async def _cohere(session, key):
    s, data, _ = await _fetch_json(session, "GET", "https://api.cohere.com/v1/models",
                                   headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "Cohere", "valid": False, "status": s, "error": data}
    ids = [m["name"] for m in data.get("models", [])]
    return {"provider": "Cohere", "valid": True, "models": ids, "limits": {}}


async def _deepseek(session, key):
    s, data, _ = await _fetch_json(session, "GET", "https://api.deepseek.com/v1/models",
                                   headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "DeepSeek", "valid": False, "status": s, "error": data}
    ids = [m["id"] for m in data.get("data", [])]
    bal_s, bal, _ = await _fetch_json(session, "GET", "https://api.deepseek.com/user/balance",
                                      headers={"Authorization": f"Bearer {key}"})
    limits = bal if bal_s == 200 else {}
    return {"provider": "DeepSeek", "valid": True, "models": ids, "limits": limits}


async def _xai(session, key):
    s, data, _ = await _fetch_json(session, "GET", "https://api.x.ai/v1/models",
                                   headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "xAI", "valid": False, "status": s, "error": data}
    ids = [m["id"] for m in data.get("data", [])]
    return {"provider": "xAI", "valid": True, "models": ids, "limits": {}}


async def _together(session, key):
    s, data, _ = await _fetch_json(session, "GET", "https://api.together.xyz/v1/models",
                                   headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "Together AI", "valid": False, "status": s, "error": data}
    ids = [m["id"] for m in (data if isinstance(data, list) else data.get("data", []))][:80]
    return {"provider": "Together AI", "valid": True, "models": ids, "limits": {}}


_HANDLERS = {
    "openai": _openai, "anthropic": _anthropic, "gemini": _gemini,
    "groq": _groq, "openrouter": _openrouter, "cohere": _cohere,
    "deepseek": _deepseek, "xai": _xai, "together": _together,
}


async def _mistral(session, key):
    s, data, _ = await _fetch_json(session, "GET", "https://api.mistral.ai/v1/models",
                                   headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "Mistral", "valid": False, "status": s, "error": data}
    ids = [m["id"] for m in data.get("data", [])]
    return {"provider": "Mistral", "valid": True, "models": ids, "limits": {}}


async def _perplexity(session, key):
    # Perplexity has no public /models GET; probe chat
    s, data, _ = await _fetch_json(session, "POST", "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": "sonar", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1})
    if s not in (200, 400):
        return {"provider": "Perplexity", "valid": False, "status": s, "error": data}
    return {"provider": "Perplexity", "valid": True,
            "models": ["sonar", "sonar-pro", "sonar-reasoning"], "limits": {}}


async def _fireworks(session, key):
    s, data, _ = await _fetch_json(session, "GET", "https://api.fireworks.ai/inference/v1/models",
                                   headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "Fireworks", "valid": False, "status": s, "error": data}
    ids = [m["id"] for m in (data.get("data", []) if isinstance(data, dict) else [])][:80]
    return {"provider": "Fireworks", "valid": True, "models": ids, "limits": {}}


async def _nvidia(session, key):
    s, data, _ = await _fetch_json(session, "GET", "https://integrate.api.nvidia.com/v1/models",
                                   headers={"Authorization": f"Bearer {key}"})
    if s != 200:
        return {"provider": "NVIDIA NIM", "valid": False, "status": s, "error": data}
    ids = [m["id"] for m in data.get("data", [])][:80]
    return {"provider": "NVIDIA NIM", "valid": True, "models": ids, "limits": {}}


_HANDLERS["mistral"] = _mistral
_HANDLERS["perplexity"] = _perplexity
_HANDLERS["fireworks"] = _fireworks
_HANDLERS["nvidia"] = _nvidia

# Probe order when key prefix is ambiguous or unknown
_PROBE_ORDER = [
    "openai", "deepseek", "mistral", "groq", "together", "fireworks",
    "perplexity", "xai", "anthropic", "openrouter", "cohere", "nvidia", "gemini",
]


async def _probe_all(session, key, order=None):
    """Try each provider sequentially; return first valid result."""
    order = order or _PROBE_ORDER
    last = None
    for name in order:
        handler = _HANDLERS.get(name)
        if not handler:
            continue
        try:
            res = await handler(session, key)
        except Exception as e:
            res = {"provider": name, "valid": False, "error": str(e)}
        if res.get("valid"):
            return res
        last = res
    return last or {"provider": "Unknown", "valid": False, "error": "no provider matched"}


async def inspect_key(key: str) -> dict:
    provider = _detect(key)
    async with aiohttp.ClientSession() as session:
        if provider in _HANDLERS:
            res = await _HANDLERS[provider](session, key)
            if res.get("valid"):
                return res
            # fall through: prefix lied, probe others
            order = [p for p in _PROBE_ORDER if p != provider]
            probed = await _probe_all(session, key, order)
            if probed.get("valid"):
                return probed
            return res  # original detailed error
        # ambiguous_sk or unknown -> probe everything
        return await _probe_all(session, key)


async def try_model(key: str, model: str, prompt: str) -> str:
    provider = _detect(key)
    async with aiohttp.ClientSession() as session:
        if provider == "anthropic":
            s, data, _ = await _fetch_json(session, "POST", "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": 512,
                      "messages": [{"role": "user", "content": prompt}]})
            if s != 200:
                raise RuntimeError(f"HTTP {s}: {data}")
            blocks = data.get("content", [])
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

        if provider == "gemini":
            s, data, _ = await _fetch_json(session, "POST",
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                json={"contents": [{"parts": [{"text": prompt}]}]})
            if s != 200:
                raise RuntimeError(f"HTTP {s}: {data}")
            cands = data.get("candidates", [])
            if not cands:
                return ""
            parts = cands[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)

        # OpenAI-compatible (openai, groq, openrouter, deepseek, xai, together, cohere v2 compat)
        base = {
            "openai": "https://api.openai.com/v1",
            "groq": "https://api.groq.com/openai/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "xai": "https://api.x.ai/v1",
            "together": "https://api.together.xyz/v1",
            "cohere": "https://api.cohere.com/compatibility/v1",
            "mistral": "https://api.mistral.ai/v1",
            "perplexity": "https://api.perplexity.ai",
            "fireworks": "https://api.fireworks.ai/inference/v1",
            "nvidia": "https://integrate.api.nvidia.com/v1",
        }.get(provider, "https://api.openai.com/v1")
        s, data, _ = await _fetch_json(session, "POST", f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 512})
        if s != 200:
            raise RuntimeError(f"HTTP {s}: {data}")
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")
