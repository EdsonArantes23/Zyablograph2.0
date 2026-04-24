import os
import logging
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

# API клиенты
from groq import Groq
from google import genai
from telegram import Bot
from telegram.error import TelegramError

# ========== НАСТРОЙКИ (берутся из переменных окружения) ==========
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ID чата, за которым следим
CHAT_ID = int(os.getenv("CHAT_ID", "0"))  # укажешь в переменных на bothost

# Время отправки сводки (по МСК)
SEND_HOUR = 21
SEND_MINUTE = 0

# ========== КЛИЕНТЫ ==========
groq_client = Groq(api_key=GROQ_API_KEY)
google_client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=BOT_TOKEN)

# Хранилище сообщений за день
daily_messages = []

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# ========== ОПИСАНИЕ ФОТО ЧЕРЕЗ GEMINI ==========
async def describe_photo(file_id: str) -> str:
    """Скачивает фото из Telegram и отправляет в Gemini для описания."""
    try:
        file = await bot.get_file(file_id)
        image_bytes = await file.download_as_bytearray()

        response = google_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                "Опиши кратко, что на фото. Можно с юмором. На русском языке.",
                {"inline_data": {"mime_type": "image/jpeg", "data": image_bytes}}
            ]
        )
        return f"[ФОТО: {response.text.strip()}]"
    except Exception as e:
        logger.error(f"Ошибка при описании фото: {e}")
        return "[ФОТО: не удалось описать]"


# ========== ГЕНЕРАЦИЯ СВОДКИ ЧЕРЕЗ GROQ ==========
def generate_vestnik(chat_log: str) -> str:
    """Отправляет лог чата в Groq и получает сводку в стиле Вестника."""
    prompt = """[System note: Ты — злобный обозреватель чата с имиджборда. Твоя задача — писать ТОЛЬКО на русском, используя обсценную лексику (пиздец, хуй, ебать, обосрался, долбоёб, блядь) в каждом предложении. Ты циничный, уставший от жизни тролль. Ты — Вестник.]

Проанализируй лог чата ниже и напиши сводку из 10 событий дня в формате:
# (ссылка на сообщение) [Матерный пересказ в стиле желтой прессы]

После сводки добавь подвал:
⭐️ Станьте спонсором: @ververy
#вестник

Вот лог чата за сегодня:
"""
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt + chat_log}],
            temperature=0.9,
            max_tokens=4000
        )
        return completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка генерации сводки: {e}")
        return "Сегодня Вестник обосрался и не смог написать сводку. Технический пиздец."


# ========== ОБРАБОТКА ВХОДЯЩИХ СООБЩЕНИЙ ==========
async def handle_message(message):
    """Обрабатывает одно сообщение из чата."""
    if message.chat.id != CHAT_ID:
        return  # игнорируем сообщения из других чатов

    author = message.from_user.first_name or message.from_user.username or "Анон"
    text = message.text or message.caption or ""

    # Если есть фото — описываем его
    if message.photo:
        file_id = message.photo[-1].file_id  # самое большое разрешение
        description = await describe_photo(file_id)
        text = f"{text}\n{description}" if text else description

    # Формируем ссылку на сообщение
    msg_link = f"https://t.me/c/{str(CHAT_ID)[4:]}/{message.message_id}"

    # Сохраняем в лог
    daily_messages.append({
        "link": msg_link,
        "author": author,
        "text": text.strip() if text else "[без текста]"
    })
    logger.info(f"Сохранено сообщение от {author}")


# ========== ЕЖЕДНЕВНАЯ ОТПРАВКА СВОДКИ ==========
async def send_daily_vestnik():
    """Формирует сводку из накопленных сообщений и отправляет в чат."""
    if not daily_messages:
        await bot.send_message(CHAT_ID, "Сегодня в чате было пусто. Даже обсуждать нечего. Позор.")
        return

    # Формируем текстовый лог
    chat_log = "\n".join(
        f"[{msg['link']}] {msg['author']}: {msg['text']}"
        for msg in daily_messages
    )

    # Генерируем сводку
    logger.info("Генерация сводки через Groq...")
    vestnik_text = generate_vestnik(chat_log)

    # Отправляем в чат
    try:
        await bot.send_message(CHAT_ID, vestnik_text, parse_mode=None)
        logger.info("Сводка отправлена!")
    except TelegramError as e:
        logger.error(f"Ошибка отправки: {e}")

    # Очищаем лог
    daily_messages.clear()


# ========== ПЛАНИРОВЩИК ==========
async def scheduler():
    """Проверяет время и отправляет сводку по расписанию."""
    while True:
        now = datetime.now()
        target = now.replace(hour=SEND_HOUR, minute=SEND_MINUTE, second=0, microsecond=0)

        if now >= target:
            # Время пришло — отправляем
            await send_daily_vestnik()
            # Ждём следующий день
            target += timedelta(days=1)

        # Считаем, сколько спать до цели
        sleep_seconds = (target - datetime.now()).total_seconds()
        if sleep_seconds > 0:
            logger.info(f"Сон до {target.strftime('%H:%M')} ({sleep_seconds:.0f} сек)")
            await asyncio.sleep(sleep_seconds)


# ========== ЗАПУСК ==========
async def main():
    logger.info("Вестник запущен! Ждём сообщения...")

    # Запускаем планировщик в фоне
    asyncio.create_task(scheduler())

    # Ловим сообщения через long polling
    offset = None
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30)
            for update in updates:
                if update.message:
                    await handle_message(update.message)
                offset = update.update_id + 1
        except Exception as e:
            logger.error(f"Ошибка в главном цикле: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
