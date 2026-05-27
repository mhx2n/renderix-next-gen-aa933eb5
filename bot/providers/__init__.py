import aiohttp

from . import gemini, perplexity, copilot

# Registry: command_key -> (display_name, ask_callable)
# ask_callable signature: async def ask(prompt: str, history: list[dict]) -> str
REGISTRY = {
    "g": ("Gemini", gemini.ask),
    "pr": ("Perplexity", perplexity.ask),
    "co": ("Copilot", copilot.ask),
}


def register(cmd: str, name: str, func):
    """Owner-extensible: add a new provider at runtime."""
    REGISTRY[cmd] = (name, func)


def make_openai_compatible_provider(name: str, base_url: str, api_key: str, model: str):
    base = base_url.rstrip("/")

    async def ask(prompt: str, history: list) -> str:
        messages = []
        for h in history[-6:]:
            q = (h.get("q") or "").strip()
            a = (h.get("a") or "").strip()
            if q:
                messages.append({"role": "user", "content": q})
            if a:
                messages.append({"role": "assistant", "content": a})
        messages.append({"role": "user", "content": prompt})
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base}/chat/completions",
                headers=headers,
                json={"model": model, "messages": messages, "max_tokens": 1500},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise RuntimeError(f"{name} HTTP {resp.status}: {data}")
                return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

    return ask


def make_anthropic_provider(name: str, api_key: str, model: str):
    async def ask(prompt: str, history: list) -> str:
        messages = []
        for h in history[-6:]:
            q = (h.get("q") or "").strip()
            a = (h.get("a") or "").strip()
            if q:
                messages.append({"role": "user", "content": q})
            if a:
                messages.append({"role": "assistant", "content": a})
        messages.append({"role": "user", "content": prompt})
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json={"model": model, "max_tokens": 1500, "messages": messages},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise RuntimeError(f"{name} HTTP {resp.status}: {data}")
                blocks = data.get("content", [])
                return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    return ask


def make_gemini_provider(name: str, api_key: str, model: str):
    async def ask(prompt: str, history: list) -> str:
        contents = []
        for h in history[-6:]:
            q = (h.get("q") or "").strip()
            a = (h.get("a") or "").strip()
            if q:
                contents.append({"role": "user", "parts": [{"text": q}]})
            if a:
                contents.append({"role": "model", "parts": [{"text": a}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={"Content-Type": "application/json"},
                json={"contents": contents},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise RuntimeError(f"{name} HTTP {resp.status}: {data}")
                cands = data.get("candidates", [])
                if not cands:
                    return ""
                parts = cands[0].get("content", {}).get("parts", [])
                return "".join(p.get("text", "") for p in parts)
    return ask


# Sentinel base_url values for non-OpenAI-compatible providers.
# Stored in DB so load_custom_providers can rebuild the right adapter.
ANTHROPIC_BASE = "anthropic://v1"
GEMINI_BASE = "gemini://v1beta"


def make_provider(kind: str, name: str, api_key: str, model: str, base_url: str | None = None):
    """Generic factory — returns (ask_callable, base_url_to_store)."""
    k = (kind or "").lower()
    if k == "anthropic":
        return make_anthropic_provider(name, api_key, model), ANTHROPIC_BASE
    if k == "gemini":
        return make_gemini_provider(name, api_key, model), GEMINI_BASE
    # default: OpenAI-compatible
    if not base_url:
        raise ValueError(f"base_url required for kind={kind}")
    return make_openai_compatible_provider(name, base_url, api_key, model), base_url


def rebuild_provider_from_db(name: str, base_url: str, api_key: str, model: str):
    """Rebuild the right adapter when loading rows from DB."""
    if base_url == ANTHROPIC_BASE:
        return make_anthropic_provider(name, api_key, model)
    if base_url == GEMINI_BASE:
        return make_gemini_provider(name, api_key, model)
    return make_openai_compatible_provider(name, base_url, api_key, model)
