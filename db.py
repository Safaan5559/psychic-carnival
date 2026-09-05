import aiosqlite
from datetime import datetime, timezone
from config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS builds (
 id TEXT PRIMARY KEY,
 user_id INTEGER,
 filename TEXT NOT NULL,
 status TEXT NOT NULL,
 created_at TEXT NOT NULL,
 started_at TEXT,
 finished_at TEXT,
 duration_seconds REAL,
 apk_path TEXT,
 log_path TEXT,
 package_name TEXT NOT NULL,
 app_name TEXT NOT NULL,
 version_name TEXT NOT NULL,
 version_code INTEGER NOT NULL,
 error TEXT,
 notified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_builds_status ON builds(status);
"""

def now():
    return datetime.now(timezone.utc).isoformat()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()

async def one(query, args=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, args)
        return await cur.fetchone()

async def execute(query, args=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, args)
        await db.commit()
        return cur.lastrowid
