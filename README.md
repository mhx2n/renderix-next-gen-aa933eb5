# Advanced Multi-AI Telegram Bot

A production-grade, fully asynchronous Telegram bot that:

- Talks to **Gemini**, **Perplexity**, and **Copilot** out of the box (no provider API keys required).
- Inspects **any** AI provider API key (OpenAI, Anthropic, Google Gemini, Groq, OpenRouter, Cohere, DeepSeek, xAI, Together AI, and any OpenAI-compatible) and shows available models, limits, quota, and lets the user try a model with a prompt.
- Supports **both `/cmd` and `.cmd`** prefixes for every command.
- Hides **owner-only** commands from regular users.
- Enforces **force-join** on a configurable channel before usage.
- Supports **reply-to-continue**: reply to any AI answer and the conversation continues in that provider's context.
- Handles **many users concurrently** (PTB `concurrent_updates=True`).
- Persists users, logs, sessions, and settings in **SQLite** (`bot.db`).
- Returns **clean plain text** (Markdown, LaTeX, HTML tags stripped).
- Ships with a **Flask health endpoint** so it runs as a Render free **Web Service** (24/7).
- Works **on a VPS** the same way (just `python main.py`).

## Quick start

```bash
cp .env.example .env
# fill BOT_TOKEN, OWNER_ID, optionally FORCE_JOIN_CHANNEL
pip install -r requirements.txt
python main.py
```

Health check: `http://localhost:10000/health`

## Deploy to Render (free)

1. Push this repo to GitHub.
2. On Render: **New > Blueprint** and point to this repo (uses `render.yaml`).
3. Set env vars `BOT_TOKEN`, `OWNER_ID`, and (optional) `FORCE_JOIN_CHANNEL`.
4. Deploy. Render's health check hits `/` and keeps the service alive.

## Deploy to VPS

```bash
git clone <repo> && cd <repo>
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit
nohup python main.py > bot.log 2>&1 &
```

Or as a systemd service — see `systemd.service.example` below.

## Commands

### User
| Command | Description |
|---|---|
| `/start` `.start` | Welcome + provider list |
| `/help` `.help` | Full user help |
| `/menu` `.menu` | Inline provider menu |
| `/ping` `.ping` | Latency check |
| `/g <prompt>` `.g <prompt>` | Ask **Gemini** |
| `/pr <prompt>` `.pr <prompt>` | Ask **Perplexity** |
| `/co <prompt>` `.co <prompt>` | Ask **Copilot** |
| `/key <API_KEY>` `.key <API_KEY>` | Inspect any AI API key |
| `/tryke <model> <prompt>` | Run a prompt with the last-inspected key |

Reply to any bot answer to continue that conversation in the same provider.

### Owner (hidden from users)
| Command | Description |
|---|---|
| `/owner` | Show owner menu |
| `/stats` | Users, messages, errors, channel |
| `/logs [n]` | Last `n` log entries (default 20) |
| `/users` | Active user count |
| `/setchannel <user>` | Set/change force-join channel (`off` to disable) |
| `/ban <id>` / `/unban <id>` | Block / unblock users |
| `/announce <text>` | Broadcast to all users (also works as a reply) |

## Extending with new providers

`bot/providers/__init__.py` exposes `register(cmd, name, async_fn)`. Drop a new
module in `bot/providers/`, then call `register("ds", "DeepSeek", deepseek.ask)`
from there. The bot picks it up automatically (slash command, dot command,
menu button, and reply-to-continue all start working).

## Notes

- Output is sanitised: `#`, `*`, `_`, `~`, `` ` ``, `>`, `|`, LaTeX (`$...$`, `\(...\)`), and HTML tags are stripped before sending so Telegram displays clean text.
- Long answers are auto-chunked into 4000-char messages.
- All provider calls are wrapped with a 120 s timeout and exceptions are logged to the DB, not silently swallowed.

## Downloader notes

- Downloads now run one at a time to keep free hosting stable and avoid overload.
- The bot normalizes short/share links (TikTok, Facebook, Instagram) before download and converts media to Telegram-friendly MP3/MP4 when needed.
- Some YouTube links may still trigger server-side bot checks. To improve success rate, export fresh browser cookies into `youtube_cookies.txt` or set `YT_COOKIES_FILE` to a valid cookies file path, then restart/redeploy.
