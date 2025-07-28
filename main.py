import logging
import os
import asyncio
import aiohttp
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes
from MeeowPlugins_MSharePlugIn import get_track_info, get_current_track_id
from utils import get_cover_url, get_genius_link, get_download_link

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG = {
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
    "CHANNEL_ID": int(os.getenv("CHANNEL_ID")),
    "GENIUS_TOKEN": os.getenv("GENIUS_TOKEN"),
    "YANDEX_TOKEN": os.getenv("YANDEX_TOKEN"),
    "DOWNLOAD_CHANNEL_ID": int(os.getenv("DOWNLOAD_CHANNEL_ID"))
}

class BotState:
    def __init__(self):
        self.current_track_id = None
        self.channel_message_id = None

bot_state = BotState()

async def edit_track_message(bot: Bot, track, message_id: int) -> bool:
    try:
        cover_url = get_cover_url(track)
        genius_link = get_genius_link(CONFIG["GENIUS_TOKEN"], track["artists"], track["title"])

        buttons = [
            [InlineKeyboardButton("📄 Текст песни", url=genius_link)],
            [InlineKeyboardButton("⬇️ Скачать трек", url="https://t.me/text_pesni_aqw")]
        ]

        caption = f"🎵 <b>{track['title']}</b>\n👤 {track['artists']}"

        await bot.edit_message_media(
            chat_id=CONFIG["CHANNEL_ID"],
            message_id=message_id,
            media={'type': 'photo', 'media': cover_url},
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        await bot.edit_message_caption(
            chat_id=CONFIG["CHANNEL_ID"],
            message_id=message_id,
            caption=caption,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return True
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение: {e}")
        return False

async def delete_message(bot: Bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение: {e}")

async def send_new_track_message(bot: Bot, track) -> int:
    cover_url = get_cover_url(track)
    genius_link = get_genius_link(CONFIG["GENIUS_TOKEN"], track["artists"], track["title"])

    buttons = [
        [InlineKeyboardButton("📄 Текст песни", url=genius_link)],
        [InlineKeyboardButton("⬇️ Скачать трек", url="https://t.me/text_pesni_aqw")]
    ]

    caption = f"🎵 <b>{track['title']}</b>\n👤 {track['artists']}"

    msg = await bot.send_photo(
        chat_id=CONFIG["CHANNEL_ID"],
        photo=cover_url,
        caption=caption,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return msg.message_id

async def track_checker(bot: Bot):
    while True:
        try:
            current_id = await get_current_track_id(CONFIG["YANDEX_TOKEN"])
            if current_id != bot_state.current_track_id:
                track = await get_track_info(CONFIG["YANDEX_TOKEN"], current_id)
                bot_state.current_track_id = current_id

                if not await edit_track_message(bot, track, bot_state.channel_message_id):
                    await delete_message(bot, CONFIG["CHANNEL_ID"], bot_state.channel_message_id)
                    bot_state.channel_message_id = await send_new_track_message(bot, track)

                # Изменить название канала
                try:
                    new_title = f"{track['artists']} — {track['title']}"
                    await bot.set_chat_title(chat_id=CONFIG["CHANNEL_ID"], title=new_title)
                except Exception as e:
                    logger.error(f"Ошибка при изменении названия канала: {e}")

        except Exception as e:
            logger.error(f"Ошибка в track_checker: {e}")

        await asyncio.sleep(10)

def main():
    application = Application.builder().token(CONFIG["TELEGRAM_BOT_TOKEN"]).build()
    bot = application.bot
    application.job_queue.run_once(lambda ctx: asyncio.create_task(track_checker(bot)), 0)
    application.run_polling()

# Flask-заглушка для Render
from flask import Flask
import threading

app_flask = Flask(__name__)

@app_flask.route('/')
def index():
    return 'Bot is running.'

def run_flask():
    app_flask.run(host='0.0.0.0', port=10000)

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    main()
