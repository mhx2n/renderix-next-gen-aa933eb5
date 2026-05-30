import aiosqlite
import time
from .config import DB_PATH
from . import mongo

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_seen INTEGER,
    first_seen INTEGER,
    is_banned INTEGER DEFAULT 0,
    msg_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER,
    level TEXT,
    user_id INTEGER,
    provider TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    chat_id INTEGER,
    message_id INTEGER,
    provider TEXT,
    state TEXT,
    updated_at INTEGER,
    PRIMARY KEY (chat_id, message_id)
);
CREATE TABLE IF NOT EXISTS speak_grants (
    user_id INTEGER PRIMARY KEY,
    granted_at INTEGER
);
CREATE TABLE IF NOT EXISTS speak_active (
    user_id INTEGER PRIMARY KEY,
    target_chat_id INTEGER,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS custom_providers (
    cmd TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    model TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    created_at INTEGER,
    updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS usage_quota (
    user_id INTEGER,
    tool TEXT,
    day TEXT,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, tool, day)
);
CREATE TABLE IF NOT EXISTS start_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    user_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_start_events_ts ON start_events(ts);
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    added_at INTEGER,
    removed INTEGER DEFAULT 0
);
"""


async def quota_check_and_inc(user_id: int, tool: str, daily_limit: int) -> tuple[bool, int]:
    """Returns (allowed, used_after). Atomically increments if allowed."""
    import datetime as _dt
    day = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT count FROM usage_quota WHERE user_id=? AND tool=? AND day=?",
            (user_id, tool, day))
        row = await cur.fetchone()
        used = row[0] if row else 0
        if used >= daily_limit:
            return False, used
        new = used + 1
        await db.execute(
            "INSERT INTO usage_quota(user_id,tool,day,count) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,tool,day) DO UPDATE SET count=excluded.count",
            (user_id, tool, day, new))
        await db.commit()
        return True, new


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def upsert_user(user):
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (user_id, username, first_name, last_seen, first_seen, msg_count)
               VALUES (?, ?, ?, ?, ?, 1)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username,
                 first_name=excluded.first_name,
                 last_seen=excluded.last_seen,
                 msg_count=users.msg_count+1
            """,
            (user.id, user.username or "", user.first_name or "", now, now),
        )
        await db.commit()
        # Mirror compact row to Mongo (fire & forget). Mongo stays small.
        try:
            async with db.execute(
                "SELECT username, first_name, last_seen, first_seen, is_banned, msg_count "
                "FROM users WHERE user_id=?", (user.id,)
            ) as cur:
                row = await cur.fetchone()
        except Exception:
            row = None
    if row:
        mongo.fire(mongo.upsert("users", {"_id": int(user.id)}, {
            "username": row[0], "first_name": row[1],
            "last_seen": row[2], "first_seen": row[3],
            "is_banned": row[4], "msg_count": row[5],
        }))


