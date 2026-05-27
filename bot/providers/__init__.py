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
