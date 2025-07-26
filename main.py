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

# Глобальное состояние
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

def get_inline_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("▶️ Запустить трекер", callback_data="start_tracker"),
                InlineKeyboardButton("⏹️ Остановить трекер", callback_data="stop_tracker"),
            ],
            [
                InlineKeyboardButton("🔄 Обновить статус", callback_data="refresh_status"),
                InlineKeyboardButton("🎧 Скачать трек", callback_data="download_track"),
            ],
        ]
    )

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

def get_current_track():
    try:
        headers = {"ya-token": CONFIG["YANDEX_TOKEN"], "User-Agent": "Mozilla/5.0"}
        response = requests.get(
            "https://track.mipoh.ru/get_current_track_beta",
            headers=headers,
            timeout=10,
            verify=False,
        )
        
        if response.status_code != 200:
            logger.warning(f"API статус {response.status_code}")
            return None

        data = response.json()
        if not data.get("track"):
            return None

        track = data["track"]
        track_id = track.get("track_id")
        if not track_id:
            return None

        artists = (
            ", ".join(track["artist"])
            if isinstance(track.get("artist"), list)
            else track.get("artist", "")
        )
        title = track.get("title", "")

        return {
            "id": track_id,
            "title": title,
            "artists": artists,
            "yandex_link": f"https://music.yandex.ru/track/{track_id}",
            "multi_link": generate_multi_service_link(track_id),
            "img": track.get("img"),
            "genius_link": get_genius_song_url(title, artists),
            "download_url": track.get("download_link"),
        }
    except Exception as e:
        logger.error(f"Ошибка получения трека: {e}")
        return None

async def download_and_send_track(bot: Bot, chat_id: int, track: dict):
    if not track.get("download_url"):
        await bot.send_message(chat_id, "❌ Ссылка для скачивания недоступна")
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(track["download_url"]) as response:
                if response.status != 200:
                    await bot.send_message(chat_id, "❌ Ошибка загрузки трека")
                    return

                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                    tmp_file.write(await response.read())
                    tmp_path = tmp_file.name

        await bot.send_audio(
            chat_id=chat_id,
            audio=open(tmp_path, "rb"),
            title=track["title"],
            performer=track["artists"],
            caption=f"{track['title']} — {track['artists']}",
        )
        os.unlink(tmp_path)
    except Exception as e:
        logger.error(f"Ошибка отправки трека: {e}")
        await bot.send_message(chat_id, "❌ Ошибка при отправке файла")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    bot = context.bot
    chat_id = query.message.chat.id

    if query.data == "start_tracker":
        if not bot_state.bot_active:
            bot_state.bot_active = True
            bot_state.channel_message_id = None
            asyncio.create_task(track_checker(bot))
            await update_status_message(bot, chat_id, "🟢 Трекер запущен!")
    
    elif query.data == "stop_tracker":
        if bot_state.bot_active:
            bot_state.bot_active = False
            if bot_state.channel_message_id:
                await delete_message(bot, CONFIG["CHANNEL_ID"], bot_state.channel_message_id)
                bot_state.channel_message_id = None
            await update_status_message(bot, chat_id, "⏹️ Трекер остановлен")
    
    elif query.data == "refresh_status":
        status = "🟢 Активен" if bot_state.bot_active else "🔴 Остановлен"
        await update_status_message(bot, chat_id, f"{status}\nУправление:")
    
    elif query.data == "download_track":
        track = get_current_track()
        if track:
            await download_and_send_track(bot, CONFIG["DOWNLOAD_CHANNEL_ID"], track)
            await query.message.reply_text("✅ Трек отправлен в канал!")
        else:
            await query.message.reply_text("❌ Трек не найден")

async def delete_message(bot: Bot, chat_id: int, msg_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except (BadRequest, TelegramError) as e:
        logger.error(f"Ошибка удаления сообщения: {e}")

async def update_status_message(bot: Bot, chat_id: int, text: str):
    try:
        if bot_state.bot_status_message_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=bot_state.bot_status_message_id,
                text=text,
                reply_markup=get_inline_keyboard(),
            )
        else:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=get_inline_keyboard(),
            )
            bot_state.bot_status_message_id = msg.message_id
    except (BadRequest, TelegramError) as e:
        logger.error(f"Ошибка обновления статуса: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if bot_state.bot_status_message_id:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=bot_state.bot_status_message_id,
            )
        except (BadRequest, TelegramError) as e:
            logger.error(f"Ошибка удаления сообщения: {e}")

    msg = await update.message.reply_text(
        "🎵 Музыкальный трекер\nУправление:",
        reply_markup=get_inline_keyboard(),
    )
    bot_state.bot_status_message_id = msg.message_id

async def send_new_track_message(bot: Bot, track: dict) -> int:
    try:
        msg = await bot.send_photo(
            chat_id=CONFIG["CHANNEL_ID"],
            photo=track["img"],
            caption=f"{track['title']} — {track['artists']}",
            reply_markup=get_inline_keyboard(),
        )
        return msg.message_id
    except (BadRequest, TelegramError) as e:
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
            reply_markup=get_inline_keyboard(),
        )
        return True
    except (BadRequest, TelegramError) as e:
        logger.error(f"Ошибка обновления трека: {e}")
        return False

async def track_checker(bot: Bot):
    while bot_state.bot_active:
        track = get_current_track()
        if track:
            if bot_state.channel_message_id and track["id"] != bot_state.last_track_id:
                if not await edit_track_message(bot, track, bot_state.channel_message_id):
                    bot_state.channel_message_id = await send_new_track_message(bot, track)
                bot_state.last_track_id = track["id"]
            elif not bot_state.channel_message_id:
                bot_state.channel_message_id = await send_new_track_message(bot, track)
                bot_state.last_track_id = track["id"]
        await asyncio.sleep(5)

def main():
    # Проверка конфигурации
    required_vars = ["TELEGRAM_BOT_TOKEN", "YANDEX_TOKEN", "CHANNEL_ID", "DOWNLOAD_CHANNEL_ID"]
    if missing := [var for var in required_vars if not CONFIG.get(var)]:
        logger.error(f"Отсутствуют переменные: {', '.join(missing)}")
        return

    # Инициализация бота
    app = Application.builder().token(CONFIG["TELEGRAM_BOT_TOKEN"]).build()
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
