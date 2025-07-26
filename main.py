import asyncio
import logging
import os
import tempfile
from urllib.parse import quote
from datetime import datetime
import pytz
import aiohttp
import lyricsgenius
import requests
from dotenv import load_dotenv
from telegram import (
    Bot,
    InputMediaPhoto,
    InputMediaAudio,
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
    "DOWNLOAD_CHANNEL_ID": int(os.getenv("DOWNLOAD_CHANNEL_ID")),  # ID канала с треками
    "GENIUS_TOKEN": os.getenv("GENIUS_TOKEN"),
}

# Московский часовой пояс
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

class BotState:
    def __init__(self):
        self.last_track_id = None
        self.channel_message_id = None
        self.download_message_id = None
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

def get_moscow_time():
    """Возвращает текущее время по Москве в формате HH:MM"""
    return datetime.now(MOSCOW_TZ).strftime("%H:%M")

def get_bot_keyboard():
    """Клавиатура для управления ботом"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Запустить трекер", callback_data="start_tracker"),
            InlineKeyboardButton("⏹️ Остановить трекер", callback_data="stop_tracker")
        ],
        [
            InlineKeyboardButton("🔄 Обновить статус", callback_data="refresh_status")
        ]
    ])

def get_channel_keyboard(track: dict):
    """Клавиатура для основного канала"""
    # Формируем ссылку на канал с треками
    channel_link = "https://t.me/text_pesni_aqw"
    if bot_state.download_message_id:
        channel_link += str(bot_state.download_message_id)
    
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 Я.Музыка", url=track["yandex_link"]),
            InlineKeyboardButton("🌐 Другие сервисы", url=track["multi_link"])
        ],
        [
            InlineKeyboardButton("📝 Текст песни", url=track["genius_link"]),
            InlineKeyboardButton("⬇️ Скачать трек", url=channel_link)
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
            "time": get_moscow_time(),
            "yandex_link": f"https://music.yandex.ru/track/{track_id}",
            "multi_link": generate_multi_service_link(track_id),
            "img": track.get("img"),
            "genius_link": get_genius_song_url(title, artists),
            "download_url": track.get("download_link"),
        }
    except Exception as e:
        logger.error(f"Ошибка получения трека: {e}")
        return None

async def send_new_track_message(bot: Bot, track: dict) -> int:
    try:
        caption = f"{track['time']} - {track['title']} — {track['artists']}"
        msg = await bot.send_photo(
            chat_id=CONFIG["CHANNEL_ID"],
            photo=track["img"],
            caption=caption,
            reply_markup=get_channel_keyboard(track)
        )
        return msg.message_id
    except Exception as e:
        logger.error(f"Ошибка отправки трека: {e}")
        return None

async def edit_track_message(bot: Bot, track: dict, msg_id: int) -> bool:
    try:
        caption = f"{track['time']} - {track['title']} — {track['artists']}"
        await bot.edit_message_media(
            chat_id=CONFIG["CHANNEL_ID"],
            message_id=msg_id,
            media=InputMediaPhoto(
                media=track["img"],
                caption=caption,
            ),
            reply_markup=get_channel_keyboard(track)
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления трека: {e}")
        return False

async def send_new_download_message(bot: Bot, track: dict) -> int:
    if not track.get("download_url"):
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(track["download_url"]) as resp:
                if resp.status != 200:
                    return None

                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                    tmp_file.write(await resp.read())
                    tmp_path = tmp_file.name

        msg = await bot.send_audio(
            chat_id=CONFIG["DOWNLOAD_CHANNEL_ID"],
            audio=open(tmp_path, "rb"),
            title=track["title"],
            performer=track["artists"],
            caption=f"🎵 {track['title']} — {track['artists']}"
        )
        os.unlink(tmp_path)
        return msg.message_id
    except Exception as e:
        logger.error(f"Ошибка отправки трека: {e}")
        return None

async def update_download_message(bot: Bot, track: dict, msg_id: int) -> bool:
    if not track.get("download_url"):
        return False

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(track["download_url"]) as resp:
                if resp.status != 200:
                    return False

                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                    tmp_file.write(await resp.read())
                    tmp_path = tmp_file.name

        await bot.edit_message_media(
            chat_id=CONFIG["DOWNLOAD_CHANNEL_ID"],
            message_id=msg_id,
            media=InputMediaAudio(
                media=open(tmp_path, "rb"),
                title=track["title"],
                performer=track["artists"]
            ),
            caption=f"🎵 {track['title']} — {track['artists']}"
        )
        os.unlink(tmp_path)
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления трека: {e}")
        return False

async def track_checker(bot: Bot):
    while bot_state.bot_active:
        track = get_current_track()
        if track:
            if bot_state.channel_message_id and track["id"] != bot_state.last_track_id:
                if bot_state.download_message_id:
                    success = await update_download_message(bot, track, bot_state.download_message_id)
                    if not success:
                        await delete_message(bot, CONFIG["DOWNLOAD_CHANNEL_ID"], bot_state.download_message_id)
                        bot_state.download_message_id = await send_new_download_message(bot, track)
                else:
                    bot_state.download_message_id = await send_new_download_message(bot, track)
                
                if not await edit_track_message(bot, track, bot_state.channel_message_id):
                    await delete_message(bot, CONFIG["CHANNEL_ID"], bot_state.channel_message_id)
                    bot_state.channel_message_id = await send_new_track_message(bot, track)
                
                bot_state.last_track_id = track["id"]
                
            elif not bot_state.channel_message_id:
                bot_state.download_message_id = await send_new_download_message(bot, track)
                bot_state.channel_message_id = await send_new_track_message(bot, track)
                bot_state.last_track_id = track["id"]
                
        await asyncio.sleep(5)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    bot = context.bot
    chat_id = query.message.chat.id

    if query.data == "start_tracker":
        if not bot_state.bot_active:
            bot_state.bot_active = True
            bot_state.channel_message_id = None
            bot_state.download_message_id = None
            asyncio.create_task(track_checker(bot))
            await update_status_message(bot, chat_id, "🟢 Трекер запущен!")
    
    elif query.data == "stop_tracker":
        if bot_state.bot_active:
            bot_state.bot_active = False
            if bot_state.channel_message_id:
                await delete_message(bot, CONFIG["CHANNEL_ID"], bot_state.channel_message_id)
                bot_state.channel_message_id = None
            if bot_state.download_message_id:
                await delete_message(bot, CONFIG["DOWNLOAD_CHANNEL_ID"], bot_state.download_message_id)
                bot_state.download_message_id = None
            await update_status_message(bot, chat_id, "⏹️ Трекер остановлен")
    
    elif query.data == "refresh_status":
        status = "🟢 Активен" if bot_state.bot_active else "🔴 Остановлен"
        await update_status_message(bot, chat_id, f"{status}\nУправление:")

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
                reply_markup=get_bot_keyboard(),
            )
        else:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=get_bot_keyboard(),
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
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения: {e}")

    msg = await update.message.reply_text(
        "🎵 Музыкальный трекер\nУправление:",
        reply_markup=get_bot_keyboard()
    )
    bot_state.bot_status_message_id = msg.message_id

def main():
    # Проверяем наличие pytz
    try:
        import pytz
    except ImportError:
        logger.error("Требуется установить pytz: pip install pytz")
        return

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
