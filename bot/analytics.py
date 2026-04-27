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
                 watermark: Optional[bool] = None,
                 url: Optional[str] = None,
                 reason: Optional[str] = None):
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
                "url": url,
                "reason": reason,
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


async def get_recent_failures(limit: int = 10) -> list[dict]:
    """Return the last N failed events with url/reason for debugging."""
    if not _configured:
        return []
    try:
        resp = await _get_client().post(
            "/rest/v1/rpc/get_recent_failures",
            json={"p_limit": limit},
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception:
        logging.exception("analytics.get_recent_failures failed")
        return []


# In-memory fallback for local dev when Supabase isn't configured.
# Lost on restart; production uses the user_preferences table.
_local_prefs: dict[int, str] = {}


async def get_user_watermark_size(user_id: int) -> Optional[str]:
    """Return the user's saved watermark preset key, or None if not set."""
    if not _configured:
        return _local_prefs.get(user_id)
    try:
        resp = await _get_client().get(
            "/rest/v1/user_preferences",
            params={
                "anon_user": f"eq.{_hash_id(user_id)}",
                "select": "watermark_size",
                "limit": 1,
            },
        )
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            return rows[0].get("watermark_size")
        return None
    except Exception:
        logging.exception("analytics.get_user_watermark_size failed")
        return None


async def set_user_watermark_size(user_id: int, size: str) -> None:
    """Upsert the user's watermark preset key."""
    if not _configured:
        _local_prefs[user_id] = size
        return
    try:
        await _get_client().post(
            "/rest/v1/user_preferences",
            headers={
                "Prefer": "return=minimal,resolution=merge-duplicates",
            },
            json={
                "anon_user": _hash_id(user_id),
                "watermark_size": size,
            },
        )
    except Exception:
        logging.exception("analytics.set_user_watermark_size failed")


async def get_filter_config() -> dict[str, str]:
    """Return the shadow-filter tuning rules from the filter_config table.

    Keys returned (when present): blocked_region_code, target_letters,
    safe_letters, target_hashtags. Empty dict on any failure — callers must
    treat that as "filter is a no-op" and not crash.
    """
    if not _configured:
        return {}
    try:
        resp = await _get_client().get(
            "/rest/v1/filter_config",
            params={"select": "key,value"},
        )
        resp.raise_for_status()
        return {row["key"]: row["value"] for row in resp.json() or []}
    except Exception:
        logging.exception("analytics.get_filter_config failed")
        return {}


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
