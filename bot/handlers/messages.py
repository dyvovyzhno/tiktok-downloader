# bot/handlers/messages.py

import logging
from aiogram.types import Message
from bot import dp
from bot import analytics
from bot.queue import DownloadTask, enqueue, extract_urls, total_ahead
from settings import ANALYTICS_EXCLUDE_IDS


def _user_id(message: Message) -> int:
    """Telegram omits from_user in channel posts."""
    return message.from_user.id if message.from_user else message.chat.id


@dp.message_handler(content_types=["text"])
@dp.channel_post_handler(content_types=["text"])
async def get_message(message: Message):
    uid = _user_id(message)
    if message.chat.type == "private":
        analytics.touch_user(message.chat.id)
    track = uid not in ANALYTICS_EXCLUDE_IDS

    urls = extract_urls(message)
    if not urls:
        return

    # Snapshot queue depth *before* adding our tasks.
    ahead = total_ahead()

    for url in urls:
        await enqueue(DownloadTask(
            url=url,
            message=message,
            user_id=uid,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            track=track,
        ))

    # Instant feedback
    n = len(urls)
    if ahead == 0:
        feedback = "⏳ Завантажую відео..." if n == 1 else f"⏳ Завантажую {n} відео..."
    else:
        pos = ahead + 1
        if n == 1:
            feedback = f"📥 Посилання отримано! В черзі: {pos}"
        else:
            feedback = f"📥 Отримано {n} посилань! В черзі: {pos}"

    try:
        await message.reply(feedback)
    except Exception:
        logging.debug("Could not send queue feedback", exc_info=True)
