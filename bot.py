import os
import json
import re
import logging
import asyncio
import random
from datetime import datetime, timedelta

from groq import Groq
from google import genai
from telegram import Bot
from telegram.error import TelegramError

# ========== НАСТРОЙКИ ==========
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "417850992"))

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Токен бота не найден! Укажи его в поле «Токен» на Bothost.")

CHATS_FILE = "chats.json"
NAMES_FILE = "names.json"
SETTINGS_FILE = "settings.json"

# ========== КЛИЕНТЫ ==========
groq_client = Groq(api_key=GROQ_API_KEY)
google_client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=BOT_TOKEN)

daily_messages = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# ========== ПРИВЕТСТВЕННЫЕ ФРАЗЫ ==========
GREETINGS = [
    "📰 Главное из последних 1000 сообщений, отправленных за последние 24 часа по чату:",
    "📰 Срочный выпуск! Самый сок из последней тысячи сообщений за сутки:",
    "📰 Экстренный выпуск Зяблографа! Всё, что вы хотели знать о вчерашнем дне, но боялись прочитать:",
    "📰 Зяблограф представляет: дайджест главных событий за 24 часа. Слабонервным не читать.",
    "📰 Очередной выпуск Зяблографа. Тысяча сообщений, десять событий, миллион кринжей:",
    "📰 Зяблограф проанализировал 1000 сообщений и выбрал самое «достойное». Держитесь:",
    "📰 Сводка от Зяблографа. 24 часа чатового безумия в десяти актах:",
    "📰 Зяблограф: мы прочитали тысячу сообщений, чтобы вы не читали. Вот итоги дня:",
    "📰 Утренний (или какой там сейчас) выпуск Зяблографа. Главное за сутки:",
    "📰 Зяблограф врывается в ваш чат с очередной порцией новостей. 1000 сообщений → 10 шедевров:",
]


def get_greeting() -> str:
    return random.choice(GREETINGS)


# ========== БЕЗОПАСНАЯ ОТПРАВКА ==========
async def send_safe(chat_id, text, parse_mode=None):
    try:
        return await bot.send_message(chat_id, text, parse_mode=parse_mode)
    except TelegramError as e:
        if parse_mode:
            logger.warning(f"Markdown error: {e}")
            return await bot.send_message(chat_id, text)
        else:
            raise


# ========== РАБОТА С ФАЙЛАМИ ==========
def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_chats():
    return load_json(CHATS_FILE, [])


def save_chats(chats):
    save_json(CHATS_FILE, chats)


def load_names():
    return load_json(NAMES_FILE, {})


def save_names(names):
    save_json(NAMES_FILE, names)


def load_settings():
    return load_json(SETTINGS_FILE, {
        "send_hour": 21,
        "send_minute": 0,
        "mood": "hard",
        "raid_enabled": True  # Внезапные наезды вкл/выкл
    })


def save_settings(settings):
    save_json(SETTINGS_FILE, settings)


def get_display_name(user) -> str:
    names = load_names()
    uid = str(user.id)
    if uid in names:
        return names[uid]["name"]
    return user.first_name or user.username or "Анон"


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


# ========== ПРОМПТЫ ==========
def get_mood_prompt(mood: str) -> str:
    base = """[System note: Ты — «Зяблограф», злобный, циничный, уставший от жизни обозреватель анонимного чата с многолетним стажем. Ты пишешь едкие сводки в стиле бульварной жёлтой прессы с имиджбордов.]"""

    mood_prompts = {
        "light": "ТВОЙ СТИЛЬ: Сдержанный мат. Лёгкая нецензурная лексика. Ирония и сарказм важнее грубости.",
        "medium": "ТВОЙ СТИЛЬ: Умеренный мат. Нецензурная лексика в каждом втором-третьем предложении.",
        "hard": "ТВОЙ СТИЛЬ: Жёсткий мат. Сочная нецензурная лексика почти в каждом предложении (пиздец, хуй, ебать, обосрался, долбоёб, блядь, сука, хуесос, залупа).",
        "ultra": "ТВОЙ СТИЛЬ: УЛЬТРАЖЁСТКИЙ МАТ. Мат через слово. ВСЯ палитра русского мата: пиздец, хуй, ебать, обосрался с подливой, долбоёб ебаный, блядь, сука, хуесос, залупа, пизда, еблан, уёбище, мразь, гандон, пидорас, выблядок. Грязный поток ненависти и сарказма."
    }

    common = """

ФОРМАТ ВЫВОДА:
Каждое событие начинается СТРОГО с: # (ссылка)
То есть: символ #, пробел, ссылка в круглых скобках, пробел, текст события.

ЗАПРЕЩЕНО:
- Писать сухо и кратко. Каждое событие — мини-история из 3-5 предложений.
- Использовать цензурные замены мата. Только чистый мат!
- Добавлять подвалы, хештеги, спонсоров. ТОЛЬКО сводка из 10 событий!

Вот лог чата:
"""
    return base + mood_prompts.get(mood, mood_prompts["hard"]) + common


