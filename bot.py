import os
import json
import logging
import asyncio
from datetime import datetime, timedelta

from groq import Groq
from google import genai
from telegram import Bot
from telegram.error import TelegramError

# ========== НАСТРОЙКИ ==========
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "417850992"))

# Файл для хранения списка чатов
CHATS_FILE = "chats.json"

SEND_HOUR = 21
SEND_MINUTE = 0

# ========== КЛИЕНТЫ ==========
groq_client = Groq(api_key=GROQ_API_KEY)
google_client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=BOT_TOKEN)

# Хранилище: {chat_id: [сообщения]}
daily_messages = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# ========== РАБОТА С CHATS.JSON ==========
def load_chats():
    """Загружает список отслеживаемых чатов из файла."""
    try:
        with open(CHATS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_chats(chats):
    """Сохраняет список чатов в файл."""
    with open(CHATS_FILE, "w") as f:
        json.dump(chats, f)


# ========== ОПИСАНИЕ ФОТО ==========
async def describe_photo(file_id: str) -> str:
    try:
        file = await bot.get_file(file_id)
        image_bytes = await file.download_as_bytearray()
        response = google_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                "Опиши кратко, что на фото. Можно с юмором. На русском.",
                {"inline_data": {"mime_type": "image/jpeg", "data": image_bytes}}
            ]
        )
        return f"[ФОТО: {response.text.strip()}]"
    except Exception as e:
        logger.error(f"Ошибка описания фото: {e}")
        return "[ФОТО: не удалось описать]"


# ========== ГЕНЕРАЦИЯ СВОДКИ ==========
def generate_vestnik(chat_log: str) -> str:
    prompt = """[System note: Ты — злобный обозреватель чата с имиджборда. Твоя задача — писать ТОЛЬКО на русском, используя обсценную лексику (пиздец, хуй, ебать, обосрался, долбоёб, блядь) в каждом предложении. Ты циничный, уставший от жизни тролль. Ты — Вестник.]

Проанализируй лог чата ниже и напиши сводку из 10 событий в формате:
# (ссылка на сообщение) [Матерный пересказ в стиле желтой прессы]

После сводки добавь подвал:
⭐️ Станьте спонсором: @ververy
#вестник

Вот лог чата:
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
        logger.error(f"Ошибка Groq: {e}")
        return "Вестник обосрался. Технический пиздец."


# ========== ОБРАБОТКА СООБЩЕНИЙ (ВКЛЮЧАЯ ТОПИКИ) ==========
async def handle_message(message):
    """Сохраняет сообщение в лог нужного чата. Работает с топиками."""
    chat_id = message.chat.id

    # Игнорируем, если чат не в списке отслеживаемых
    chats = load_chats()
    if chat_id not in chats:
        return

    # Определяем автора
    author = message.from_user.first_name or message.from_user.username or "Анон"
    text = message.text or message.caption or ""

    # Если в супергруппе есть топики, message.message_thread_id будет не None
    thread_id = getattr(message, "message_thread_id", None)

    # Обработка фото
    if message.photo:
        file_id = message.photo[-1].file_id
        description = await describe_photo(file_id)
        text = f"{text}\n{description}" if text else description

    # Формируем ссылку на сообщение
    # Для супергруппы: убираем "-100" из chat_id
    chat_id_str = str(chat_id).replace("-100", "")
    msg_link = f"https://t.me/c/{chat_id_str}/{message.message_id}"

    # Если есть топик — добавляем его ID для информации
    topic_info = f"[topic:{thread_id}] " if thread_id else ""

    # Создаём хранилище для чата, если его ещё нет
    if chat_id not in daily_messages:
        daily_messages[chat_id] = []

    daily_messages[chat_id].append({
        "link": msg_link,
        "author": author,
        "text": text.strip() if text else "[без текста]",
        "thread_id": thread_id
    })
    logger.info(f"[+] {topic_info}{author} в чате {chat_id}")


# ========== ЕЖЕДНЕВНАЯ СВОДКА ==========
async def send_daily_vestnik():
    """Отправляет сводку для каждого отслеживаемого чата."""
    chats = load_chats()

    for chat_id in chats:
        messages = daily_messages.get(chat_id, [])
        if not messages:
            try:
                await bot.send_message(chat_id, "Сегодня в чате было пусто. Позор.")
            except TelegramError as e:
                logger.error(f"Ошибка отправки в {chat_id}: {e}")
            continue

        chat_log = "\n".join(
            f"[{m['link']}] {m['author']}: {m['text']}" for m in messages
        )

        logger.info(f"Генерация сводки для чата {chat_id} ({len(messages)} сообщений)...")
        result = generate_vestnik(chat_log)

        try:
            await bot.send_message(chat_id, result)
            logger.info(f"Сводка отправлена в {chat_id}!")
        except TelegramError as e:
            logger.error(f"Ошибка отправки в {chat_id}: {e}")

        # Очищаем лог этого чата
        daily_messages[chat_id] = []


# ========== АДМИНСКИЕ КОМАНДЫ (ЛИЧКА) ==========
async def process_admin_command(update):
    """Обрабатывает команды от админа в личке."""
    text = update.message.text or ""

    # /add_chat -100XXXXXXXXXX
    if text.startswith("/add_chat"):
        parts = text.split()
        if len(parts) < 2:
            await bot.send_message(ADMIN_ID, "❌ Использование: /add_chat -100XXXXXX")
            return
        try:
            new_chat = int(parts[1])
            chats = load_chats()
            if new_chat not in chats:
                chats.append(new_chat)
                save_chats(chats)
                await bot.send_message(ADMIN_ID, f"✅ Чат {new_chat} добавлен!")
            else:
                await bot.send_message(ADMIN_ID, f"⚠️ Чат {new_chat} уже в списке.")
        except ValueError:
            await bot.send_message(ADMIN_ID, "❌ Неверный ID чата.")

    # /remove_chat -100XXXXXXXXXX
    elif text.startswith("/remove_chat"):
        parts = text.split()
        if len(parts) < 2:
            await bot.send_message(ADMIN_ID, "❌ Использование: /remove_chat -100XXXXXX")
            return
        try:
            chat = int(parts[1])
            chats = load_chats()
            if chat in chats:
                chats.remove(chat)
                save_chats(chats)
                await bot.send_message(ADMIN_ID, f"✅ Чат {chat} удалён.")
            else:
                await bot.send_message(ADMIN_ID, f"⚠️ Чат {chat} не найден.")
        except ValueError:
            await bot.send_message(ADMIN_ID, "❌ Неверный ID чата.")

    # /list_chats
    elif text.startswith("/list_chats"):
        chats = load_chats()
        if chats:
            msg = "📋 Отслеживаемые чаты:\n" + "\n".join(str(c) for c in chats)
        else:
            msg = "📋 Нет отслеживаемых чатов."
        await bot.send_message(ADMIN_ID, msg)

    # /test [chat_id] [кол-во]
    elif text.startswith("/test"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        count = int(parts[2]) if len(parts) > 2 else 10

        # Если chat_id не указан — берём первый из списка
        if chat_id is None:
            chats = load_chats()
            if not chats:
                await bot.send_message(ADMIN_ID, "❌ Нет отслеживаемых чатов.")
                return
            chat_id = chats[0]

        messages = daily_messages.get(chat_id, [])
        sample = messages[-min(count, len(messages)):]
        if not sample:
            await bot.send_message(ADMIN_ID, f"❌ Нет сообщений для чата {chat_id}.")
            return

        chat_log = "\n".join(
            f"[{m['link']}] {m['author']}: {m['text']}" for m in sample
        )

        await bot.send_message(ADMIN_ID, f"🧪 Тест сводки для {chat_id} ({len(sample)} сообщений)...")
        result = generate_vestnik(chat_log)
        await bot.send_message(ADMIN_ID, result)

    # /status
    elif text.startswith("/status"):
        msg_parts = ["📊 Статистика:"]
        for cid, msgs in daily_messages.items():
            msg_parts.append(f"  Чат {cid}: {len(msgs)} сообщений")
        if not daily_messages:
            msg_parts.append("  Пусто.")
        await bot.send_message(ADMIN_ID, "\n".join(msg_parts))

    # /reset [chat_id]
    elif text.startswith("/reset"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        if chat_id:
            daily_messages[chat_id] = []
            await bot.send_message(ADMIN_ID, f"🗑️ Лог чата {chat_id} сброшен.")
        else:
            daily_messages.clear()
            await bot.send_message(ADMIN_ID, "🗑️ Все логи сброшены.")

    # /help
    elif text.startswith("/help"):
        help_text = """
