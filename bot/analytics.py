# bot/analytics.py

import hashlib
import logging
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

# In pyinstaller bundles __file__ resolves to a temp extraction dir.
# Use the binary's (or script's) actual directory for persistent data.
if getattr(sys, 'frozen', False):
    _BASE_DIR = Path(sys.executable).resolve().parent
else:
    _BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = _BASE_DIR / "analytics.db"

# Salt is generated once per installation and stored alongside the DB.
# If the salt file is lost, old hashes become unmatchable — that's fine,
# it just starts a new "generation" of anonymous IDs.
_SALT_PATH = DB_PATH.with_suffix(".salt")


def _get_salt() -> bytes:
    if _SALT_PATH.exists():
        return _SALT_PATH.read_bytes()
    salt = os.urandom(32)
    _SALT_PATH.write_bytes(salt)
    return salt


_SALT = _get_salt()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    anon_user   TEXT    NOT NULL,
    anon_chat   TEXT    NOT NULL,
    chat_type   TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    video_bytes INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_anon_user ON events(anon_user);

CREATE TABLE IF NOT EXISTS known_users (
    chat_id     INTEGER PRIMARY KEY,
    first_seen  REAL    NOT NULL,
    last_seen   REAL    NOT NULL
);
"""


def _hash_id(raw_id: int) -> str:
    return hashlib.sha256(_SALT + str(raw_id).encode()).hexdigest()[:16]


@contextmanager
def _connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema():
    with _connect() as conn:
        conn.executescript(_SCHEMA)


_ensure_schema()


def record(user_id: int, chat_id: int, chat_type: str,
           status: str, video_bytes: int = 0):
    """Record a single event. Designed to never raise — analytics
    failures must not break the bot."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO events (ts, anon_user, anon_chat, chat_type, status, video_bytes)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), _hash_id(user_id), _hash_id(chat_id),
                 chat_type, status, video_bytes),
            )
    except Exception:
        logging.exception("analytics.record failed")


def touch_user(chat_id: int):
    """Register or update a private-chat user for future broadcasts."""
    try:
        now = time.time()
        with _connect() as conn:
            conn.execute(
                "INSERT INTO known_users (chat_id, first_seen, last_seen)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(chat_id) DO UPDATE SET last_seen=excluded.last_seen",
                (chat_id, now, now),
            )
    except Exception:
        logging.exception("analytics.touch_user failed")


def get_broadcast_recipients() -> list[int]:
    """Return all known private-chat user IDs."""
    try:
        with _connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT chat_id FROM known_users ORDER BY last_seen DESC")
            return [row[0] for row in cur.fetchall()]
    except Exception:
        logging.exception("analytics.get_broadcast_recipients failed")
        return []


def get_stats() -> dict:
    """Return a summary dict suitable for a /stats reply."""
    try:
        with _connect() as conn:
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*) FROM events")
            total = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM events WHERE status='ok'")
            ok = cur.fetchone()[0]

            cur.execute("SELECT COUNT(DISTINCT anon_user) FROM events")
            unique_users = cur.fetchone()[0]

            cur.execute("SELECT COUNT(DISTINCT anon_chat) FROM events")
            unique_chats = cur.fetchone()[0]

            cur.execute("SELECT SUM(video_bytes) FROM events WHERE status='ok'")
            total_bytes = cur.fetchone()[0] or 0

            # Top-10 users by request count
            cur.execute(
                "SELECT anon_user, COUNT(*) as cnt, "
                "       SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok_cnt "
                "FROM events GROUP BY anon_user ORDER BY cnt DESC LIMIT 10"
            )
            top_users = [
                {"anon_id": row[0], "requests": row[1], "success": row[2]}
                for row in cur.fetchall()
            ]

            # Last 7 days daily breakdown
            week_ago = time.time() - 7 * 86400
            cur.execute(
                "SELECT date(ts, 'unixepoch') as day, COUNT(*), "
                "       SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) "
                "FROM events WHERE ts >= ? GROUP BY day ORDER BY day",
                (week_ago,),
            )
            daily = [
                {"date": row[0], "requests": row[1], "success": row[2]}
                for row in cur.fetchall()
            ]

            return {
                "total_requests": total,
                "successful": ok,
                "failed": total - ok,
                "unique_users": unique_users,
                "unique_chats": unique_chats,
                "total_video_mb": round(total_bytes / (1024 * 1024), 1),
                "top_users": top_users,
                "daily_last_7d": daily,
            }
    except Exception:
        logging.exception("analytics.get_stats failed")
        return {}
