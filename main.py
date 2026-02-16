import asyncio
import logging
import os
import tempfile
import threading
from datetime import datetime

import pytz
import aiohttp
import requests
from dotenv import load_dotenv
from flask import Flask
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
from telegram.error import BadRequest

# ===========================
# ЛОГИ
# ===========================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

CONFIG = {
    "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
    "YANDEX_TOKEN": os.getenv("YANDEX_TOKEN"),
    "CHANNEL_ID": os.getenv("CHANNEL_ID"),
    "DOWNLOAD_CHANNEL_ID": int(os.getenv("DOWNLOAD_CHANNEL_ID")),
}

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# ===========================
# СОСТОЯНИЕ
# ===========================
class BotState:
    def __init__(self):
        self.last_track_id = None
        self.channel_message_id = None
        self.download_message_id = None
        self.bot_active = False
        self.channel_post_settings = {
            "poster": True,
            "buttons": True
        }

bot_state = BotState()

# ===========================
# ВСПОМОГАТЕЛЬНЫЕ
# ===========================
def get_moscow_time():
    return datetime.now(MOSCOW_TZ).strftime("%H:%M")

def generate_multi_service_link(track_id: str) -> str:
    return f"https://song.link/ya/{track_id}"

def generate_caption(track: dict):
    return (
        f"{track['time']} - "
        f"<a href='{track['multi_link']}'>{track['title']}</a> — "
        f"<a href='{track['multi_link']}'>{track['artists']}</a>"
    )

def get_channel_keyboard():
    if not bot_state.channel_post_settings["buttons"]:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ Скачать трек", url="https://t.me/text_pesni_aqw")]
    ])

def get_bot_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Запустить", callback_data="start_tracker"),
            InlineKeyboardButton("⏹️ Остановить", callback_data="stop_tracker")
        ],
        [
            InlineKeyboardButton("🖼 Постер", callback_data="toggle_poster"),
            InlineKeyboardButton("🔘 Кнопки", callback_data="toggle_buttons")
        ]
    ])

# ===========================
# ПОЛУЧЕНИЕ ТРЕКА
# ===========================
def get_current_track():
    try:
        headers = {"ya-token": CONFIG["YANDEX_TOKEN"]}
        response = requests.get(
            "https://track.mipoh.ru/get_current_track_beta",
            headers=headers,
            timeout=10,
            verify=False,
        )

        if response.status_code != 200:
            return None

        data = response.json()
        if not data.get("track"):
            return None

        track = data["track"]
        track_id = track.get("track_id")
        if not track_id:
            return None

        artists = ", ".join(track["artist"]) if isinstance(track.get("artist"), list) else track.get("artist", "")
        title = track.get("title", "")

        return {
            "id": track_id,
            "title": title,
            "artists": artists,
            "time": get_moscow_time(),
            "multi_link": generate_multi_service_link(track_id),
            "img": track.get("img"),
            "download_url": track.get("download_link"),
        }

    except Exception as e:
        logger.error(f"Ошибка получения трека: {e}")
        return None

# ===========================
# УДАЛЕНИЕ СООБЩЕНИЙ
# ===========================
async def delete_previous_messages(bot: Bot):
    if bot_state.channel_message_id:
        try:
            await bot.delete_message(CONFIG["CHANNEL_ID"], bot_state.channel_message_id)
        except:
            pass
        bot_state.channel_message_id = None

    if bot_state.download_message_id:
        try:
            await bot.delete_message(CONFIG["DOWNLOAD_CHANNEL_ID"], bot_state.download_message_id)
        except:
            pass
        bot_state.download_message_id = None

