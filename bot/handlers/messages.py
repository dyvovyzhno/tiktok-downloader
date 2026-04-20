# bot/handlers/messages.py

import asyncio
import logging
from aiogram.utils.exceptions import RetryAfter, BadRequest
from aiogram.types import Message
from bot import bot, dp
from bot.api.tiktok import TikTokAPI, Retrying
from bot.overlay import add_author_overlay
from bot import analytics

TikTok = TikTokAPI(
    headers={
        "Referer": "https://www.tiktok.com/",
    }
)


def _user_id(message: Message) -> int:
    """Telegram omits from_user in channel posts."""
    return message.from_user.id if message.from_user else message.chat.id


@dp.message_handler(content_types=["text"])
@dp.channel_post_handler(content_types=["text"])
async def get_message(message: Message):
    uid = _user_id(message)
    try:
        async for video in TikTok.handle_message(message):
            if not video or not video.content:
                continue
            content = await add_author_overlay(video.content, video.author)
            await bot.send_video(
                message.chat.id,
                content,
                reply_to_message_id=message.message_id,
            )
            analytics.record(uid, message.chat.id, message.chat.type,
                             "ok", len(content))
    except Retrying as e:
        logging.warning(f"Could not download video: {e}")
        analytics.record(uid, message.chat.id, message.chat.type, "fail")
        try:
            await message.reply("Не вдалось завантажити це відео (можливо приватне чи видалене).")
        except BadRequest:
            pass
    except RetryAfter as e:
        wait_time = int(e.timeout)
        print(f"Rate limit hit. Waiting for {wait_time} seconds before retrying...")
        await asyncio.sleep(wait_time)
        await get_message(message)
    except BadRequest as e:
        error_message = str(e)
        analytics.record(uid, message.chat.id, message.chat.type, "error")
        print(f"Failed to send video due to: {error_message}")
        if "Not enough rights" in error_message:
            try:
                await message.reply("I do not have enough rights to send videos or messages in this chat. Please adjust my permissions.")
            except BadRequest:
                print("Could not notify the user due to insufficient permissions.")
        elif "Message to reply not found" in error_message:
            try:
                await message.reply("The original message was not found. Please resend your request.")
            except BadRequest:
                print("Failed to send the error message to the user.")
        else:
            try:
                await message.reply("An error occurred while trying to send a video.")
            except BadRequest:
                print("Failed to send the error message to the user.")
