"""Microsoft Copilot provider. Adapted from user's main_1.py."""
import asyncio
import json
import threading
import uuid

import requests
import websocket


class _CopilotClient:
    def __init__(self):
        self.session = requests.Session()
        self.client_id = str(uuid.uuid4())
        self.conversation_id = None
        self._start()

    def _start(self):
        r = self.session.post(
            "https://copilot.microsoft.com/c/api/start",
            json={
                "timeZone": "Asia/Kolkata", "startNewConversation": True,
                "teenSupportEnabled": True, "correctPersonalizationSetting": True,
                "deferredDataUseCapable": True,
            },
            headers={
                "User-Agent": "CopilotNative/30.0.440421003-prod (Android 11; Google; sdk_gphone_arm64)",
                "Content-Type": "application/json", "X-Search-UILang": "en-US",
            }, timeout=30,
        )
        self.conversation_id = r.json()["currentConversationId"]

    def ask_sync(self, message: str) -> str:
        ws_url = f"wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId={self.client_id}"
        cookies = "; ".join(f"{k}={v}" for k, v in self.session.cookies.get_dict().items())
        result = {"text": "", "mid": None}
        done = threading.Event()

        def on_open(ws):
            opts = {
                "event": "setOptions",
                "supportedCards": ["image", "video", "finance", "local", "sports"],
                "supportedActions": [], "supportedFeatures": [],
            }
            ws.send(json.dumps(opts))
            ws.send(json.dumps({
                "event": "send",
                "content": [{"type": "text", "text": message}],
                "conversationId": self.conversation_id,
            }))

        def on_message(ws, msg):
            try:
                data = json.loads(msg)
            except Exception:
                return
            ev = data.get("event")
            if ev == "startMessage":
                result["mid"] = data.get("messageId")
            elif ev == "appendText" and data.get("messageId") == result["mid"]:
                result["text"] += data.get("text", "")
            elif ev == "done":
                ws.close()
                done.set()

        def on_error(_ws, _err):
            done.set()

        ws = websocket.WebSocketApp(
            ws_url,
            header=[
                f"Cookie: {cookies}",
                "User-Agent: CopilotNative/30.0.440421003-prod (Android 11; Google; sdk_gphone_arm64)",
                "X-Search-UILang: en-US",
            ],
            on_open=on_open, on_message=on_message, on_error=on_error,
        )
        threading.Thread(target=ws.run_forever, daemon=True).start()
        done.wait(timeout=90)
        if not result["text"]:
            raise RuntimeError("Empty response from Copilot")
        return result["text"]


def _ask_sync(prompt: str, history: list) -> str:
    if history:
        ctx = "\n".join(f"User: {h['q']}\nAssistant: {h['a']}" for h in history[-3:])
        prompt = f"Context:\n{ctx}\n\nQuestion: {prompt}"
    client = _CopilotClient()
    return client.ask_sync(prompt)


async def ask(prompt: str, history: list) -> str:
    return await asyncio.to_thread(_ask_sync, prompt, history)


# ---------------------------------------------------------------------------
# Streaming variant — yields incremental text deltas as Copilot generates.
# Used by the guest-mention / bot-to-bot handler so replies feel animated
# (matches Telegram's May 2026 "streamed AI answers" platform feature).
# ---------------------------------------------------------------------------
async def ask_stream(prompt: str, history: list):
    """Async generator: yields cumulative answer text as it grows."""
    if history:
        ctx = "\n".join(f"User: {h['q']}\nAssistant: {h['a']}" for h in history[-3:])
        prompt = f"Context:\n{ctx}\n\nQuestion: {prompt}"

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()

    def _runner():
        try:
            client = _CopilotClient()
            ws_url = (
                f"wss://copilot.microsoft.com/c/api/chat?api-version=2&"
                f"clientSessionId={client.client_id}"
            )
            cookies = "; ".join(
                f"{k}={v}" for k, v in client.session.cookies.get_dict().items()
            )
            state = {"mid": None, "buf": ""}

            def on_open(ws):
                ws.send(json.dumps({
                    "event": "setOptions",
                    "supportedCards": ["image", "video", "finance", "local", "sports"],
                    "supportedActions": [], "supportedFeatures": [],
                }))
                ws.send(json.dumps({
                    "event": "send",
                    "content": [{"type": "text", "text": prompt}],
                    "conversationId": client.conversation_id,
                }))

            def on_message(ws, msg):
                try:
                    data = json.loads(msg)
                except Exception:
                    return
                ev = data.get("event")
                if ev == "startMessage":
                    state["mid"] = data.get("messageId")
                elif ev == "appendText" and data.get("messageId") == state["mid"]:
                    state["buf"] += data.get("text", "")
                    loop.call_soon_threadsafe(queue.put_nowait, state["buf"])
                elif ev == "done":
                    ws.close()
                    loop.call_soon_threadsafe(queue.put_nowait, DONE)

            def on_error(_ws, _err):
                loop.call_soon_threadsafe(queue.put_nowait, DONE)

            ws = websocket.WebSocketApp(
                ws_url,
                header=[
                    f"Cookie: {cookies}",
                    "User-Agent: CopilotNative/30.0.440421003-prod (Android 11; Google; sdk_gphone_arm64)",
                    "X-Search-UILang: en-US",
                ],
                on_open=on_open, on_message=on_message, on_error=on_error,
            )
            ws.run_forever()
        except Exception:
            loop.call_soon_threadsafe(queue.put_nowait, DONE)

    threading.Thread(target=_runner, daemon=True).start()

    while True:
        item = await asyncio.wait_for(queue.get(), timeout=120)
        if item is DONE:
            return
        yield item
