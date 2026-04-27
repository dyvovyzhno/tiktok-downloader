# bot/handlers/commands.py

import asyncio
import logging
from datetime import datetime
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.exceptions import BotBlocked, ChatNotFound, UserDeactivated
from bot import bot, dp
from bot import analytics
from bot.filters import content_filter
from bot.overlay import (
    DEFAULT_WATERMARK_SIZE,
    WATERMARK_PRESETS,
    WATERMARK_SIZE_LABELS_SHORT,
    WATERMARK_SIZE_LABELS_UA,
)
from settings import ADMIN_ID


def _watermark_size_keyboard(current: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=1)
    for key in WATERMARK_PRESETS:
        prefix = "✓ " if key == current else ""
        kb.add(InlineKeyboardButton(
            f"{prefix}{WATERMARK_SIZE_LABELS_UA[key]} ({WATERMARK_SIZE_LABELS_SHORT[key]})",
            callback_data=f"wms:{key}",
        ))
    return kb


@dp.message_handler(commands=["watermark_size"])
async def cmd_watermark_size(message: Message):
    """Let the user pick the size used for the custom @author overlay."""
    uid = message.from_user.id if message.from_user else message.chat.id
    saved = await analytics.get_user_watermark_size(uid)
    if saved not in WATERMARK_PRESETS:
        saved = DEFAULT_WATERMARK_SIZE

    await message.reply(
        "Оберіть розмір своєї ватермарки.\n"
        f"Поточний: <b>{WATERMARK_SIZE_LABELS_UA[saved]}</b>",
        parse_mode="HTML",
        reply_markup=_watermark_size_keyboard(saved),
    )


@dp.message_handler(commands=["stats"])
async def cmd_stats(message: Message):
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return

    stats = await analytics.get_stats()
    if not stats:
        await message.reply("Статистика поки порожня.")
        return

    recipients = await analytics.get_broadcast_recipients()

    total = stats['total_requests']
    shadow = stats.get('shadow_blocks', 0) or 0
    shadow_pct = round(shadow * 100 / total, 1) if total else 0.0

    lines = [
        "📊 <b>Статистика бота</b>",
        "",
        f"Всього запитів: <b>{total}</b>",
        f"  ✅ успішних: {stats['successful']}",
        f"  ❌ невдалих: {stats['failed']}",
        f"  🛡 shadow blocked: {shadow} ({shadow_pct}%)",
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


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


@dp.message_handler(commands=["debug"])
async def cmd_debug(message: Message):
    """Show the last N failed downloads (admin only)."""
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return

    # /debug              → last 10
    # /debug 20           → last 20
    args = message.get_args().strip()
    try:
        limit = min(int(args), 50) if args else 10
    except ValueError:
        limit = 10

    failures = await analytics.get_recent_failures(limit)
    if not failures:
        await message.reply("Немає зафіксованих помилок.")
        return

    lines = [f"🐞 <b>Останні {len(failures)} помилок:</b>", ""]
    for i, f in enumerate(failures, 1):
        ts = f.get("ts")
        when = (datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
                if ts else "?")
        status = f.get("status", "?")
        url = f.get("url") or "—"
        reason = f.get("reason") or "—"
        chat_type = f.get("chat_type", "?")
        lines.append(
            f"<b>{i}.</b> [{when}] <code>{status}</code> · {chat_type}\n"
            f"  URL: <code>{_html_escape(url)}</code>\n"
            f"  Причина: <i>{_html_escape(reason)}</i>"
        )

    await message.reply("\n".join(lines), parse_mode="HTML",
                        disable_web_page_preview=True)


@dp.message_handler(commands=["reload_filter"])
async def cmd_reload_filter(message: Message):
    """Re-pull shadow-filter rules from Supabase (admin only)."""
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        return
    await content_filter.reload()
    await message.reply("🛡 Filter rules reloaded from Supabase.")


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