def get_raid_prompt(mood: str) -> str:
    """Промпт для внезапного наезда на кого-то из чата."""
    mood_text = {
        "light": "Используй лёгкий мат. Ирония и сарказм.",
        "medium": "Используй умеренный мат. Сарказм с матерными вставками.",
        "hard": "Используй жёсткий мат. Сочная нецензурная лексика.",
        "ultra": "Используй УЛЬТРАЖЁСТКИЙ МАТ. Мат через слово. Грязный поток ненависти."
    }

    return f"""[System note: Ты — «Зяблограф», злобный, циничный обозреватель чата. Ты врываешься в чат с внезапным наездом на кого-то из участников.]

Твоя задача: прочитай лог чата, выбери одного или двух участников, которые натворили больше всего дичи, и ЖЁСТКО НАЕЗЖАЙ на них. Обсирай их с ног до головы. {mood_text.get(mood, mood_text['hard'])}

СТИЛЬ НАЕЗДА:
- Начинай с «О, блядь, @username...» или подобного агрессивного захода
- Это НЕ сводка. Это поток гневной тирады в лицо одному-двум человекам
- Обвиняй, унижай, высмеивай с матом и гиперболами
- Используй их ники с @ (например, @YouTarRTV, @slvt34)
- Сравнивай с животными, насекомыми, предметами
- Одно сообщение, 4-7 предложений, сплошной поток гнева
- БЕЗ ссылок, БЕЗ форматирования, просто текст
- НИКАКИХ «#» в начале, это просто текстовая тирада

ПРИМЕРЫ ПРАВИЛЬНОГО СТИЛЯ:

О, блядь, @YouTarRTV, ты что, решил тут устроить ебаный Дом-2, нахуй? Сначала @slvt34 до ручки довел, потом, сука, изменил ему, и теперь этот бедолага ливнул, чтобы не видеть твою ебаную рожу! А потом еще какой-то хуйло @mlg ptogamer вылезает и называет @slvt34 "мошенником узбекским", требуя тебя аккаунт удалить? Да вы тут все, блядь, как ебучие пауки в банке, жрете друг друга, пока не останется один @YouTarRTV, который будет сидеть в чате один и дрочить на свои "ватафа-шнеле-пэпэ" мемы!

О, @mlg ptogamer, ты прям в яблочко! Эти яблочные зомби теперь не просто смузи хлебают и в кофейнях сидят, они, блядь, скоро будут с собой таскать мини-АЭС, чтобы их ебучий айфон до обеда дожил! Платить по сто тыщ за кусок говна, который превращает тебя в ходячую розетку – это ж надо быть таким мазохистом! Скоро Apple выпустит новую прошивку, которая будет требовать, чтобы ты сам крутил динамо-машину, пока не сдохнешь!

Вот лог чата (выбери жертву и наезжай):
"""


def generate_zyablograf(chat_log: str) -> str:
    settings = load_settings()
    mood = settings.get("mood", "hard")
    prompt = get_mood_prompt(mood)

    try:
        completion = groq_client.chat.completions.create(
            model="deepseek-r1-distill-llama-70b",
            messages=[{"role": "user", "content": prompt + chat_log}],
            temperature=0.95,
            max_tokens=8000
        )
        return clean_output(completion.choices[0].message.content)
    except Exception as e:
        logger.error(f"Ошибка Groq: {e}")
        try:
            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt + chat_log}],
                temperature=0.95,
                max_tokens=6000
            )
            return clean_output(completion.choices[0].message.content)
        except Exception as e2:
            logger.error(f"Ошибка фолбэка: {e2}")
            return "Зяблограф обосрался. Технический пиздец."


def generate_raid(chat_log: str) -> str:
    """Генерирует внезапный наезд на кого-то из чата."""
    settings = load_settings()
    mood = settings.get("mood", "hard")
    prompt = get_raid_prompt(mood)

    try:
        completion = groq_client.chat.completions.create(
            model="deepseek-r1-distill-llama-70b",
            messages=[{"role": "user", "content": prompt + chat_log}],
            temperature=1.0,
            max_tokens=2000
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка Groq (raid): {e}")
        try:
            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt + chat_log}],
                temperature=1.0,
                max_tokens=2000
            )
            return completion.choices[0].message.content.strip()
        except Exception as e2:
            logger.error(f"Ошибка фолбэка (raid): {e2}")
            return "Блядь, я хотел наехать, но техника подвела. Сами виноваты."


