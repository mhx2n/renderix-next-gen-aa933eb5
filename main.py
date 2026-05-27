"""
Advanced Multi-AI Telegram Bot.

Single entrypoint for Render Web Service and VPS.
- Starts a Flask health server on $PORT (so Render's free web service stays alive).
- Starts the Telegram bot (long-polling) concurrently.
- Multi-user, fully asynchronous, error-isolated per request.
"""
import asyncio
import logging
import sys
import traceback

from telegram.error import Conflict
from telegram.ext import ApplicationBuilder

from bot.config import BOT_TOKEN, PORT
from bot.db import init_db, log as db_log
from bot.handlers import (
    register_handlers, setup_bot_commands, notify_restart_complete,
    load_custom_providers,
)
from bot.health import run_in_thread

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

log = logging.getLogger("main")


async def _amain():
    await init_db()
    stop = asyncio.Event()

    def _polling_error_callback(exc):
        if isinstance(exc, Conflict):
            log.critical(
                "Telegram polling conflict detected: another bot instance is already using getUpdates. "
                "Stopping this instance to avoid endless crash loops."
            )
            stop.set()
            return
        log.exception("Polling error: %s", exc)

    async def _startup_error(stage: str, exc: Exception):
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[:1800]
        log.error("Startup failure during %s: %s", stage, exc)
        try:
            await db_log("ERROR", 0, "system", f"startup:{stage}\n{tb}")
        except Exception:
            pass

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)   # multi-user concurrency
        .build()
    )
    # Populate custom providers in REGISTRY BEFORE wiring handlers so each
    # custom provider gets its own /command and .alias automatically.
    await load_custom_providers(None)
    register_handlers(app)

    try:
        await app.initialize()
        await app.start()
    except Exception as exc:
        await _startup_error("initialize/start", exc)
        raise

    # Health server (non-blocking, daemon thread)
    try:
        me = await app.bot.get_me()
    except Exception as exc:
        await _startup_error("get_me", exc)
        raise
    run_in_thread(PORT, {"username": me.username, "id": me.id})
    log.info("Health server on :%s | Bot @%s started", PORT, me.username)

    # Register Telegram command menus (per-scope: user vs owner)
    try:
        await setup_bot_commands(app)
        await notify_restart_complete(app)
    except Exception as exc:
        await _startup_error("post-start setup", exc)
        raise

    try:
        await app.bot.delete_webhook(drop_pending_updates=False)
    except Exception as exc:
        log.warning("Could not clear webhook before polling: %s", exc)
    await asyncio.sleep(2)

    # Drop pending updates from previous run to avoid double-processing.
    try:
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=[
                "message", "callback_query", "edited_message", "inline_query",
            ],
            error_callback=_polling_error_callback,
        )
    except Exception as exc:
        await _startup_error("start_polling", exc)
        raise
    log.info("Polling started. Press Ctrl+C to stop.")

    # Run until cancelled
    try:
        await stop.wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    try:
        asyncio.run(_amain())
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