# ===========================
# ОТПРАВКА / РЕДАКТИРОВАНИЕ
# ===========================
async def send_or_edit_track_message(bot: Bot, track: dict):
    caption = generate_caption(track)
    msg_id = bot_state.channel_message_id
    use_poster = bot_state.channel_post_settings["poster"]

    if msg_id:
        try:
            if use_poster:
                await bot.edit_message_media(
                    chat_id=CONFIG["CHANNEL_ID"],
                    message_id=msg_id,
                    media=InputMediaPhoto(
                        media=track["img"],
                        caption=caption,
                        parse_mode="HTML"
                    ),
                    reply_markup=get_channel_keyboard()
                )
            else:
                await bot.edit_message_text(
                    chat_id=CONFIG["CHANNEL_ID"],
                    message_id=msg_id,
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=get_channel_keyboard()
                    disable_web_page_preview=True
                )
            return

        except BadRequest as e:
            if "Message is not modified" in str(e):
                return

            try:
                await bot.delete_message(CONFIG["CHANNEL_ID"], msg_id)
            except:
                pass

            bot_state.channel_message_id = None

        except Exception:
            pass

    # Отправляем новое сообщение
    if use_poster:
        msg = await bot.send_photo(
            CONFIG["CHANNEL_ID"],
            photo=track["img"],
            caption=caption,
            parse_mode="HTML",
            reply_markup=get_channel_keyboard()
        )
    else:
        msg = await bot.send_message(
            CONFIG["CHANNEL_ID"],
            caption,
            parse_mode="HTML",
            reply_markup=get_channel_keyboard()
            disable_web_page_preview=True
        )

    bot_state.channel_message_id = msg.message_id

# ===========================
# ОТПРАВКА MP3
# ===========================
async def send_new_download_message(bot: Bot, track: dict):
    if not track.get("download_url"):
        return None

    async with aiohttp.ClientSession() as session:
        async with session.get(track["download_url"]) as resp:
            if resp.status != 200:
                return None

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                tmp.write(await resp.read())
                tmp_path = tmp.name

    msg = await bot.send_audio(
        CONFIG["DOWNLOAD_CHANNEL_ID"],
        audio=open(tmp_path, "rb"),
        title=track["title"],
        performer=track["artists"],
    )

    os.unlink(tmp_path)
    return msg.message_id

# ===========================
# ОСНОВНОЙ ЦИКЛ
# ===========================
async def track_checker(bot: Bot):
    while bot_state.bot_active:
        track = get_current_track()

        if track and track["id"] != bot_state.last_track_id:

            if bot_state.download_message_id:
                try:
                    await bot.delete_message(
                        CONFIG["DOWNLOAD_CHANNEL_ID"],
                        bot_state.download_message_id
                    )
                except:
                    pass

            await send_or_edit_track_message(bot, track)
            bot_state.last_track_id = track["id"]
            bot_state.download_message_id = await send_new_download_message(bot, track)

        await asyncio.sleep(5)

# ===========================
# КНОПКИ
# ===========================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot = context.bot

    if query.data == "start_tracker":
        if not bot_state.bot_active:
            bot_state.bot_active = True
            asyncio.create_task(track_checker(bot))
            await query.edit_message_text("🟢 Трекер запущен", reply_markup=get_bot_keyboard())

    elif query.data == "stop_tracker":
        bot_state.bot_active = False
        await delete_previous_messages(bot)
        bot_state.last_track_id = None
        await query.edit_message_text("🔴 Трекер остановлен", reply_markup=get_bot_keyboard())

    elif query.data == "toggle_poster":
        bot_state.channel_post_settings["poster"] = not bot_state.channel_post_settings["poster"]
        await query.edit_message_reply_markup(reply_markup=get_bot_keyboard())

    elif query.data == "toggle_buttons":
        bot_state.channel_post_settings["buttons"] = not bot_state.channel_post_settings["buttons"]
        await query.edit_message_reply_markup(reply_markup=get_bot_keyboard())

# ===========================
# /start
# ===========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎵 Музыкальный трекер", reply_markup=get_bot_keyboard())

# ===========================
# TELEGRAM
# ===========================
def run_bot():
    app = Application.builder().token(CONFIG["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling(drop_pending_updates=True)

# ===========================
# FLASK ДЛЯ RENDER
# ===========================
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

# ===========================
# MAIN
# ===========================
if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    run_bot()
