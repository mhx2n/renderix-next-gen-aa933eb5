"""
MongoDB persistence layer (advanced sync mirror for SQLite).

Design goals:
- Keep SQLite as the hot operational store (zero changes for read paths).
- Mirror only OWNER-managed, non-ephemeral state to MongoDB:
    settings, custom_providers, speak_grants, groups, users (compact).
- Skip volatile / high-churn collections (logs, sessions, usage_quota,
  start_events) to stay well under the 512 MB free-tier budget.
- On startup, if SQLite is empty/fresh (after a redeploy), restore from
  MongoDB so every owner edit survives re-deploys & bot updates.
- All sync calls are fire-and-forget and tolerate network failures.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import MONGODB_URI, MONGODB_DB

log = logging.getLogger("mongo")

_client = None
_dbh = None
_enabled = False


def enabled() -> bool:
    return _enabled


async def init():
    """Connect to MongoDB if MONGODB_URI is set. Safe no-op otherwise."""
    global _client, _dbh, _enabled
    if not MONGODB_URI:
        log.info("MongoDB disabled (no MONGODB_URI). Running SQLite-only.")
        return
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        _client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=8000)
        # Force a ping to verify connectivity
        await _client.admin.command("ping")
        _dbh = _client[MONGODB_DB]
        _enabled = True
        log.info("MongoDB connected: db=%s", MONGODB_DB)
    except Exception as e:
        _enabled = False
        log.warning("MongoDB init failed: %s — continuing with SQLite only.", e)


def _coll(name: str):
    if not _enabled or _dbh is None:
        return None
    return _dbh[name]


async def _safe(coro):
    try:
        await coro
    except Exception as e:
        log.warning("mongo op failed: %s", e)


def fire(coro):
    """Fire-and-forget a mongo coroutine without blocking the caller."""
    if not _enabled:
        try:
            coro.close()
        except Exception:
            pass
        return
    try:
        asyncio.get_event_loop().create_task(_safe(coro))
    except RuntimeError:
        # No running loop — run synchronously as last resort
        try:
            asyncio.run(_safe(coro))
        except Exception:
            pass


# ---------- write helpers (called from db.py) ----------
async def upsert(coll: str, key: dict, doc: dict):
    c = _coll(coll)
    if c is None:
        return
    await c.update_one(key, {"$set": doc}, upsert=True)


async def delete(coll: str, key: dict):
    c = _coll(coll)
    if c is None:
        return
    await c.delete_one(key)


# ---------- restore (called once at startup AFTER init_db) ----------
async def restore_to_sqlite():
    """Pull all mirrored collections from MongoDB back into SQLite.
    Idempotent: uses INSERT ... ON CONFLICT DO UPDATE everywhere."""
    if not _enabled:
        return
    import aiosqlite
    from .config import DB_PATH

    restored = {"settings": 0, "custom_providers": 0, "speak_grants": 0,
                "groups": 0, "users": 0}
    try:
        async with aiosqlite.connect(DB_PATH) as sdb:
            # settings
            c = _coll("settings")
            if c is not None:
                async for row in c.find({}):
                    await sdb.execute(
                        "INSERT INTO settings(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (row.get("_id"), row.get("value", "")),
                    )
                    restored["settings"] += 1
            # custom_providers
            c = _coll("custom_providers")
            if c is not None:
                async for row in c.find({}):
                    await sdb.execute(
                        """INSERT INTO custom_providers(cmd,name,base_url,api_key,model,enabled,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?)
                           ON CONFLICT(cmd) DO UPDATE SET
                               name=excluded.name, base_url=excluded.base_url,
                               api_key=excluded.api_key, model=excluded.model,
                               enabled=excluded.enabled, updated_at=excluded.updated_at""",
                        (row.get("_id"), row.get("name", ""), row.get("base_url", ""),
                         row.get("api_key", ""), row.get("model", ""),
                         int(row.get("enabled", 1)),
                         int(row.get("created_at", 0)), int(row.get("updated_at", 0))),
                    )
                    restored["custom_providers"] += 1
            # speak_grants
            c = _coll("speak_grants")
            if c is not None:
                async for row in c.find({}):
                    await sdb.execute(
                        "INSERT OR REPLACE INTO speak_grants(user_id, granted_at) VALUES(?,?)",
                        (int(row.get("_id")), int(row.get("granted_at", 0))),
                    )
                    restored["speak_grants"] += 1
            # groups
            c = _coll("groups")
            if c is not None:
                async for row in c.find({}):
                    await sdb.execute(
                        """INSERT INTO groups(chat_id,title,added_at,removed) VALUES(?,?,?,?)
                           ON CONFLICT(chat_id) DO UPDATE SET
                               title=excluded.title, removed=excluded.removed""",
                        (int(row.get("_id")), row.get("title", ""),
                         int(row.get("added_at", 0)), int(row.get("removed", 0))),
                    )
                    restored["groups"] += 1
            # users (compact)
            c = _coll("users")
            if c is not None:
                async for row in c.find({}):
                    await sdb.execute(
                        """INSERT INTO users(user_id, username, first_name, last_seen, first_seen, is_banned, msg_count)
                           VALUES(?,?,?,?,?,?,?)
                           ON CONFLICT(user_id) DO UPDATE SET
                               username=excluded.username,
                               first_name=excluded.first_name,
                               is_banned=excluded.is_banned,
                               msg_count=MAX(users.msg_count, excluded.msg_count)""",
                        (int(row.get("_id")), row.get("username", ""),
                         row.get("first_name", ""),
                         int(row.get("last_seen", 0)), int(row.get("first_seen", 0)),
                         int(row.get("is_banned", 0)), int(row.get("msg_count", 0))),
                    )
                    restored["users"] += 1
            await sdb.commit()
        log.info("Restored from MongoDB: %s", restored)
    except Exception as e:
        log.warning("Mongo restore failed: %s", e)