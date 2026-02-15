import asyncio
import logging
import os
import tempfile
from datetime import datetime
import pytz
import aiohttp
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

# ===========================
# Логирование
# ===========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
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
# Состояние бота
# ===========================
class BotState:
    def __init__(self):
        self.last_track_id = None
        self.channel_message_id = None
        self.download_message_id = None
        self.bot_active = False
        self.bot_status_message_id = None

        # Настройки отображения в канале
        self.channel_post_settings = {
            "poster": True,    # показывать постер
            "buttons": True    # показывать кнопки
        }


bot_state = BotState()

# ===========================
# Вспомогательные функции
# ===========================
def get_moscow_time():
    return datetime.now(MOSCOW_TZ).strftime("%H:%M")


def generate_multi_service_link(track_id: str) -> str:
    return f"https://song.link/ya/{track_id}"


def get_bot_keyboard():
    poster_status = "Вкл" if bot_state.channel_post_settings["poster"] else "Выкл"
    buttons_status = "Вкл" if bot_state.channel_post_settings["buttons"] else "Выкл"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Запустить", callback_data="start_tracker"),
            InlineKeyboardButton("⏹️ Остановить", callback_data="stop_tracker")
        ],
        [
            InlineKeyboardButton("🔄 Обновить статус", callback_data="refresh_status")
        ],
        [
            InlineKeyboardButton(f"🖼 Постер: {poster_status}", callback_data="toggle_poster"),
            InlineKeyboardButton(f"🔘 Кнопки: {buttons_status}", callback_data="toggle_buttons")
        ]
    ])


def get_channel_keyboard(track: dict):
    if not bot_state.channel_post_settings["buttons"]:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬇️ Скачать трек", url="https://t.me/text_pesni_aqw")]
    ])


def generate_caption(track: dict):
   return f"{track['time']} - <a href='{track['multi_link']}'>{track['title']}</a> — <a href='{track['multi_link']}'>{track['artists']}</a>"


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
# Отправка/редактирование поста
# ===========================
async def send_or_edit_track_message(bot: Bot, track: dict):
    caption = generate_caption(track)
    msg_id = bot_state.channel_message_id

    # Сценарий 1: постер + кнопки
    if bot_state.channel_post_settings["poster"] and bot_state.channel_post_settings["buttons"]:
        if msg_id:
            try:
                await bot.edit_message_media(
                    chat_id=CONFIG["CHANNEL_ID"],
                    message_id=msg_id,
                    media=InputMediaPhoto(media=track["img"], caption=caption, parse_mode="HTML"),
                    reply_markup=get_channel_keyboard(track)
                )
            except:
                msg = await bot.send_photo(
                    chat_id=CONFIG["CHANNEL_ID"],
                    photo=track["img"],
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=get_channel_keyboard(track)
                )
                bot_state.channel_message_id = msg.message_id
        else:
            msg = await bot.send_photo(
                chat_id=CONFIG["CHANNEL_ID"],
                photo=track["img"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=get_channel_keyboard(track)
            )
            bot_state.channel_message_id = msg.message_id

    # Сценарий 2: постер + название
    elif bot_state.channel_post_settings["poster"]:
        if msg_id:
            try:
                await bot.edit_message_media(
                    chat_id=CONFIG["CHANNEL_ID"],
                    message_id=msg_id,
                    media=InputMediaPhoto(media=track["img"], caption=caption, parse_mode="HTML"),
                    reply_markup=None
                )
            except:
                msg = await bot.send_photo(
                    chat_id=CONFIG["CHANNEL_ID"],
                    photo=track["img"],
                    caption=caption,
                    parse_mode="HTML"
                )
                bot_state.channel_message_id = msg.message_id
        else:
            msg = await bot.send_photo(
                chat_id=CONFIG["CHANNEL_ID"],
                photo=track["img"],
                caption=caption,
                parse_mode="HTML"
            )
            bot_state.channel_message_id = msg.message_id

    # Сценарий 3: кнопки + название
    elif bot_state.channel_post_settings["buttons"]:
        if msg_id:
            try:
                await bot.edit_message_text(
                    chat_id=CONFIG["CHANNEL_ID"],
                    message_id=msg_id,
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=get_channel_keyboard(track)
                )
            except:
                msg = await bot.send_message(
                    chat_id=CONFIG["CHANNEL_ID"],
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=get_channel_keyboard(track)
                )
                bot_state.channel_message_id = msg.message_id
        else:
            msg = await bot.send_message(
                chat_id=CONFIG["CHANNEL_ID"],
                text=caption,
                parse_mode="HTML",
                reply_markup=get_channel_keyboard(track)
            )
            bot_state.channel_message_id = msg.message_id

# ===========================
# Отправка mp3 в отдельный канал
# ===========================
async def send_new_download_message(bot: Bot, track: dict) -> int:
    if not track.get("download_url"):
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(track["download_url"]) as resp:
                if resp.status != 200:
                    return None

                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    tmp.write(await resp.read())
                    tmp_path = tmp.name

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
        logger.error(f"Ошибка отправки mp3: {e}")
        return None

# ===========================
# Основной цикл трекера
# ===========================
async def track_checker(bot: Bot):
    while bot_state.bot_active:
        track = get_current_track()
        if track and track["id"] != bot_state.last_track_id:
            await send_or_edit_track_message(bot, track)
            bot_state.last_track_id = track["id"]
            bot_state.download_message_id = await send_new_download_message(bot, track)
        await asyncio.sleep(5)

# ===========================
# Обработка inline кнопок
# ===========================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot = context.bot
    chat_id = query.message.chat.id

    if query.data == "start_tracker":
        if not bot_state.bot_active:
            bot_state.bot_active = True
            asyncio.create_task(track_checker(bot))
            await update_status_message(bot, chat_id, "🟢 Трекер запущен")

    elif query.data == "stop_tracker":
        bot_state.bot_active = False
        await update_status_message(bot, chat_id, "🔴 Трекер остановлен")

    elif query.data == "refresh_status":
        status = "🟢 Активен" if bot_state.bot_active else "🔴 Остановлен"
        await update_status_message(bot, chat_id, status)

    elif query.data == "toggle_poster":
        bot_state.channel_post_settings["poster"] = not bot_state.channel_post_settings["poster"]
        await update_status_message(bot, chat_id, "Настройки обновлены")

    elif query.data == "toggle_buttons":
        bot_state.channel_post_settings["buttons"] = not bot_state.channel_post_settings["buttons"]
        await update_status_message(bot, chat_id, "Настройки обновлены")

# ===========================
# Обновление сообщения со статусом бота
# ===========================
async def update_status_message(bot: Bot, chat_id: int, text: str):
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

# ===========================
# Команда /start
# ===========================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🎵 Музыкальный трекер\nУправление:",
        reply_markup=get_bot_keyboard()
    )
    bot_state.bot_status_message_id = msg.message_id

# ===========================
# Запуск бота
# ===========================
def main():
    app = Application.builder().token(CONFIG["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
