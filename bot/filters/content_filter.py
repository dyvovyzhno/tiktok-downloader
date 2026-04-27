# bot/filters/content_filter.py
#
# Regional content filter for TikTok videos.
#
# Looks at the metadata that comes back with the video (region code,
# description text, hashtags) and returns BLOCK or ALLOW. On BLOCK, the
# caller shadow-fails with a generic error so the user can't tell the
# filter exists.
#
# The active rules live in the Supabase `filter_config` table, NOT in this
# file — call `await reload()` once at startup to pull them. Until then (or
# if Supabase is unreachable), the filter is a no-op: ALLOW for everything.
#
# Compares two configured sets of "exclusive" letters: one set is unique to
# the restricted-region language, the other to the safe-region language. Any
# safe-only letter anywhere in the text → ALLOW. Any target-only letter with
# zero safe-only letters → BLOCK.

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from bot import analytics


class FilterAction(Enum):
    ALLOW = "allow"
    BLOCK = "block"


# ── runtime config (populated by reload()) ──────────────────────────────────

_blocked_region_code: str = ""
_target_letters: set[str] = set()
_safe_letters: set[str] = set()
_target_hashtags: tuple[str, ...] = ()


def configure(
    blocked_region_code: str,
    target_letters: str,
    safe_letters: str,
    target_hashtags_csv: str,
) -> None:
    """Set the active rules in-process. Called by reload() and tests."""
    global _blocked_region_code, _target_letters, _safe_letters, _target_hashtags
    _blocked_region_code = (blocked_region_code or "").strip().upper()
    _target_letters = set(target_letters or "")
    _safe_letters = set(safe_letters or "")
    _target_hashtags = tuple(
        h.strip().lower()
        for h in (target_hashtags_csv or "").split(",")
        if h.strip()
    )


async def reload() -> None:
    """Pull the active rules from Supabase. Logs and no-ops on failure."""
    cfg = await analytics.get_filter_config()
    configure(
        blocked_region_code=cfg.get("blocked_region_code", ""),
        target_letters=cfg.get("target_letters", ""),
        safe_letters=cfg.get("safe_letters", ""),
        target_hashtags_csv=cfg.get("target_hashtags", ""),
    )
    logging.info(
        f"content_filter loaded: region={_blocked_region_code or '-'} "
        f"target_letters={len(_target_letters)} "
        f"safe_letters={len(_safe_letters)} "
        f"hashtags={len(_target_hashtags)}"
    )


# ── implementation ──────────────────────────────────────────────────────────

def _count_letters(text: str, letters: set[str]) -> int:
    if not letters:
        return 0
    return sum(1 for ch in text if ch in letters)


def _matched_hashtag(description: str) -> Optional[str]:
    if not _target_hashtags:
        return None
    lower = description.lower()
    for tag in _target_hashtags:
        if tag in lower:
            return tag
    return None


def evaluate(
    description: Optional[str],
    region: Optional[str],
    nickname: Optional[str] = None,
    signature: Optional[str] = None,
    author: Optional[str] = None,
) -> tuple[FilterAction, Optional[str]]:
    """Return (action, reason). `reason` is a short tag for logging.

    Description, nickname (display name), signature (bio) and author handle
    (uniqueId) are all checked together: if a target signal appears in any
    of them, it counts. This catches creators with empty captions but a
    target signal in display name or bio, plus transliterated handles when
    the substring list includes ASCII variants.
    """
    if (
        _blocked_region_code
        and region
        and region.upper() == _blocked_region_code
    ):
        return FilterAction.BLOCK, "region"

    text = " ".join(filter(None, [description, nickname, signature, author]))
    if not text:
        return FilterAction.ALLOW, None

    tag = _matched_hashtag(text)
    if tag:
        return FilterAction.BLOCK, f"hashtag:{tag}"

    target = _count_letters(text, _target_letters)
    safe = _count_letters(text, _safe_letters)

    if safe >= 1:
        return FilterAction.ALLOW, None

    if target >= 1:
        return FilterAction.BLOCK, f"letters(t={target},len={len(text)})"

    return FilterAction.ALLOW, None