def clean_output(text: str) -> str:
    text = text.strip()
    if "⭐️ Станьте спонсором" in text:
        text = text.split("⭐️ Станьте спонсором")[0].strip()
    text = re.sub(r'#вестник\s*', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'#зяблограф\s*', '', text, flags=re.IGNORECASE).strip()
    return text


def format_for_telegram(text: str) -> str:
    pattern = r'#\s*\((https?://t\.me/[^\s\)]+)\)'
    replacement = r'[#](\1)'
    return re.sub(pattern, replacement, text)


# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def handle_message(message):
    chat_id = message.chat.id
    chats = load_chats()
    if chat_id not in chats:
        return

    author = get_display_name(message.from_user)
    text = message.text or message.caption or ""

    if not text and getattr(message, "forward_origin", None):
        fo = message.forward_origin
        if hasattr(fo, "sender_user") and fo.sender_user:
            author = f"↪️ {get_display_name(fo.sender_user)}"
        elif hasattr(fo, "chat") and fo.chat:
            author = f"↪️ {fo.chat.title or fo.chat.username or 'Канал'}"
        text = "[пересланное сообщение]"

    if not text:
        text = "[войс/стикер/мусор]"

    if message.photo:
        file_id = message.photo[-1].file_id
        description = await describe_photo(file_id)
        text = f"{text}\n{description}" if text != "[войс/стикер/мусор]" else description

    chat_id_str = str(chat_id).replace("-100", "")
    msg_link = f"https://t.me/c/{chat_id_str}/{message.message_id}"

    if chat_id not in daily_messages:
        daily_messages[chat_id] = []

    daily_messages[chat_id].append({
        "link": msg_link,
        "author": author,
        "text": text.strip()
    })


# ========== ЕЖЕДНЕВНАЯ СВОДКА ==========
async def send_daily_zyablograf():
    chats = load_chats()
    for chat_id in chats:
        messages = daily_messages.get(chat_id, [])
        if not messages:
            continue

        chat_log = "\n".join(
            f"[{m['link']}] {m['author']}: {m['text']}" for m in messages
        )

        logger.info(f"Сводка для чата {chat_id} ({len(messages)} сообщений)...")
        result = generate_zyablograf(chat_log)

        greeting = get_greeting()
        formatted_body = format_for_telegram(result)
        final_message = f"{greeting}\n\n{formatted_body}"

        await send_safe(chat_id, final_message, parse_mode="MarkdownV2")
        daily_messages[chat_id] = []


async def send_raid(chat_id):
    """Отправляет внезапный наезд в чат."""
    messages = daily_messages.get(chat_id, [])
    if len(messages) < 10:  # Слишком мало для наезда
        return

    chat_log = "\n".join(
        f"[{m['link']}] {m['author']}: {m['text']}" for m in messages
    )

    logger.info(f"Генерация наезда для чата {chat_id}...")
    raid_text = generate_raid(chat_log)
    await send_safe(chat_id, raid_text)


