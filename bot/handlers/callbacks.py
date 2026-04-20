# bot/handlers/callbacks.py
#
# Inline-button callback handlers (watermark choice).

import logging

from aiogram.types import CallbackQuery
from aiogram.utils.exceptions import InvalidQueryID

from bot import dp
from bot.queue import (
    pop_pending, enqueue, DownloadTask,
)


async def _safe_answer(callback: CallbackQuery, text: str = None):
    """Answer a callback query, ignoring expired/invalid query IDs."""
    try:
        await callback.answer(text)
    except InvalidQueryID:
        pass


@dp.callback_query_handler(lambda cb: cb.data and cb.data.startswith("wm:"))
async def on_watermark_choice(callback: CallbackQuery):
    parts = callback.data.split(":", 2)  # wm : y/n : key
    if len(parts) != 3:
        await _safe_answer(callback, "Помилка")
        return

    _, choice, key = parts
    pt = pop_pending(key)

    if pt is None:
        await _safe_answer(callback, "Час вийшов, надішліть посилання ще раз.")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    with_watermark = choice == "y"
    wm_label = "з ватермаркою" if with_watermark else "без ватермарки"
    n = len(pt.urls)

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
    for url in pt.urls:
        await enqueue(DownloadTask(
            url=url,
            message=pt.message,
            user_id=pt.user_id,
            chat_id=pt.chat_id,
            chat_type=pt.chat_type,
            track=pt.track,
            with_watermark=with_watermark,
        ))
