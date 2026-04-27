# bot/queue.py
#
# Download queue with limited concurrency.  Workers pull tasks from an
# asyncio.Queue and process at most MAX_CONCURRENT_DOWNLOADS in parallel,
# protecting the (weak) server from overload.
#
# The message handler asks the user (via inline buttons) whether to add
# an author watermark *before* queuing the download.  The callback
# handler then enqueues the task with the chosen flag.

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from aiogram.types import InputFile, Message
from aiogram.utils.exceptions import RetryAfter, BadRequest

from bot import bot
from bot.api.tiktok import TikTokAPI, Retrying
from bot.overlay import add_author_overlay, DEFAULT_WATERMARK_SIZE
from bot import analytics
from bot import telemetry
from settings import MAX_CONCURRENT_DOWNLOADS

_tiktok = TikTokAPI(headers={"Referer": "https://www.tiktok.com/"})

# ── pending tasks (waiting for watermark choice) ─────────────────────

PENDING_TTL = 300  # 5 min — expire if no button tap


@dataclass
class PendingTask:
    """Stored while we wait for the user to choose watermark on/off."""
    urls: list[str]
    message: Message
    user_id: int
    chat_id: int
    chat_type: str
    track: bool
    created: float


_pending: dict[str, PendingTask] = {}


def store_pending(urls: list[str], message: Message,
                  user_id: int, chat_id: int, chat_type: str,
                  track: bool) -> str:
    key = uuid.uuid4().hex[:12]
    _pending[key] = PendingTask(
        urls=urls, message=message, user_id=user_id,
        chat_id=chat_id, chat_type=chat_type, track=track,
        created=time.time(),
    )
    return key


def pop_pending(key: str) -> Optional[PendingTask]:
    return _pending.pop(key, None)


def _cleanup_stale():
    now = time.time()
    stale = [k for k, v in _pending.items() if now - v.created > PENDING_TTL]
    for k in stale:
        _pending.pop(k, None)


# ── download queue ───────────────────────────────────────────────────


@dataclass
class StatusMessage:
    """Shared reference to the "⏳ Завантажую..." status message.

    A single status message covers every URL in a batch, so `remaining`
    is decremented by each task; the message is deleted when it hits 0.
    """
    chat_id: int
    message_id: int
    remaining: int


WATERMARK_MODES = ("tt", "custom", "none")


@dataclass
class DownloadTask:
    url: str
    message: Message
    user_id: int
    chat_id: int
    chat_type: str
    track: bool
    # "tt"     — prefer TikTok's own watermark (fall back to custom overlay
    #            if the watermarked stream isn't available)
    # "custom" — strip TikTok watermark, burn our @author overlay on top
    # "none"   — clean video, no watermark
    watermark_mode: str = "tt"
    watermark_size: str = DEFAULT_WATERMARK_SIZE
    status: Optional[StatusMessage] = None


# Created in start_workers() so the Queue binds to the running event loop
# (Python 3.9 asyncio.Queue captures the loop at construction time).
_queue: asyncio.Queue = None  # type: ignore[assignment]
_active: int = 0


def pending_count() -> int:
    return _queue.qsize()


def active() -> int:
    return _active


def total_ahead() -> int:
    return _queue.qsize() + _active


async def enqueue(task: DownloadTask) -> int:
    ahead = _queue.qsize() + _active
    await _queue.put(task)
    return ahead


def extract_urls(message: Message) -> list[str]:
    return list(_tiktok._extract_urls_from_message(message))


# ── worker logic ─────────────────────────────────────────────────────

def _build_video_file(content: bytes, author: Optional[str]) -> InputFile:
    safe_author = re.sub(r"[^A-Za-z0-9_.-]+", "_", author).strip("_") if author else ""
    filename = f"tiktok_{safe_author}.mp4" if safe_author else "tiktok.mp4"
    return InputFile(io.BytesIO(content), filename=filename)


