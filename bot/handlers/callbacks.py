# bot/handlers/callbacks.py
#
# Inline-button callback handlers (watermark choice).

import logging

from aiogram.types import CallbackQuery

from bot import dp
from bot.queue import (
    pop_pending, enqueue, DownloadTask, total_ahead,
)


@dp.callback_query_handler(lambda cb: cb.data and cb.data.startswith("wm:"))
async def on_watermark_choice(callback: CallbackQuery):
    parts = callback.data.split(":", 2)  # wm : y/n : key
    if len(parts) != 3:
        await callback.answer("Помилка")
        return

    _, choice, key = parts
    pt = pop_pending(key)

    if pt is None:
        await callback.answer("Час вийшов, надішліть посилання ще раз.")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    with_watermark = choice == "y"

    # Queue each URL from the original message
    ahead = 0
    for url in pt.urls:
        ahead = await enqueue(DownloadTask(
            url=url,
            message=pt.message,
            user_id=pt.user_id,
            chat_id=pt.chat_id,
            chat_type=pt.chat_type,
            track=pt.track,
            with_watermark=with_watermark,
        ))

    # Update the question message with queue status
    n = len(pt.urls)
    wm_label = "з ватермаркою" if with_watermark else "без ватермарки"
    if ahead == 0:
        if n == 1:
            status = f"⏳ Завантажую відео ({wm_label})..."
        else:
            status = f"⏳ Завантажую {n} відео ({wm_label})..."
    else:
        pos = ahead + 1
        if n == 1:
            status = f"📥 В черзі: {pos} ({wm_label})"
        else:
            status = f"📥 {n} відео в черзі: {pos} ({wm_label})"

    try:
        await callback.message.edit_text(status)
    except Exception:
        pass

    await callback.answer()
