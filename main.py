import asyncio
import requests
import os
import logging
from urllib.parse import quote, unquote
from telegram import (
    Bot, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton, Update
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler
)
from telegram.error import BadRequest, TelegramError
import lyricsgenius
from unidecode import unidecode
from dotenv import load_dotenv
import aiohttp
import tempfile

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
YANDEX_TOKEN = os.getenv("YANDEX_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
DOWNLOAD_CHANNEL_ID = int(os.getenv("DOWNLOAD_CHANNEL_ID"))
GENIUS_TOKEN = os.getenv("GENIUS_TOKEN")

last_track_id = None
channel_message_id = None
bot_active = False
bot_status_message_id = None

genius = None
if GENIUS_TOKEN:
    try:
        genius = lyricsgenius.Genius(
            GENIUS_TOKEN,
            timeout=15,
            remove_section_headers=True,
            skip_non_songs=True,
            excluded_terms=["(Remix)", "(Live)"]
        )
        genius.verbose = False
    except Exception as e:
        logger.error(f"Ошибка инициализации Genius API: {e}")

def get_inline_keyboard():
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

def generate_multi_service_link(track_id: str) -> str:
    return f"https://song.link/ya/{track_id}"

def get_genius_song_url(title: str, artist: str) -> str:
    if not genius:
        return f"https://genius.com/search?q={quote(f'{artist} {title}')}"
    try:
        clean_title = unidecode(title.split('(')[0].split('-')[0].strip())
        clean_artist = unidecode(artist.split(',')[0].split('&')[0].strip())
        song = genius.search_song(clean_title, clean_artist)
        if song and song.url:
            return song.url
        return f"https://genius.com/search?q={quote(f'{clean_artist} {clean_title}')}"
    except Exception as e:
        logger.error(f"Ошибка поиска на Genius: {e}")
        return f"https://genius.com/search?q={quote(f'{artist} {title}')}"
def get_current_track():
    try:
        headers = {
            "ya-token": YANDEX_TOKEN,
            "User-Agent": "Mozilla/5.0"
        }
        r = requests.get(
            "https://track.mipoh.ru/get_current_track_beta",
            headers=headers,
            timeout=10,
            verify=False
        )
        if r.status_code != 200:
            logger.warning(f"API статус {r.status_code}")
            return None
        data = r.json()
        if not data.get("track"):
            return None
        t = data["track"]
        track_id = t.get("track_id")
        if not track_id:
            return None
        artists = ", ".join(t["artist"]) if isinstance(t.get("artist"), list) else t.get("artist", "")
        title = t.get("title", "")
        return {
            "id": track_id,
            "title": title,
            "artists": artists,
            "yandex_link": f"https://music.yandex.ru/track/{track_id}",
            "multi_link": generate_multi_service_link(track_id),
            "img": t.get("img"),
            "genius_link": get_genius_song_url(title, artists),
            "download_url": t.get("download_link")
        }
    except Exception as e:
        logger.error(f"Ошибка получения трека: {e}")
        return None

async def download_and_send_track(bot: Bot, chat_id: int, track: dict):
    if not track.get("download_url"):
        await bot.send_message(chat_id, "Ссылка для скачивания недоступна.")
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(track["download_url"]) as resp:
                if resp.status != 200:
                    await bot.send_message(chat_id, "Ошибка загрузки трека.")
                    return
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
                    tmp_file.write(await resp.read())
                    tmp_path = tmp_file.name
        await bot.send_audio(
            chat_id=chat_id,
            audio=open(tmp_path, 'rb'),
            title=track['title'],
            performer=track['artists'],
            caption=f"{track['title']} — {track['artists']}"
        )
        os.remove(tmp_path)
    except Exception as e:
        logger.error(f"Ошибка отправки трека: {e}")
        await bot.send_message(chat_id, "Произошла ошибка при отправке файла.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_active, channel_message_id
    query = update.callback_query
    await query.answer()
    if query.data == "start_tracker":
        if not bot_active:
            bot_active = True
            channel_message_id = None
            asyncio.create_task(track_checker())
            await update_status_message(context.bot, query.message.chat.id, "🟢 Трекер запущен!")
    elif query.data == "stop_tracker":
        if bot_active:
            bot_active = False
            if channel_message_id:
                await delete_message(context.bot, CHANNEL_ID, channel_message_id)
                channel_message_id = None
            await update_status_message(context.bot, query.message.chat.id, "⏹️ Трекер остановлен")
    elif query.data == "refresh_status":
        status = "🟢 Активен" if bot_active else "🔴 Остановлен"
        await update_status_message(context.bot, query.message.chat.id, f"{status}\nУправление:")
    elif query.data == "download_track":
        track = get_current_track()
        if track:
            await download_and_send_track(context.bot, DOWNLOAD_CHANNEL_ID, track)
            await query.message.reply_text("Трек отправлен в канал!")
        else:
            await query.message.reply_text("Трек не найден.")

async def delete_message(bot: Bot, chat_id: int, msg_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")

async def update_status_message(bot: Bot, chat_id: int, text: str):
    global bot_status_message_id
    try:
        if bot_status_message_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=bot_status_message_id,
                text=text,
                reply_markup=get_inline_keyboard()
            )
        else:
            msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=get_inline_keyboard()
            )
            bot_status_message_id = msg.message_id
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_status_message_id
    if bot_status_message_id:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=bot_status_message_id
            )
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения: {e}")
    msg = await update.message.reply_text(
        "🎵 Музыкальный трекер\nУправление:",
        reply_markup=get_inline_keyboard()
    )
    bot_status_message_id = msg.message_id

async def send_new_track_message(bot: Bot, track: dict) -> int:
    try:
        keyboard = get_inline_keyboard()
        msg = await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=track["img"],
            caption=f"{track['title']} — {track['artists']}",
            reply_markup=keyboard
        )
        return msg.message_id
    except Exception as e:
        logger.error(f"Ошибка отправки трека: {e}")
        return None

async def edit_track_message(bot: Bot, track: dict, msg_id: int) -> bool:
    try:
        keyboard = get_inline_keyboard()
        await bot.edit_message_media(
            chat_id=CHANNEL_ID,
            message_id=msg_id,
            media=InputMediaPhoto(media=track["img"], caption=f"{track['title']} — {track['artists']}"),
            reply_markup=keyboard
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления трека: {e}")
        return False

async def track_checker():
    global last_track_id, channel_message_id, bot_active
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    while bot_active:
        track = get_current_track()
        if track:
            if channel_message_id and track["id"] != last_track_id:
                if not await edit_track_message(bot, track, channel_message_id):
                    channel_message_id = await send_new_track_message(bot, track)
                last_track_id = track["id"]
            elif not channel_message_id:
                channel_message_id = await send_new_track_message(bot, track)
                last_track_id = track["id"]
        await asyncio.sleep(5)

def main():
    required_vars = ["TELEGRAM_BOT_TOKEN", "YANDEX_TOKEN", "CHANNEL_ID", "DOWNLOAD_CHANNEL_ID"]
    if missing := [var for var in required_vars if not os.getenv(var)]:
        logger.error(f"Отсутствуют обязательные переменные: {', '.join(missing)}")
        return
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