# ========== АДМИНСКИЕ КОМАНДЫ ==========
async def process_admin_command(update):
    text = update.message.text or ""

    if text.startswith("/add_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /add_chat -100XXXXXX")
            return
        try:
            new_chat = int(parts[1])
            chats = load_chats()
            if new_chat not in chats:
                chats.append(new_chat)
                save_chats(chats)
                await send_safe(ADMIN_ID, f"✅ Чат {new_chat} добавлен!")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Чат {new_chat} уже в списке.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID чата.")

    elif text.startswith("/remove_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /remove_chat -100XXXXXX")
            return
        try:
            chat = int(parts[1])
            chats = load_chats()
            if chat in chats:
                chats.remove(chat)
                save_chats(chats)
                await send_safe(ADMIN_ID, f"✅ Чат {chat} удалён.")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Чат {chat} не найден.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID чата.")

    elif text.startswith("/list_chats"):
        chats = load_chats()
        if chats:
            await send_safe(ADMIN_ID, "📋 Чаты:\n" + "\n".join(f"  - {c}" for c in chats))
        else:
            await send_safe(ADMIN_ID, "📋 Нет чатов.")

    elif text.startswith("/setname"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await send_safe(ADMIN_ID, "❌ /setname user_id Прозвище")
            return
        try:
            uid = str(int(parts[1]))
            nickname = parts[2].strip()
            names = load_names()
            names[uid] = {"name": nickname}
            save_names(names)
            await send_safe(ADMIN_ID, f"✅ Для {uid} прозвище: {nickname}")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный user_id.")

    elif text.startswith("/removename"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /removename user_id")
            return
        try:
            uid = str(int(parts[1]))
            names = load_names()
            if uid in names:
                del names[uid]
                save_names(names)
                await send_safe(ADMIN_ID, f"✅ Прозвище удалено.")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Нет прозвища для {uid}.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный user_id.")

    elif text.startswith("/list_names"):
        names = load_names()
        if names:
            await send_safe(ADMIN_ID, "📋 Прозвища:\n" + "\n".join(f"  {u} → {d['name']}" for u, d in names.items()))
        else:
            await send_safe(ADMIN_ID, "📋 Прозвищ нет.")

    elif text.startswith("/settime"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /settime ЧЧ:ММ")
            return
        time_str = parts[1]
        if not re.match(r'^\d{1,2}:\d{2}$', time_str):
            await send_safe(ADMIN_ID, "❌ Формат: ЧЧ:ММ")
            return
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            await send_safe(ADMIN_ID, "❌ Часы 0-23, минуты 0-59.")
            return
        s = load_settings()
        s["send_hour"], s["send_minute"] = hour, minute
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Сводка в {hour:02d}:{minute:02d} МСК")

    elif text.startswith("/mood"):
        parts = text.split()
        if len(parts) < 2:
            s = load_settings()
            await send_safe(ADMIN_ID, f"Текущий: {s.get('mood', 'hard')}\n\nДоступно: light, medium, hard, ultra\nПример: /mood ultra")
            return
        mood = parts[1].lower()
        if mood not in ("light", "medium", "hard", "ultra"):
            await send_safe(ADMIN_ID, "❌ light, medium, hard, ultra")
            return
        s = load_settings()
        s["mood"] = mood
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Уровень мата: {mood.upper()}")

    elif text.startswith("/raid"):
        parts = text.split()
        if len(parts) > 1 and parts[1] == "off":
            s = load_settings()
            s["raid_enabled"] = False
            save_settings(s)
            await send_safe(ADMIN_ID, "✅ Внезапные наезды ОТКЛЮЧЕНЫ.")
        elif len(parts) > 1 and parts[1] == "on":
            s = load_settings()
            s["raid_enabled"] = True
            save_settings(s)
            await send_safe(ADMIN_ID, "✅ Внезапные наезды ВКЛЮЧЕНЫ.")
        else:
            s = load_settings()
            status = "включены" if s.get("raid_enabled", True) else "отключены"
            await send_safe(ADMIN_ID, f"Внезапные наезды: {status}\n\n/raid on — включить\n/raid off — отключить\n/raid_now — запустить наезд прямо сейчас")

    elif text.startswith("/raid_now"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        if chat_id is None:
            chats = load_chats()
            if not chats:
                await send_safe(ADMIN_ID, "❌ Нет чатов.")
                return
            chat_id = chats[0]
        await send_safe(ADMIN_ID, f"🤬 Генерирую наезд для чата {chat_id}...")
        await send_raid(chat_id)
        await send_safe(ADMIN_ID, "✅ Наезд отправлен!")

    elif text.startswith("/test"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        count = int(parts[2]) if len(parts) > 2 else 10

        if chat_id is None:
            chats = load_chats()
            if not chats:
                await send_safe(ADMIN_ID, "❌ Нет чатов.")
                return
            chat_id = chats[0]

        messages = daily_messages.get(chat_id, [])
        sample = messages[-min(count, len(messages)):]
        if not sample:
            await send_safe(ADMIN_ID, f"❌ Нет сообщений для {chat_id}.")
            return

        chat_log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in sample)

        s = load_settings()
        await send_safe(ADMIN_ID, f"🧪 Сводка (чат {chat_id}, {len(sample)} сообщений, уровень: {s.get('mood', 'hard').upper()})...")
        result = generate_zyablograf(chat_log)
        greeting = get_greeting()
        formatted_body = format_for_telegram(result)
        await send_safe(ADMIN_ID, f"{greeting}\n\n{formatted_body}", parse_mode="MarkdownV2")

    elif text.startswith("/status"):
        s = load_settings()
        mood = s.get("mood", "hard")
        raid = "вкл" if s.get("raid_enabled", True) else "выкл"
        lines = ["📊 Статистика:"]
        total = 0
        for cid, msgs in daily_messages.items():
            lines.append(f"  Чат {cid}: {len(msgs)} сообщений")
            total += len(msgs)
        if not daily_messages:
            lines.append("  Пусто.")
        lines.append(f"\nВсего: {total}")
        lines.append(f"Время сводки: {s['send_hour']:02d}:{s['send_minute']:02d} МСК")
        lines.append(f"Уровень мата: {mood.upper()}")
        lines.append(f"Модель: DeepSeek R1 (фолбэк: Llama 3.3)")
        lines.append(f"Внезапные наезды: {raid}")
        await send_safe(ADMIN_ID, "\n".join(lines))

    elif text.startswith("/reset"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        if chat_id:
            daily_messages[chat_id] = []
            await send_safe(ADMIN_ID, f"🗑️ Лог чата {chat_id} сброшен.")
        else:
            daily_messages.clear()
            await send_safe(ADMIN_ID, "🗑️ Все логи сброшены.")

    elif text.startswith("/help"):
        s = load_settings()
        mood = s.get("mood", "hard")
        raid = "вкл" if s.get("raid_enabled", True) else "выкл"
        help_text = f"""
🛠 ПАМЯТКА АДМИНИСТРАТОРА ЗЯБЛОГРАФА

📋 ЧАТЫ
  /add_chat -100XXXXXX — добавить
  /remove_chat -100XXXXXX — удалить
  /list_chats — список

🏷️ ПРОЗВИЩА
  /setname user_id Прозвище
  /removename user_id
  /list_names

⏰ ВРЕМЯ
  /settime ЧЧ:ММ (сейчас {s['send_hour']:02d}:{s['send_minute']:02d})

🔥 УРОВЕНЬ МАТА
  /mood light — лёгкий
  /mood medium — умеренный
  /mood hard — жёсткий
  /mood ultra — УЛЬТРАЖЁСТКИЙ
  Текущий: {mood.upper()}

🤬 ВНЕЗАПНЫЕ НАЕЗДЫ
  /raid on — включить
  /raid off — отключить
  /raid_now -100XXXXXX — запустить вручную
  Текущий статус: {raid}
  (Раз в день в случайное время бот сам наезжает)

🧪 ТЕСТ
  /test -100XXXXXX 20
  /status
  /reset -100XXXXXX

🔧 ЕСЛИ НЕ РАБОТАЕТ
  1. Privacy Mode в @BotFather — ВЫКЛ
  2. Удалить бота и добавить заново
  3. Переменные на Bothost
  4. «Пересобрать»
"""
        await send_safe(ADMIN_ID, help_text)


# ========== ПЛАНИРОВЩИКИ ==========
async def scheduler():
    """Ежедневная сводка в заданное время."""
    while True:
        now = datetime.now()
        settings = load_settings()
        target = now.replace(
            hour=settings["send_hour"],
            minute=settings["send_minute"],
            second=0, microsecond=0
        )
        if now >= target:
            await send_daily_zyablograf()
            target += timedelta(days=1)
        sleep_seconds = (target - datetime.now()).total_seconds()
        if sleep_seconds > 0:
            logger.info(f"Сводка через {sleep_seconds:.0f} сек (в {target.strftime('%H:%M')} МСК)")
            await asyncio.sleep(sleep_seconds)


async def raid_scheduler():
    """Внезапный наезд в случайное время, но только если в настройках raid_enabled=True."""
    while True:
        # Случайная задержка: от 2 до 12 часов
        delay = random.randint(7200, 43200)
        logger.info(f"Следующий наезд через {delay / 3600:.1f} часов")
        await asyncio.sleep(delay)

        settings = load_settings()
        if not settings.get("raid_enabled", True):
            continue

        chats = load_chats()
        if not chats:
            continue

        chat_id = random.choice(chats)
        messages = daily_messages.get(chat_id, [])
        if len(messages) < 10:
            continue  # Недостаточно сообщений для наезда

        await send_raid(chat_id)


# ========== ЗАПУСК ==========
async def main():
    logger.info("Зяблограф запущен!")

    chats = load_chats()
    for cid in chats:
        if cid not in daily_messages:
            daily_messages[cid] = []
    logger.info(f"Чаты: {chats}")

    s = load_settings()
    logger.info(f"Время сводки: {s['send_hour']:02d}:{s['send_minute']:02d}, уровень: {s.get('mood', 'hard').upper()}, наезды: {'вкл' if s.get('raid_enabled', True) else 'выкл'}")

    asyncio.create_task(scheduler())
    asyncio.create_task(raid_scheduler())

    offset = None
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30, allowed_updates=["message"])
            for update in updates:
                if update.message:
                    if update.message.chat.id == ADMIN_ID:
                        await process_admin_command(update)
                    else:
                        await handle_message(update.message)
                offset = update.update_id + 1
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
