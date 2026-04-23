# bot/handlers/messages.py

import logging
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot import dp
from bot import analytics
from bot.queue import extract_urls, store_pending
from settings import ANALYTICS_EXCLUDE_IDS


def _user_id(message: Message) -> int:
    """Telegram omits from_user in channel posts."""
    return message.from_user.id if message.from_user else message.chat.id


def _watermark_keyboard(key: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ З ватермаркою", callback_data=f"wm:y:{key}"),
        InlineKeyboardButton("❌ Без", callback_data=f"wm:n:{key}"),
    )
    return kb


@dp.message_handler(content_types=["text"])
@dp.channel_post_handler(content_types=["text"])
async def get_message(message: Message):
    uid = _user_id(message)
    logging.info(f"msg from {uid} ({message.chat.type}): {(message.text or '')[:80]!r}")
    if message.chat.type == "private":
        await analytics.touch_user(message.chat.id)
    track = uid not in ANALYTICS_EXCLUDE_IDS

    urls = extract_urls(message)
    if not urls:
        return

    key = store_pending(
        urls=urls,
        message=message,
        user_id=uid,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        track=track,
    )

    try:
        await message.reply(
            "Додати ватермарку автора?",
            reply_markup=_watermark_keyboard(key),
        )
    except Exception:
        logging.debug("Could not send watermark question", exc_info=True)
