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
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from aiogram.types import Message
from aiogram.utils.exceptions import RetryAfter, BadRequest

from bot import bot
from bot.api.tiktok import TikTokAPI, Retrying
from bot.overlay import add_author_overlay
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
class DownloadTask:
    url: str
    message: Message
    user_id: int
    chat_id: int
    chat_type: str
    track: bool
    with_watermark: bool = True


_queue: asyncio.Queue = asyncio.Queue()
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

async def send_video(task: DownloadTask, content: bytes):
    try:
        await bot.send_video(
            task.chat_id,
            content,
            reply_to_message_id=task.message.message_id,
        )
    except RetryAfter as e:
        logging.warning(f"Telegram rate limit, waiting {e.timeout}s")
        await asyncio.sleep(int(e.timeout))
        await bot.send_video(
            task.chat_id,
            content,
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

        video = await _tiktok.download_video(task.url)
        if not video or not video.content:
            return

        if task.with_watermark and video.author:
            content = await add_author_overlay(video.content, video.author)
        else:
            content = video.content

        await send_video(task, content)
        if task.track:
            analytics.record(task.user_id, task.chat_id, task.chat_type,
                             "ok", len(content))
            telemetry.record_download(task.chat_type, len(content))

    except Retrying as e:
        logging.warning(f"Could not download video: {e}")
        if task.track:
            analytics.record(task.user_id, task.chat_id, task.chat_type, "fail")
            telemetry.record_failure(task.chat_type, "download_failed")
        await _reply_error(task,
                           "Не вдалось завантажити це відео "
                           "(можливо приватне чи видалене).")
    except BadRequest as e:
        err = str(e)
        if task.track:
            analytics.record(task.user_id, task.chat_id, task.chat_type, "error")
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
    n = count or MAX_CONCURRENT_DOWNLOADS
    for i in range(n):
        asyncio.create_task(_worker(i + 1))
    logging.info(f"Download queue: {n} workers ready")