async def send_video(task: DownloadTask, content: bytes, author: Optional[str] = None):
    try:
        await bot.send_video(
            task.chat_id,
            _build_video_file(content, author),
            reply_to_message_id=task.message.message_id,
        )
    except RetryAfter as e:
        logging.warning(f"Telegram rate limit, waiting {e.timeout}s")
        await asyncio.sleep(int(e.timeout))
        await bot.send_video(
            task.chat_id,
            _build_video_file(content, author),
            reply_to_message_id=task.message.message_id,
        )


async def _reply_error(task: DownloadTask, text: str):
    try:
        await bot.send_message(
            task.chat_id, text,
            reply_to_message_id=task.message.message_id,
        )
    except BadRequest:
        pass


async def _process(task: DownloadTask):
    global _active
    _active += 1
    try:
        _cleanup_stale()

        prefer_tt = task.watermark_mode == "tt"
        video = await _tiktok.download_video(task.url, prefer_watermarked=prefer_tt)
        if not video or not video.content:
            return

        # Apply our custom overlay when:
        #  - mode is "custom" (always), or
        #  - mode is "tt" but TikTok's watermarked version wasn't available.
        # "none" never overlays.
        should_overlay = (
            task.watermark_mode == "custom"
            or (task.watermark_mode == "tt" and not video.has_watermark)
        )
        if should_overlay and video.author:
            logging.info(
                f"applying ffmpeg @author overlay for @{video.author} "
                f"(mode={task.watermark_mode}, size={task.watermark_size})"
            )
            content = await add_author_overlay(
                video.content, video.author, task.watermark_size,
            )
        else:
            content = video.content

        any_watermark = task.watermark_mode != "none"

        await send_video(task, content, author=video.author)
        if task.track:
            await analytics.record(task.user_id, task.chat_id, task.chat_type,
                                   "ok", len(content),
                                   watermark=any_watermark)
            telemetry.record_download(task.chat_type, len(content))

    except Retrying as e:
        logging.warning(f"Could not download video: {e} | url={task.url}")
        if task.track:
            await analytics.record(task.user_id, task.chat_id, task.chat_type,
                                   "fail",
                                   watermark=task.watermark_mode != "none",
                                   url=task.url, reason=str(e))
            telemetry.record_failure(task.chat_type, "download_failed")
        await _reply_error(task,
                           "Не вдалось завантажити це відео "
                           "(можливо приватне чи видалене).")
    except BadRequest as e:
        err = str(e)
        if task.track:
            await analytics.record(task.user_id, task.chat_id, task.chat_type,
                                   "error",
                                   watermark=task.watermark_mode != "none",
                                   url=task.url, reason=err)
            telemetry.record_failure(task.chat_type, "send_failed")
        logging.warning(f"Failed to send video: {err}")
        if "Not enough rights" in err:
            await _reply_error(
                task,
                "I do not have enough rights to send videos or messages "
                "in this chat. Please adjust my permissions.")
        elif "Message to reply not found" in err:
            await _reply_error(
                task,
                "The original message was not found. "
                "Please resend your request.")
        else:
            await _reply_error(
                task,
                "An error occurred while trying to send a video.")
    finally:
        _active -= 1
        if task.status is not None:
            task.status.remaining -= 1
            if task.status.remaining <= 0:
                try:
                    await bot.delete_message(
                        task.status.chat_id,
                        task.status.message_id,
                    )
                except Exception:
                    pass


async def _worker(wid: int):
    logging.info(f"Download worker #{wid} started")
    while True:
        task = await _queue.get()
        try:
            await _process(task)
        except Exception:
            logging.exception(f"Worker #{wid}: unhandled error")
        finally:
            _queue.task_done()


async def start_workers(count: Optional[int] = None):
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    n = count or MAX_CONCURRENT_DOWNLOADS
    for i in range(n):
        asyncio.create_task(_worker(i + 1))
    logging.info(f"Download queue: {n} workers ready")
