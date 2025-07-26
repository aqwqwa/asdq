import asyncio
import logging
import os
import tempfile
from urllib.parse import quote

import aiohttp
import lyricsgenius
import requests
from dotenv import load_dotenv
from telegram import (
    Bot,
    InputMediaPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from telegram.error import BadRequest, TelegramError
from unidecode import unidecode

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

# Конфигурация
CONFIG = {
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
    "YANDEX_TOKEN": os.getenv("YANDEX_TOKEN"),
    "CHANNEL_ID": os.getenv("CHANNEL_ID"),
    "DOWNLOAD_CHANNEL_ID": int(os.getenv("DOWNLOAD_CHANNEL_ID")),
    "GENIUS_TOKEN": os.getenv("GENIUS_TOKEN"),
}

class BotState:
    def __init__(self):
        self.last_track_id = None
        self.channel_message_id = None
        self.bot_active = False
        self.bot_status_message_id = None
        self.genius = self._init_genius()

    def _init_genius(self):
        if CONFIG["GENIUS_TOKEN"]:
            try:
                genius = lyricsgenius.Genius(
                    CONFIG["GENIUS_TOKEN"],
                    timeout=15,
                    remove_section_headers=True,
                    skip_non_songs=True,
                    excluded_terms=["(Remix)", "(Live)"],
                )
                genius.verbose = False
                return genius
            except Exception as e:
                logger.error(f"Ошибка инициализации Genius: {e}")
        return None

bot_state = BotState()

def get_bot_keyboard():
    """Клавиатура для управления ботом"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Запустить трекер", callback_data="start_tracker"),
            InlineKeyboardButton("⏹️ Остановить трекер", callback_data="stop_tracker")
        ],
        [
            InlineKeyboardButton("🔄 Обновить статус", callback_data="refresh_status"),
            InlineKeyboardButton("🎧 Скачать трек", callback_data="download_track")
        ]
    ])

def get_channel_keyboard(track: dict):
    """Клавиатура для постов в канале"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 Я.Музыка", url=track["yandex_link"]),
            InlineKeyboardButton("🌐 Другие сервисы", url=track["multi_link"])
        ],
        [
            InlineKeyboardButton("📝 Текст песни", url=track["genius_link"])
        ]
    ])

def generate_multi_service_link(track_id: str) -> str:
    return f"https://song.link/ya/{track_id}"

def get_genius_song_url(title: str, artist: str) -> str:
    if not bot_state.genius:
        return f"https://genius.com/search?q={quote(f'{artist} {title}')}"
    
    try:
        clean_title = unidecode(title.split("(")[0].split("-")[0].strip())
        clean_artist = unidecode(artist.split(",")[0].split("&")[0].strip())
        song = bot_state.genius.search_song(clean_title, clean_artist)
        return song.url if song and song.url else f"https://genius.com/search?q={quote(f'{clean_artist} {clean_title}')}"
    except Exception as e:
        logger.error(f"Ошибка Genius: {e}")
        return f"https://genius.com/search?q={quote(f'{artist} {title}')}"

async def send_new_track_message(bot: Bot, track: dict) -> int:
    try:
        msg = await bot.send_photo(
            chat_id=CONFIG["CHANNEL_ID"],
            photo=track["img"],
            caption=f"{track['title']} — {track['artists']}",
            reply_markup=get_channel_keyboard(track)  # Используем клавиатуру для канала
        )
        return msg.message_id
    except Exception as e:
        logger.error(f"Ошибка отправки трека: {e}")
        return None

async def edit_track_message(bot: Bot, track: dict, msg_id: int) -> bool:
    try:
        await bot.edit_message_media(
            chat_id=CONFIG["CHANNEL_ID"],
            message_id=msg_id,
            media=InputMediaPhoto(
                media=track["img"],
                caption=f"{track['title']} — {track['artists']}",
            ),
            reply_markup=get_channel_keyboard(track)  # Используем клавиатуру для канала
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления трека: {e}")
        return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if bot_state.bot_status_message_id:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=bot_state.bot_status_message_id,
            )
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения: {e}")

    msg = await update.message.reply_text(
        "🎵 Музыкальный трекер\nУправление:",
        reply_markup=get_bot_keyboard()  # Используем клавиатуру для бота
    )
    bot_state.bot_status_message_id = msg.message_id

# ... (остальные функции остаются без изменений, как в предыдущем коде)

def main():
    required_vars = ["TELEGRAM_BOT_TOKEN", "YANDEX_TOKEN", "CHANNEL_ID", "DOWNLOAD_CHANNEL_ID"]
    if missing := [var for var in required_vars if not CONFIG.get(var)]:
        logger.error(f"Отсутствуют переменные: {', '.join(missing)}")
        return

    app = Application.builder().token(CONFIG["TELEGRAM_BOT_TOKEN"]).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
