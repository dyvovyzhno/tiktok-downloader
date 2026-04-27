# bot/handlers/callbacks.py
#
# Inline-button callback handlers (watermark choice + size picker).

import logging

from aiogram.types import CallbackQuery
from aiogram.utils.exceptions import InvalidQueryID

from bot import dp
from bot import analytics
from bot import telemetry
from bot.overlay import (
    DEFAULT_WATERMARK_SIZE,
    WATERMARK_PRESETS,
    WATERMARK_SIZE_LABELS_UA,
)
from bot.queue import (
    WATERMARK_MODES, pop_pending, enqueue, DownloadTask, StatusMessage,
)
from settings import ANALYTICS_EXCLUDE_IDS


_MODE_LABELS_UA = {
    "tt": "ватермарка TikTok",
    "custom": "своя ватермарка",
    "none": "без ватермарки",
}


async def _safe_answer(callback: CallbackQuery, text: str = None):
    """Answer a callback query, ignoring expired/invalid query IDs."""
    try:
        await callback.answer(text)
    except InvalidQueryID:
        pass


@dp.callback_query_handler(lambda cb: cb.data and cb.data.startswith("wm:"))
async def on_watermark_choice(callback: CallbackQuery):
    parts = callback.data.split(":", 2)  # wm : mode : key
    if len(parts) != 3:
        await _safe_answer(callback, "Помилка")
        return

    _, mode, key = parts
    if mode not in WATERMARK_MODES:
        await _safe_answer(callback, "Помилка")
        return

    pt = pop_pending(key)
    if pt is None:
        await _safe_answer(callback, "Час вийшов, надішліть посилання ще раз.")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    n = len(pt.urls)
    wm_label = _MODE_LABELS_UA[mode]

    # Look up the user's saved size; only meaningful for the "custom" mode but
    # a stale or unset value falls back to the default.
    saved_size = await analytics.get_user_watermark_size(pt.user_id)
    if saved_size not in WATERMARK_PRESETS:
        saved_size = DEFAULT_WATERMARK_SIZE

    # Telemetry — collapse the 3-way mode to the existing bool until we
    # decide to track tt-vs-custom separately.
    if pt.user_id not in ANALYTICS_EXCLUDE_IDS:
        any_watermark = mode != "none"
        for _ in range(n):
            telemetry.record_watermark_choice(any_watermark, pt.chat_type)

    # ── instant feedback ──────────────────────────────────────────
    await _safe_answer(callback)
    try:
        status = (
            f"⏳ Завантажую відео ({wm_label})..."
            if n == 1
            else f"⏳ Завантажую {n} відео ({wm_label})..."
        )
        await callback.message.edit_text(status)
    except Exception:
        pass

    # ── queue the download(s) ─────────────────────────────────────
    status_ref = StatusMessage(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        remaining=n,
    )
    for url in pt.urls:
        await enqueue(DownloadTask(
            url=url,
            message=pt.message,
            user_id=pt.user_id,
            chat_id=pt.chat_id,
            chat_type=pt.chat_type,
            track=pt.track,
            watermark_mode=mode,
            watermark_size=saved_size,
            status=status_ref,
        ))


@dp.callback_query_handler(lambda cb: cb.data and cb.data.startswith("wms:"))
async def on_watermark_size_choice(callback: CallbackQuery):
    parts = callback.data.split(":", 1)  # wms : preset
    if len(parts) != 2 or parts[1] not in WATERMARK_PRESETS:
        await _safe_answer(callback, "Помилка")
        return

    size = parts[1]
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is None:
        await _safe_answer(callback, "Помилка")
        return

    await analytics.set_user_watermark_size(user_id, size)
    await _safe_answer(callback, "Збережено")

    try:
        await callback.message.edit_text(
            f"✅ Розмір своєї ватермарки: <b>{WATERMARK_SIZE_LABELS_UA[size]}</b>",
            parse_mode="HTML",
        )
    except Exception:
        logging.debug("Could not edit watermark-size confirmation", exc_info=True)
