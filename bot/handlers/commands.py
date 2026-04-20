# bot/handlers/commands.py

from aiogram.types import Message
from bot import dp
from bot import analytics
from settings import ADMIN_ID


@dp.message_handler(commands=["stats"])
async def cmd_stats(message: Message):
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return

    stats = analytics.get_stats()
    if not stats:
        await message.reply("Статистика поки порожня.")
        return

    lines = [
        "📊 <b>Статистика бота</b>",
        "",
        f"Всього запитів: <b>{stats['total_requests']}</b>",
        f"  ✅ успішних: {stats['successful']}",
        f"  ❌ невдалих: {stats['failed']}",
        f"Унікальних користувачів: <b>{stats['unique_users']}</b>",
        f"Унікальних чатів: <b>{stats['unique_chats']}</b>",
        f"Завантажено відео: <b>{stats['total_video_mb']} MB</b>",
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