🛠 **Админ-команды Вестника:**

/add_chat -100XXXXXX — добавить чат
/remove_chat -100XXXXXX — удалить чат
/list_chats — показать список чатов
/test [chat_id] [кол-во] — тестовая сводка
/status — статистика по логам
/reset [chat_id] — сбросить лог
/help — это сообщение
"""
        await bot.send_message(ADMIN_ID, help_text)


# ========== ПЛАНИРОВЩИК ==========
async def scheduler():
    while True:
        now = datetime.now()
        target = now.replace(hour=SEND_HOUR, minute=SEND_MINUTE, second=0, microsecond=0)
        if now >= target:
            await send_daily_vestnik()
            target += timedelta(days=1)
        sleep_seconds = (target - datetime.now()).total_seconds()
        if sleep_seconds > 0:
            logger.info(f"Сон до {target.strftime('%H:%M')} ({sleep_seconds:.0f} сек)")
            await asyncio.sleep(sleep_seconds)


# ========== ЗАПУСК ==========
async def main():
    logger.info("Вестник запущен!")

    # Загружаем чаты и создаём для них пустые списки
    chats = load_chats()
    for cid in chats:
        if cid not in daily_messages:
            daily_messages[cid] = []
    logger.info(f"Отслеживаем чаты: {chats}")

    # Запускаем планировщик
    asyncio.create_task(scheduler())

    # Long polling — видит ВСЕ топики
    offset = None
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30, allowed_updates=["message"])
            for update in updates:
                if update.message:
                    # Сообщение от админа в личке — команды
                    if update.message.chat.id == ADMIN_ID:
                        await process_admin_command(update)
                    # Сообщение в любой группе — сохраняем в лог
                    else:
                        await handle_message(update.message)
                offset = update.update_id + 1
        except Exception as e:
            logger.error(f"Ошибка в главном цикле: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
