import os
import json
import re
import base64
import logging
import asyncio
import random
from datetime import datetime, timedelta, timezone

from groq import Groq, BadRequestError
from telegram import Bot
from telegram.error import TelegramError

# ========== НАСТРОЙКИ ==========
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "417850992"))

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Токен бота не найден! Укажи его в поле «Токен» на Bothost.")

DICT_FILE = "dictionary.json"
BOT_SETTINGS_KEY = "BOT_SETTINGS"  # Ключ переменной окружения для всех настроек

groq_client = Groq(api_key=GROQ_API_KEY)
bot = Bot(token=BOT_TOKEN)

daily_messages = {}
reactions = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MSK_TZ = timezone(timedelta(hours=3))

def msk_now():
    return datetime.now(MSK_TZ)

# ========== ЗАГРУЗКА СЛОВАРЯ ==========
def load_dictionary():
    try:
        with open(DICT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Словарь не загружен: {e}")
        return {
            "существительные": ["пиздец", "хуй", "дебил"],
            "глаголы": ["обосрался", "охуел"],
            "прилагательные": ["ебанутый"],
            "наречия": ["пиздецки"],
            "эпитеты_для_людей": ["наш местный клоун"],
            "метафоры": ["как слон в посудной лавке"],
            "сравнения": ["тупее, чем пробка"],
            "деепричастные_обороты": ["с грацией пьяного бегемота"]
        }

SWEAR_DICT = load_dictionary()

def get_dict_stats() -> str:
    total = sum(len(v) for v in SWEAR_DICT.values())
    parts = [f"{k}: {len(v)}" for k, v in SWEAR_DICT.items()]
    return f"{total} слов ({', '.join(parts)})"

# ========== ХРАНЕНИЕ НАСТРОЕК В ПЕРЕМЕННОЙ ОКРУЖЕНИЯ ==========
def load_all_data():
    """Загружает все настройки из BOT_SETTINGS (переменная окружения)."""
    raw = os.getenv(BOT_SETTINGS_KEY, "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("BOT_SETTINGS повреждён, начинаю с нуля")
        return {}

def save_all_data(data):
    """Сохраняет все настройки в BOT_SETTINGS."""
    os.environ[BOT_SETTINGS_KEY] = json.dumps(data, ensure_ascii=False)

def load_chats():
    return load_all_data().get("chats", [])

def save_chats(chats):
    data = load_all_data()
    data["chats"] = chats
    save_all_data(data)

def load_names():
    return load_all_data().get("names", {})

def save_names(names):
    data = load_all_data()
    data["names"] = names
    save_all_data(data)

def load_settings():
    defaults = {
        "send_hour": 21, "send_minute": 0, "mood": "hard",
        "raid_enabled": True,
        "raid_min_hours": 2, "raid_max_hours": 12,
        "custom_main_prompt": None, "custom_raid_prompt": None
    }
    stored = load_all_data().get("settings", {})
    # Слияние с дефолтами — если чего-то нет, подставится
    return {**defaults, **stored}

def save_settings(settings):
    data = load_all_data()
    data["settings"] = settings
    save_all_data(data)

def get_display_name(user) -> str:
    names = load_names()
    uid = str(user.id)
    if uid in names and "name" in names[uid]:
        return names[uid]["name"]
    return user.first_name or user.username or "Анон"

# ========== ИНЖЕКЦИЯ СЛОВ ==========
def inject_smart_words(prompt: str, chat_id=None) -> str:
    react_block = ""
    if chat_id and chat_id in reactions and reactions[chat_id]:
        reacts = reactions[chat_id][-5:]
        react_lines = "\n".join(f"• @{r['author']} ответил: «{r['text']}»" for r in reacts)
        react_block = f"\n\n🔥 РЕАКЦИИ НА ПРОШЛЫЕ ВЫПУСКИ:\n{react_lines}\nУпомяни, что ты видел эти ответы, и добавь сарказма."

    swear_nouns = ", ".join(SWEAR_DICT.get("существительные", [])[:40])
    swear_verbs = ", ".join(SWEAR_DICT.get("глаголы", [])[:35])
    swear_adj = ", ".join(SWEAR_DICT.get("прилагательные", [])[:30])
    swear_adv = ", ".join(SWEAR_DICT.get("наречия", [])[:20])
    epithets = ", ".join(SWEAR_DICT.get("эпитеты_для_людей", [])[:20])
    metaphors = ", ".join(SWEAR_DICT.get("метафоры", [])[:15])
    comparisons = ", ".join(SWEAR_DICT.get("сравнения", [])[:15])
    gerunds = ", ".join(SWEAR_DICT.get("деепричастные_обороты", [])[:15])

    injection = f"""
📚 СЛОВАРЬ ЗЯБЛОГРАФА — ПОДБЕРИ СЛОВА ПОД КОНТЕКСТ:
Выбери 5-8 слов и выражений, которые ИДЕАЛЬНО ПОДХОДЯТ к событиям в чате, и органично вплети их в текст.

Существительные: {swear_nouns}
Глаголы: {swear_verbs}
Прилагательные: {swear_adj}
Наречия: {swear_adv}
Эпитеты для людей: {epithets}
Метафоры: {metaphors}
Сравнения: {comparisons}
Деепричастные обороты: {gerunds}
{react_block}
"""
    return prompt.replace("Вот лог чата:", f"{injection}\nВот лог чата:")

# ========== ПРОМПТЫ ==========
DEFAULT_MAIN_PROMPT = """[System note: Ты — «Зяблограф», злобный, циничный обозреватель чата. Ты пишешь ТОЛЬКО на чистом русском языке с матом и сарказмом.] {mood_style}

Перед тобой уже отфильтрованные сообщения чата, содержащие самые важные и горячие моменты за сутки. Составь из них сводку.

ПРАВИЛА:
- Выбери СТОЛЬКО событий, сколько реально достойных тем — не растягивай до 10, если материала мало
- Каждое событие — УНИКАЛЬНАЯ тема и ссылка
- Начинай с # (ссылка) Текст из 3-5 предложений
- Используй мат, сарказм, метафоры, сравнения
- Без подвалов и хештегов

Вот лог:
"""

DEFAULT_RAID_PROMPT = """[System note: Ты — «Зяблограф», врываешься в чат с наездом. Только русский мат.] {mood_style}

Прочитай лог, выбери 1-2 участников, натворивших дичи, и ЖЁСТКО НАЕЗЖАЙ. Начинай с «О, блядь, @username...». Одно сообщение, 4-7 предложений, без ссылок.

Вот лог:
"""

GREETINGS = [
    "📰 Главное из последних сообщений за сутки:",
    "📰 Экстренный выпуск Зяблографа!",
    "📰 Зяблограф проанализировал чат и выбрал самое «достойное»:",
    "📰 Зяблограф: главные события за 24 часа:",
]

MOOD_STYLES = {
    "light": "Сдержанный мат, ирония.",
    "medium": "Умеренный мат в каждом втором-третьем предложении.",
    "hard": "Жёсткий мат почти в каждом предложении, разнообразно.",
    "ultra": "Ультра-жёсткий мат через слово, грязный поток.",
}

def get_greeting() -> str:
    return random.choice(GREETINGS)

def get_mood_style(mood: str) -> str:
    return MOOD_STYLES.get(mood, MOOD_STYLES["hard"])

# ========== ЭКРАНИРОВАНИЕ MARKDOWNV2 ==========
def escape_markdown(text: str) -> str:
    """Экранирует спецсимволы MarkdownV2, кроме уже экранированных."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    # Не экранируем то, что уже экранировано (\_)
    result = []
    i = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text) and text[i+1] in escape_chars:
            result.append(text[i:i+2])
            i += 2
        elif text[i] in escape_chars:
            result.append('\\' + text[i])
            i += 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)

# ========== УМНОЕ ДЕЛЕНИЕ ДЛИННЫХ СООБЩЕНИЙ ==========
def split_by_paragraphs(text: str, max_len: int = 4000) -> list:
    if len(text) <= max_len:
        return [text]
    paragraphs = text.split("\n\n")
    parts = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_len:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                parts.append(current)
                current = ""
            if len(para) > max_len:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sentence in sentences:
                    if len(current) + len(sentence) + 2 <= max_len:
                        current = (current + " " + sentence).strip()
                    else:
                        if current:
                            parts.append(current)
                        current = sentence
            else:
                current = para
    if current:
        parts.append(current)
    return parts

# ========== БЕЗОПАСНАЯ ОТПРАВКА С АВТОЭКРАНИРОВАНИЕМ ==========
async def send_safe(chat_id, text, parse_mode=None, thread_id=1):
    """Отправляет сообщение с автоэкранированием MarkdownV2."""
    try:
        if parse_mode == "MarkdownV2":
            text = escape_markdown(text)
        return await bot.send_message(chat_id, text, parse_mode=parse_mode, message_thread_id=thread_id)
    except TelegramError as e:
        if "message_thread_id" in str(e).lower() or "thread" in str(e).lower():
            logger.warning(f"Ошибка с thread_id, пробую без: {e}")
            try:
                return await bot.send_message(chat_id, text, parse_mode=parse_mode)
            except TelegramError as e2:
                logger.error(f"Ошибка отправки: {e2}")
                return None
        elif parse_mode:
            logger.warning(f"Ошибка Markdown, пробую без форматирования: {e}")
            return await bot.send_message(chat_id, text, message_thread_id=thread_id)
        else:
            raise

# ========== ОПИСАНИЕ ФОТО (GROQ VISION) ==========
async def describe_photo(file_id: str) -> str:
    """Скачивает фото и отправляет в Groq Vision."""
    try:
        file = await bot.get_file(file_id)
        image_bytes = await file.download_as_bytearray()
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        data_url = f"data:image/jpeg;base64,{image_base64}"

        completion = groq_client.chat.completions.create(
            model="llama-3.2-11b-vision-preview",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Опиши подробно, что на фото. Можно с юмором. Только на русском."},
                    {"type": "image_url", "image_url": {"url": data_url}}
                ]
            }],
            temperature=0.7,
            max_tokens=300
        )
        description = completion.choices[0].message.content.strip()
        logger.info(f"Фото описано (Groq): {description[:80]}...")
        return f"[ФОТО: {description}]"
    except BadRequestError as e:
        logger.error(f"BadRequestError Vision: {e}")
        return "[ФОТО: модель временно недоступна]"
    except Exception as e:
        logger.error(f"Ошибка фото Groq: {type(e).__name__}: {e}")
        return "[ФОТО: не удалось описать]"

# ========== ФИЛЬТРАЦИЯ ==========
def filter_important_messages(messages, max_to_select=30):
    important = []
    hot_patterns = [
        r'(?i)\b(?:бля|хуй|пизд|еба|сука|нах|сос|чмо|пидр|гандон|долб|муда|скотин|говн|жоп|сра|сса|перд|дрис)\w*\b',
        r'/\w+', r'@\w+', r'https?://', r'[ФОТО:', r'(?i)\b(?:почему|кто|где|когда|зачем|какого)\b',
    ]
    combined = re.compile('|'.join(hot_patterns))
    for msg in messages:
        if len(important) >= max_to_select:
            break
        if combined.search(msg.get("text", "")):
            important.append(msg)
    if len(important) < 5:
        important = messages[-max_to_select:]
    return important[-max_to_select:]

# ========== СБОРКА ПРОМПТОВ И ГЕНЕРАЦИЯ ==========
def build_main_prompt(chat_id=None):
    s = load_settings()
    custom = s.get("custom_main_prompt")
    mood = get_mood_style(s.get("mood", "hard"))
    prompt = custom.replace("{mood_style}", mood) if custom else DEFAULT_MAIN_PROMPT.replace("{mood_style}", mood)
    return inject_smart_words(prompt, chat_id)

def build_raid_prompt(chat_id=None):
    s = load_settings()
    custom = s.get("custom_raid_prompt")
    mood = get_mood_style(s.get("mood", "hard"))
    prompt = custom.replace("{mood_style}", mood) if custom else DEFAULT_RAID_PROMPT.replace("{mood_style}", mood)
    return inject_smart_words(prompt, chat_id)

def generate_zyablograf(chat_log: str, chat_id=None) -> str:
    prompt = build_main_prompt(chat_id) + chat_log
    return _call_groq(prompt, max_tokens=8000)

def generate_raid(chat_log: str, chat_id=None) -> str:
    prompt = build_raid_prompt(chat_id) + chat_log
    return _call_groq(prompt, max_tokens=2000, temperature=1.0)

def _call_groq(prompt, max_tokens=6000, temperature=0.95):
    """Вызов Groq с экспоненциальным backoff."""
    models = ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b"]
    for model in models:
        for attempt in range(3):
            try:
                completion = groq_client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}],
                    temperature=temperature, max_tokens=max_tokens
                )
                return clean_output(completion.choices[0].message.content)
            except Exception as e:
                wait = 2 ** attempt
                logger.warning(f"Попытка {attempt+1} для {model}: {e}. Жду {wait}с...")
                if attempt < 2:
                    import time
                    time.sleep(wait)
                else:
                    logger.error(f"Модель {model} недоступна после 3 попыток")
        # Пробуем следующую модель
    return "Зяблограф обосрался. Технический пиздец."

def clean_output(text):
    text = text.strip()
    text = re.sub(r'[^\x00-\x7Fа-яА-ЯёЁ0-9\s\.,!?;:()«»""''\-—@#\n]', '', text)
    if "⭐️ Станьте спонсором" in text: text = text.split("⭐️ Станьте спонсором")[0].strip()
    return text

def format_for_telegram(text):
    return re.sub(r'#\s*\((https?://t\.me/[^\s\)]+)\)', r'[#](\1)', text)

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def handle_message(message):
    chat_id = message.chat.id
    if chat_id not in load_chats():
        return

    if message.reply_to_message and message.reply_to_message.from_user.id == bot.id:
        author = get_display_name(message.from_user)
        txt = message.text or message.caption or "[без текста]"
        reactions.setdefault(chat_id, []).append({"author": author, "text": txt.strip()})

    author = get_display_name(message.from_user)
    text = message.text or message.caption or ""
    if not text and getattr(message, "forward_origin", None):
        fo = message.forward_origin
        if hasattr(fo, "sender_user") and fo.sender_user: author = f"↪️ {get_display_name(fo.sender_user)}"
        elif hasattr(fo, "chat") and fo.chat: author = f"↪️ {fo.chat.title or 'Канал'}"
        text = "[пересланное]"
    if message.photo:
        desc = await describe_photo(message.photo[-1].file_id)
        text = f"{text}\n{desc}" if text else desc
    if not text: text = "[войс/стикер]"

    cid = str(chat_id).replace("-100", "")
    link = f"https://t.me/c/{cid}/{message.message_id}"
    daily_messages.setdefault(chat_id, []).append({
        "link": link, "author": author, "text": text.strip(),
        "user_id": message.from_user.id
    })

# ========== ОТПРАВКА ДАЙДЖЕСТОВ И РЕЙДОВ ==========
async def send_daily_zyablograf():
    for chat_id in load_chats():
        msgs = daily_messages.get(chat_id, [])
        if not msgs:
            continue
        important = filter_important_messages(msgs, 30)
        log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in important)
        result = generate_zyablograf(log, chat_id)
        greeting = get_greeting()
        formatted_body = format_for_telegram(result)
        full_text = f"{greeting}\n\n{formatted_body}"

        MAX_MSG_LEN = 4000
        if len(full_text) <= MAX_MSG_LEN:
            await send_safe(chat_id, full_text, parse_mode="MarkdownV2", thread_id=1)
        else:
            logger.info(f"Сводка длинная ({len(full_text)} символов), делю на части...")
            parts = split_by_paragraphs(formatted_body, MAX_MSG_LEN - 100)
            for i, part in enumerate(parts):
                msg = f"{greeting}\n\n{part}" if i == 0 else part
                await send_safe(chat_id, msg, parse_mode="MarkdownV2", thread_id=1)
                if i < len(parts) - 1:
                    await asyncio.sleep(1)

        daily_messages[chat_id] = []
        reactions[chat_id] = []

async def send_raid(chat_id):
    msgs = daily_messages.get(chat_id, [])
    if len(msgs) < 10:
        return
    important = filter_important_messages(msgs, 20)
    log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in important)
    result = generate_raid(log, chat_id)
    await send_safe(chat_id, result, thread_id=1)

# ========== АДМИНСКИЕ КОМАНДЫ ==========
async def process_admin_command(update):
    text = update.message.text or ""
    if text.startswith("/add_chat"):
        parts = text.split()
        if len(parts) < 2: await send_safe(ADMIN_ID, "❌ /add_chat -100XXXXXX"); return
        try:
            c = int(parts[1]); chats = load_chats()
            if c not in chats: chats.append(c); save_chats(chats); await send_safe(ADMIN_ID, f"✅ Чат {c}")
            else: await send_safe(ADMIN_ID, "⚠️ Уже в списке.")
        except ValueError: await send_safe(ADMIN_ID, "❌ Неверный ID.")

    elif text.startswith("/remove_chat"):
        parts = text.split()
        if len(parts) < 2: await send_safe(ADMIN_ID, "❌ /remove_chat -100XXXXXX"); return
        try:
            c = int(parts[1]); chats = load_chats()
            if c in chats: chats.remove(c); save_chats(chats); await send_safe(ADMIN_ID, f"✅ Чат {c} удалён.")
            else: await send_safe(ADMIN_ID, "⚠️ Не найден.")
        except ValueError: await send_safe(ADMIN_ID, "❌ Неверный ID.")

    elif text.startswith("/list_chats"): await send_safe(ADMIN_ID, "📋 Чаты:\n" + "\n".join(f"  - {c}" for c in load_chats()))

    elif text.startswith("/settime"):
        parts = text.split()
        if len(parts) < 2 or not re.match(r'^\d{1,2}:\d{2}$', parts[1]): await send_safe(ADMIN_ID, "❌ /settime ЧЧ:ММ"); return
        h, m = map(int, parts[1].split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59): await send_safe(ADMIN_ID, "❌ 0-23, 0-59."); return
        s = load_settings(); s["send_hour"], s["send_minute"] = h, m; save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Сводка в {h:02d}:{m:02d} МСК")

    elif text.startswith("/mood"):
        parts = text.split()
        if len(parts) < 2: await send_safe(ADMIN_ID, f"Текущий: {load_settings().get('mood', 'hard')}\nlight/medium/hard/ultra"); return
        mood = parts[1].lower()
        if mood not in MOOD_STYLES: await send_safe(ADMIN_ID, "❌ light, medium, hard, ultra"); return
        s = load_settings(); s["mood"] = mood; save_settings(s)
        await send_safe(ADMIN_ID, f"✅ {mood.upper()}")

    elif text.startswith("/raid_timer"):
        parts = text.split()
        if len(parts) < 3: await send_safe(ADMIN_ID, "❌ /raid_timer МИН МАКС"); return
        try:
            mn, mx = float(parts[1]), float(parts[2])
            if mn <= 0 or mx <= 0 or mn > mx: raise ValueError
        except ValueError: await send_safe(ADMIN_ID, "❌ Положительные, МИН ≤ МАКС."); return
        s = load_settings(); s["raid_min_hours"], s["raid_max_hours"] = mn, mx; save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Интервал рейдов: {mn}-{mx} ч.")

    elif text.startswith("/raid"):
        parts = text.split()
        if len(parts) > 1 and parts[1] == "now":
            cid = int(parts[2]) if len(parts) > 2 else (load_chats() or [None])[0]
            if not cid: await send_safe(ADMIN_ID, "❌ Нет чатов."); return
            await send_raid(cid); await send_safe(ADMIN_ID, f"🤬 Рейд в {cid}!")
        else:
            s = load_settings()
            await send_safe(ADMIN_ID, f"Наезды: {'вкл' if s.get('raid_enabled', True) else 'выкл'}\n/raid on|off|now")

    elif text.startswith("/test"):
        parts = text.split()
        cid = int(parts[1]) if len(parts) > 1 else (load_chats() or [None])[0]
        cnt = int(parts[2]) if len(parts) > 2 else 10
        if not cid: await send_safe(ADMIN_ID, "❌ Нет чатов."); return
        msgs = daily_messages.get(cid, [])[-min(cnt, len(daily_messages.get(cid, []))):]
        if not msgs: await send_safe(ADMIN_ID, "❌ Нет сообщений."); return
        if len(msgs) > 30: msgs = filter_important_messages(msgs, 30)
        log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in msgs)
        await send_safe(ADMIN_ID, f"🧪 Сводка ({len(msgs)} сообщений)...")
        result = generate_zyablograf(log, cid)
        greeting = get_greeting()
        formatted = format_for_telegram(result)
        full = f"{greeting}\n\n{formatted}"
        if len(full) <= 4000:
            await send_safe(ADMIN_ID, full, parse_mode="MarkdownV2")
        else:
            parts = split_by_paragraphs(formatted, 3900)
            for i, part in enumerate(parts):
                msg = f"{greeting}\n\n{part}" if i == 0 else part
                await send_safe(ADMIN_ID, msg, parse_mode="MarkdownV2")
                if i < len(parts) - 1:
                    await asyncio.sleep(1)

    elif text.startswith("/status"):
        s = load_settings()
        lines = ["📊 Статистика:"]
        total = 0
        for cid, msgs in daily_messages.items():
            lines.append(f"  Чат {cid}: {len(msgs)} сообщений"); total += len(msgs)
        if not daily_messages: lines.append("  Пусто.")
        lines += [f"\nВсего: {total}", f"Время: {s['send_hour']:02d}:{s['send_minute']:02d} МСК", f"Мат: {s.get('mood', 'hard').upper()}", f"Словарь: {get_dict_stats()}", f"Наезды: {'вкл' if s.get('raid_enabled', True) else 'выкл'}"]
        await send_safe(ADMIN_ID, "\n".join(lines))

    elif text.startswith("/reset"):
        parts = text.split()
        cid = int(parts[1]) if len(parts) > 1 else None
        if cid: daily_messages[cid] = []; reactions[cid] = []
        else: daily_messages.clear(); reactions.clear()
        await send_safe(ADMIN_ID, "🗑️ Сброшено.")

    elif text.startswith("/backup"):
        chats, names, s = load_chats(), load_names(), load_settings()
        cmds = []
        for c in chats: cmds.append(f"/add_chat {c}")
        for uid, data in names.items():
            if "name" in data: cmds.append(f"/setname {uid} {data['name']}")
            if "description" in data: cmds.append(f"/setdesc {uid} {data['description']}")
        cmds.append(f"/settime {s['send_hour']:02d}:{s['send_minute']:02d}")
        cmds.append(f"/mood {s.get('mood', 'hard')}")
        await send_safe(ADMIN_ID, "🛠 Команды восстановления:\n" + "\n".join(cmds))

    elif text.startswith("/help"):
        s = load_settings()
        await send_safe(ADMIN_ID, f"""🛠 ЗЯБЛОГРАФ
📝 /prompt_show|set|reset main|raid
📋 /add_chat|remove_chat|list_chats
🏷️ /setname|removename|list_names
⏰ /settime ЧЧ:ММ (МСК) ({s['send_hour']:02d}:{s['send_minute']:02d})
🔥 /mood light|medium|hard|ultra
🤬 /raid on|off|now
🕒 /raid_timer МИН МАКС
🧪 /test [чат] [кол-во]
/status | /reset | /backup
📚 {get_dict_stats()}""")

# ========== ПЛАНИРОВЩИКИ ==========
async def scheduler():
    while True:
        s = load_settings()
        now = msk_now()
        target = now.replace(hour=s["send_hour"], minute=s["send_minute"], second=0, microsecond=0)
        if now >= target:
            logger.info(f"Дайджест запущен ({target.strftime('%H:%M')} МСК прошло)")
            await send_daily_zyablograf()
            target += timedelta(days=1)
        sleep_sec = (target - msk_now()).total_seconds()
        if sleep_sec > 0:
            logger.info(f"Сон до {target.strftime('%H:%M')} МСК ({sleep_sec:.0f} с)")
            await asyncio.sleep(sleep_sec)

async def raid_scheduler():
    while True:
        s = load_settings()
        if not s.get("raid_enabled", True): await asyncio.sleep(600); continue
        delay = random.randint(int(s.get("raid_min_hours", 2) * 3600), int(s.get("raid_max_hours", 12) * 3600))
        await asyncio.sleep(delay)
        chats = load_chats()
        if chats and len(daily_messages.get(chats[0], [])) >= 10: await send_raid(chats[0])

# ========== ЗАПУСК ==========
async def main():
    logger.info("Зяблограф запущен!")
    for cid in load_chats(): daily_messages.setdefault(cid, []); reactions.setdefault(cid, [])
    asyncio.create_task(scheduler())
    asyncio.create_task(raid_scheduler())
    offset = None
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30, allowed_updates=["message"])
            for u in updates:
                if u.message:
                    if u.message.chat.id == ADMIN_ID: await process_admin_command(u)
                    else: await handle_message(u.message)
                offset = u.update_id + 1
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
