# bot/analytics.py
#
# Anonymous analytics backed by Supabase (PostgreSQL via REST API).
# Uses httpx (already a project dependency) — no extra packages needed.
#
# All public functions are async and designed to never raise: analytics
# failures must not break the bot.

from __future__ import annotations

import hashlib
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from settings import SUPABASE_URL, SUPABASE_KEY

# ── anonymous-id hashing ─────────────────────────────────────────────
# Salt is generated once per installation and stored on disk.
# If the salt file is lost, old hashes become unmatchable — that's fine,
# it just starts a new "generation" of anonymous IDs.

if getattr(sys, 'frozen', False):
    _BASE_DIR = Path(sys.executable).resolve().parent.parent
else:
    _BASE_DIR = Path(__file__).resolve().parent.parent

_SALT_PATH = _BASE_DIR / "analytics.salt"


def _get_salt() -> bytes:
    if _SALT_PATH.exists():
        return _SALT_PATH.read_bytes()
    salt = os.urandom(32)
    _SALT_PATH.write_bytes(salt)
    return salt


_SALT = _get_salt()


def _hash_id(raw_id: int) -> str:
    return hashlib.sha256(_SALT + str(raw_id).encode()).hexdigest()[:16]


# ── Supabase REST client ─────────────────────────────────────────────

_configured = bool(SUPABASE_URL and SUPABASE_KEY)

if not _configured:
    logging.warning("SUPABASE not configured — analytics disabled")

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=SUPABASE_URL,
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
    return _client


# ── public API ────────────────────────────────────────────────────────

async def record(user_id: int, chat_id: int, chat_type: str,
                 status: str, video_bytes: int = 0,
                 watermark: Optional[bool] = None):
    """Insert one analytics event."""
    if not _configured:
        return
    try:
        await _get_client().post(
            "/rest/v1/events",
            headers={"Prefer": "return=minimal"},
            json={
                "ts": time.time(),
                "anon_user": _hash_id(user_id),
                "anon_chat": _hash_id(chat_id),
                "chat_type": chat_type,
                "status": status,
                "video_bytes": video_bytes,
                "watermark": watermark,
            },
        )
    except Exception:
        logging.exception("analytics.record failed")


async def touch_user(chat_id: int):
    """Register or update a private-chat user for future broadcasts."""
    if not _configured:
        return
    try:
        await _get_client().post(
            "/rest/v1/rpc/touch_user",
            json={"p_chat_id": chat_id},
        )
    except Exception:
        logging.exception("analytics.touch_user failed")


async def get_broadcast_recipients() -> list[int]:
    """Return all known private-chat user IDs."""
    if not _configured:
        return []
    try:
        resp = await _get_client().get(
            "/rest/v1/known_users",
            params={"select": "chat_id", "order": "last_seen.desc"},
        )
        resp.raise_for_status()
        return [row["chat_id"] for row in resp.json()]
    except Exception:
        logging.exception("analytics.get_broadcast_recipients failed")
        return []


async def get_stats() -> dict:
    """Return a summary dict suitable for a /stats reply."""
    if not _configured:
        return {}
    try:
        resp = await _get_client().post(
            "/rest/v1/rpc/get_stats",
            json={},
        )
        resp.raise_for_status()
        data = resp.json()
        total_bytes = data.get("total_video_bytes", 0)
        data["total_video_mb"] = round(total_bytes / (1024 * 1024), 1)
        data.pop("total_video_bytes", None)
        return data
    except Exception:
        logging.exception("analytics.get_stats failed")
        return {}
