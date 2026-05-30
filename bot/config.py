import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
FORCE_JOIN_CHANNEL = (os.getenv("FORCE_JOIN_CHANNEL", "") or "").lstrip("@").strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip()
PORT = int(os.getenv("PORT", "10000") or 10000)
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()
# Optional MongoDB persistence (free tier 512MB). When set, owner-managed
# data (settings, custom providers, users, groups, grants) is mirrored to
# MongoDB so re-deploys / restarts preserve all customization.
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "xenex_bot").strip() or "xenex_bot"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing. Set it in .env or environment.")
if not OWNER_ID:
    raise RuntimeError("OWNER_ID missing. Set it in .env or environment.")
