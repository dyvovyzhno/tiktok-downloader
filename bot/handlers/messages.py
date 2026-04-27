# bot/handlers/messages.py

import logging
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot import dp
from bot import analytics
from bot.overlay import (
    DEFAULT_WATERMARK_SIZE,
    WATERMARK_PRESETS,
    WATERMARK_SIZE_LABELS_SHORT,
)
from bot.queue import extract_urls, store_pending
from settings import ANALYTICS_EXCLUDE_IDS


def _user_id(message: Message) -> int:
    """Telegram omits from_user in channel posts."""
    return message.from_user.id if message.from_user else message.chat.id


def _watermark_keyboard(key: str, size: str) -> InlineKeyboardMarkup:
    """Three-way watermark picker. The custom-overlay button is suffixed with
    the user's saved size (T/S/M/L/XL) — set via /watermark_size."""
    short = WATERMARK_SIZE_LABELS_SHORT.get(size, WATERMARK_SIZE_LABELS_SHORT[DEFAULT_WATERMARK_SIZE])
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🎵 Ватермарка TikTok", callback_data=f"wm:tt:{key}"),
        InlineKeyboardButton(f"✏️ Своя ({short})", callback_data=f"wm:custom:{key}"),
        InlineKeyboardButton("❌ Без ватермарки", callback_data=f"wm:none:{key}"),
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

    saved_size = await analytics.get_user_watermark_size(uid)
    if saved_size not in WATERMARK_PRESETS:
        saved_size = DEFAULT_WATERMARK_SIZE

    try:
        await message.reply(
            "Оберіть тип ватермарки:",
            reply_markup=_watermark_keyboard(key, saved_size),
        )
    except Exception:
        logging.debug("Could not send watermark question", exc_info=True)
