# bot/handlers/commands.py

import asyncio
import logging
from aiogram.types import Message
from aiogram.utils.exceptions import BotBlocked, ChatNotFound, UserDeactivated
from bot import bot, dp
from bot import analytics
from settings import ADMIN_ID


@dp.message_handler(commands=["stats"])
async def cmd_stats(message: Message):
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return

    stats = await analytics.get_stats()
    if not stats:
        await message.reply("Статистика поки порожня.")
        return

    recipients = await analytics.get_broadcast_recipients()

    lines = [
        "📊 <b>Статистика бота</b>",
        "",
        f"Всього запитів: <b>{stats['total_requests']}</b>",
        f"  ✅ успішних: {stats['successful']}",
        f"  ❌ невдалих: {stats['failed']}",
        f"Унікальних користувачів: <b>{stats['unique_users']}</b>",
        f"Унікальних чатів: <b>{stats['unique_chats']}</b>",
        f"Завантажено відео: <b>{stats['total_video_mb']} MB</b>",
        f"Відомих юзерів (для broadcast): <b>{len(recipients)}</b>",
    ]

    wm_yes = stats.get("watermark_yes", 0)
    wm_no = stats.get("watermark_no", 0)
    wm_total = wm_yes + wm_no
    if wm_total > 0:
        pct_yes = round(wm_yes * 100 / wm_total, 1)
        pct_no = round(wm_no * 100 / wm_total, 1)
        lines += [
            "",
            "🏷 <b>Ватермарка:</b>",
            f"  ✅ з: {wm_yes} ({pct_yes}%)",
            f"  ❌ без: {wm_no} ({pct_no}%)",
        ]

    if stats.get("daily_last_7d"):
        lines += ["", "📅 <b>Останні 7 днів:</b>"]
        for d in stats["daily_last_7d"]:
            lines.append(f"  {d['date']}  —  {d['requests']} запитів ({d['success']} ✅)")

    if stats.get("top_users"):
        lines += ["", "👤 <b>Топ користувачі:</b>"]
        for i, u in enumerate(stats["top_users"], 1):
            lines.append(
                f"  {i}. <code>{u['anon_id']}</code>  "
                f"— {u['requests']} запитів ({u['success']} ✅)"
            )

    await message.reply("\n".join(lines), parse_mode="HTML")


@dp.message_handler(commands=["broadcast"])
async def cmd_broadcast(message: Message):
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return

    text = message.get_args()
    if not text:
        await message.reply(
            "Використання: <code>/broadcast Ваше повідомлення</code>",
            parse_mode="HTML",
        )
        return

    recipients = await analytics.get_broadcast_recipients()
    if not recipients:
        await message.reply("Немає відомих користувачів для розсилки.")
        return

    await message.reply(
        f"Розсилка <b>{len(recipients)}</b> юзерам (~{len(recipients)} хв)...",
        parse_mode="HTML",
    )

    sent, failed, blocked = 0, 0, 0
    for chat_id in recipients:
        try:
            await bot.send_message(chat_id, text)
            sent += 1
        except (BotBlocked, ChatNotFound, UserDeactivated):
            blocked += 1
        except Exception as e:
            failed += 1
            logging.warning(f"broadcast to {chat_id} failed: {e}")
        await asyncio.sleep(60)

    await message.reply(
        f"Розсилка завершена:\n"
        f"  ✅ доставлено: {sent}\n"
        f"  🚫 заблоковано/видалено: {blocked}\n"
        f"  ❌ помилки: {failed}",
    )