async def is_banned(uid: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_banned FROM users WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def set_banned(uid: int, val: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET is_banned=? WHERE user_id=?", (val, uid))
        await db.commit()
    mongo.fire(mongo.upsert("users", {"_id": int(uid)}, {"is_banned": int(val)}))


async def log(level: str, user_id: int, provider: str, message: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO logs (ts, level, user_id, provider, message) VALUES (?,?,?,?,?)",
            (int(time.time()), level, user_id or 0, provider or "", (message or "")[:2000]),
        )
        await db.commit()


async def get_logs(limit: int = 30):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT ts, level, user_id, provider, message FROM logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return await cur.fetchall()


async def all_user_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE is_banned=0") as cur:
            return [r[0] for r in await cur.fetchall()]


async def stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*), COALESCE(SUM(msg_count),0), SUM(is_banned) FROM users") as cur:
            users, msgs, banned = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM logs WHERE level='ERROR'") as cur:
            errs = (await cur.fetchone())[0]
        return {
            "users": users or 0,
            "messages": msgs or 0,
            "banned": banned or 0,
            "errors": errs or 0,
        }


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()
    mongo.fire(mongo.upsert("settings", {"_id": key}, {"value": value}))


async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default


async def save_session(chat_id: int, message_id: int, provider: str, state: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO sessions(chat_id,message_id,provider,state,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(chat_id,message_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at""",
            (chat_id, message_id, provider, state, int(time.time())),
        )
        await db.commit()


async def get_session(chat_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT provider, state FROM sessions WHERE chat_id=? AND message_id=?",
            (chat_id, message_id),
        ) as cur:
            return await cur.fetchone()


# ---------- speak-as-bot grants ----------
async def grant_speak(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO speak_grants(user_id, granted_at) VALUES(?,?)",
            (uid, int(time.time())),
        )
        await db.commit()
    mongo.fire(mongo.upsert("speak_grants", {"_id": int(uid)},
                            {"granted_at": int(time.time())}))


async def revoke_speak(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM speak_grants WHERE user_id=?", (uid,))
        await db.execute("DELETE FROM speak_active WHERE user_id=?", (uid,))
        await db.commit()
    mongo.fire(mongo.delete("speak_grants", {"_id": int(uid)}))


async def can_speak(uid: int, owner_id: int) -> bool:
    if uid == owner_id:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM speak_grants WHERE user_id=?", (uid,)) as cur:
            return bool(await cur.fetchone())


async def list_speak_grants():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, granted_at FROM speak_grants") as cur:
            return await cur.fetchall()


async def set_speak_target(uid: int, chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        if chat_id is None:
            await db.execute("DELETE FROM speak_active WHERE user_id=?", (uid,))
        else:
            await db.execute(
                "INSERT OR REPLACE INTO speak_active(user_id,target_chat_id,updated_at) VALUES(?,?,?)",
                (uid, int(chat_id), int(time.time())),
            )
        await db.commit()


async def get_speak_target(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT target_chat_id FROM speak_active WHERE user_id=?", (uid,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


# ---------- custom providers ----------
async def add_custom_provider(cmd: str, name: str, base_url: str, api_key: str, model: str):
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO custom_providers(cmd,name,base_url,api_key,model,enabled,created_at,updated_at)
               VALUES(?,?,?,?,?,1,?,?)
               ON CONFLICT(cmd) DO UPDATE SET
                   name=excluded.name,
                   base_url=excluded.base_url,
                   api_key=excluded.api_key,
                   model=excluded.model,
                   enabled=1,
                   updated_at=excluded.updated_at
            """,
            (cmd, name, base_url, api_key, model, now, now),
        )
        await db.commit()
    mongo.fire(mongo.upsert("custom_providers", {"_id": cmd}, {
        "name": name, "base_url": base_url, "api_key": api_key,
        "model": model, "enabled": 1, "created_at": now, "updated_at": now,
    }))


async def remove_custom_provider(cmd: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM custom_providers WHERE cmd=?", (cmd,))
        await db.commit()
    mongo.fire(mongo.delete("custom_providers", {"_id": cmd}))


async def list_custom_providers(enabled_only: bool = True):
    query = (
        "SELECT cmd, name, base_url, api_key, model, enabled FROM custom_providers WHERE enabled=1 ORDER BY cmd"
        if enabled_only else
        "SELECT cmd, name, base_url, api_key, model, enabled FROM custom_providers ORDER BY cmd"
    )
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query) as cur:
            return await cur.fetchall()


# ---------- start events & groups ----------
async def log_start(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO start_events(ts, user_id) VALUES(?, ?)",
            (int(time.time()), int(user_id or 0)),
        )
        await db.commit()


async def add_group(chat_id: int, title: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO groups(chat_id, title, added_at, removed) VALUES(?,?,?,0)
               ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, removed=0""",
            (int(chat_id), title or "", int(time.time())),
        )
        await db.commit()
    mongo.fire(mongo.upsert("groups", {"_id": int(chat_id)}, {
        "title": title or "", "added_at": int(time.time()), "removed": 0,
    }))


async def remove_group(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE groups SET removed=1 WHERE chat_id=?", (int(chat_id),))
        await db.commit()
    mongo.fire(mongo.upsert("groups", {"_id": int(chat_id)}, {"removed": 1}))


async def usage_report() -> dict:
    now = int(time.time())
    spans = {"daily": 86400, "weekly": 86400 * 7, "monthly": 86400 * 30, "annual": 86400 * 365}
    out = {}
    async with aiosqlite.connect(DB_PATH) as db:
        for key, sec in spans.items():
            async with db.execute(
                "SELECT COUNT(*) FROM start_events WHERE ts >= ?", (now - sec,)
            ) as cur:
                out[key] = (await cur.fetchone())[0] or 0
        async with db.execute("SELECT COUNT(*) FROM groups WHERE removed=0") as cur:
            out["groups"] = (await cur.fetchone())[0] or 0
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            out["users"] = (await cur.fetchone())[0] or 0
    return out


async def top_users(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT user_id, first_name, username, msg_count
               FROM users WHERE is_banned=0
               ORDER BY msg_count DESC LIMIT ?""",
            (int(limit),),
        ) as cur:
            return await cur.fetchall()
