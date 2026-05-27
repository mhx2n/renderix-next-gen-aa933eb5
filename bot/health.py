"""Tiny Flask app for Render health check. Runs in a background thread."""
import threading
import time
from flask import Flask, jsonify

START_TS = time.time()


def create_app(bot_info: dict):
    app = Flask(__name__)

    @app.get("/")
    def root():
        return jsonify({
            "status": "ok",
            "service": "ai-multi-bot",
            "uptime_seconds": int(time.time() - START_TS),
            "bot": bot_info,
        })

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    return app


def run_in_thread(port: int, bot_info: dict):
    app = create_app(bot_info)
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
    return t
