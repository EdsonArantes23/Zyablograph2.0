import os
import json
import re
import logging
import asyncio
import random
from datetime import datetime, timedelta, timezone

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
DICT_FILE = "dictionary.json"

groq_client = Groq(api_key=GROQ_API_KEY)
google_client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=BOT_TOKEN)

daily_messages = {}
reactions = {}  # {chat_id: [{"author": str, "text": str}]}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MSK_TZ = timezone(timedelta(hours=3))
BOT_USERNAME = None  # заполним при старте

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
            "существительные": ["пиздец", "хуй", "дебил", "мудак"],
            "глаголы": ["обосрался", "охуел", "заскулил"],
            "прилагательные": ["ебанутый", "конченный"],
            "наречия": ["пиздецки", "дико"],
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

# ========== УМНЫЙ ПОДБОР СЛОВ ==========
def inject_smart_words(prompt: str, chat_id=None) -> str:
    # Вставка реакций, если есть
    react_block = ""
    if chat_id and chat_id in reactions and reactions[chat_id]:
        reacts = reactions[chat_id][-5:]  # последние 5 реакций
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

Выбери 5-8 слов и выражений, которые ИДЕАЛЬНО ПОДХОДЯТ к событиям в чате, и органично вплети их в сводку.

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
        "send_hour": 21, "send_minute": 0, "mood": "hard",
        "raid_enabled": True,
        "raid_min_hours": 2, "raid_max_hours": 12,
        "custom_main_prompt": None, "custom_raid_prompt": None
    })

def save_settings(settings):
    save_json(SETTINGS_FILE, settings)

def get_display_name(user) -> str:
    names = load_names()
    uid = str(user.id)
    if uid in names and "name" in names[uid]:
        return names[uid]["name"]
    return user.first_name or user.username or "Анон"

def get_user_description(user) -> str:
    """Возвращает описание пользователя, если задано."""
    names = load_names()
    uid = str(user.id)
    return names.get(uid, {}).get("description", "")

def resolve_user_id(identifier: str):
    """Принимает ID (число) или @username, возвращает ID или None.
    Ищет в логах всех чатов пользователя с таким username."""
    if identifier.startswith('@'):
        uname = identifier[1:].lower()
        for chat_msgs in daily_messages.values():
            for msg in chat_msgs:
                if msg["author"].lower() == uname:
                    # находим ID по сохранённому user_id? нужно хранить user_id в сообщениях
                    # пока у нас только author-имя, не ID. Поэтому обновим структуру daily_messages.
                    pass
        # сейчас daily_messages не хранит user_id, нужно исправить.
        return None
    else:
        try:
            return int(identifier)
        except ValueError:
            return None

# ========== ОПИСАНИЕ ФОТО ==========
async def describe_photo(file_id: str) -> str:
    try:
        file = await bot.get_file(file_id)
        image_bytes = await file.download_as_bytearray()
        response = google_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                "Опиши подробно, что на фото. Можно с юмором. Только на русском.",
                {"inline_data": {"mime_type": "image/jpeg", "data": image_bytes}}
            ]
        )
        return f"[ФОТО: {response.text.strip()}]"
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")
        return "[ФОТО: не удалось описать]"

# ========== ФИЛЬТРАЦИЯ СООБЩЕНИЙ ==========
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

# ========== СБОРКА ПРОМПТОВ ==========
def build_main_prompt(chat_id=None):
    settings = load_settings()
    custom = settings.get("custom_main_prompt")
    mood_style = get_mood_style(settings.get("mood", "hard"))
    if custom:
        prompt = custom.replace("{mood_style}", mood_style)
    else:
        prompt = DEFAULT_MAIN_PROMPT.replace("{mood_style}", mood_style)
    return inject_smart_words(prompt, chat_id)

def build_raid_prompt(chat_id=None):
    settings = load_settings()
    custom = settings.get("custom_raid_prompt")
    mood_style = get_mood_style(settings.get("mood", "hard"))
    if custom:
        prompt = custom.replace("{mood_style}", mood_style)
    else:
        prompt = DEFAULT_RAID_PROMPT.replace("{mood_style}", mood_style)
    return inject_smart_words(prompt, chat_id)

def generate_zyablograf(chat_log: str, chat_id=None) -> str:
    return _call_groq(build_main_prompt(chat_id) + chat_log, max_tokens=8000)

def generate_raid(chat_log: str, chat_id=None) -> str:
    return _call_groq(build_raid_prompt(chat_id) + chat_log, max_tokens=2000, temperature=1.0)

def _call_groq(full_prompt: str, max_tokens=6000, temperature=0.95) -> str:
    for model in ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b"]:
        try:
            completion = groq_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=temperature, max_tokens=max_tokens
            )
            return clean_output(completion.choices[0].message.content)
        except Exception as e:
            logger.error(f"Ошибка {model}: {e}")
    return "Зяблограф обосрался. Технический пиздец."

def clean_output(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[^\x00-\x7Fа-яА-ЯёЁ0-9\s\.,!?;:()«»""''\-—@#\n]', '', text)
    if "⭐️ Станьте спонсором" in text:
        text = text.split("⭐️ Станьте спонсором")[0].strip()
    text = re.sub(r'#вестник\s*', '', text, flags=re.IGNORECASE).strip()
    return text

def format_for_telegram(text: str) -> str:
    return re.sub(r'#\s*\((https?://t\.me/[^\s\)]+)\)', r'[#](\1)', text)

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def handle_message(message):
    chat_id = message.chat.id
    if chat_id not in load_chats():
        return

    # Проверяем, не ответ ли это боту
    if message.reply_to_message and message.reply_to_message.from_user.id == bot.id:
        author = get_display_name(message.from_user)
        text = message.text or message.caption or "[без текста]"
        if chat_id not in reactions:
            reactions[chat_id] = []
        reactions[chat_id].append({"author": author, "text": text.strip()})
        logger.info(f"Реакция на бота: {author}: {text.strip()}")

    author = get_display_name(message.from_user)
    text = message.text or message.caption or ""
    if not text and getattr(message, "forward_origin", None):
        fo = message.forward_origin
        if hasattr(fo, "sender_user") and fo.sender_user:
            author = f"↪️ {get_display_name(fo.sender_user)}"
        elif hasattr(fo, "chat") and fo.chat:
            author = f"↪️ {fo.chat.title or fo.chat.username or 'Канал'}"
        text = "[пересланное сообщение]"
    if message.photo:
        desc = await describe_photo(message.photo[-1].file_id)
        text = f"{text}\n{desc}" if text else desc
    if not text:
        text = "[войс/стикер/мусор]"

    cid = str(chat_id).replace("-100", "")
    link = f"https://t.me/c/{cid}/{message.message_id}"
    daily_messages.setdefault(chat_id, []).append({
        "link": link,
        "author": author,
        "text": text.strip(),
        "user_id": message.from_user.id  # важно для поиска по ID
    })

# ========== ЕЖЕДНЕВНАЯ СВОДКА ==========
async def send_daily_zyablograf():
    for chat_id in load_chats():
        msgs = daily_messages.get(chat_id, [])
        if not msgs:
            continue
        important = filter_important_messages(msgs, max_to_select=30)
        filtered_log = "\n".join(
            f"[{m['link']}] {m['author']}: {m['text']}" for m in important
        )
        logger.info(f"Дайджест для {chat_id}: {len(msgs)} → {len(important)} сообщений")
        result = generate_zyablograf(filtered_log, chat_id)
        msg = f"{get_greeting()}\n\n{format_for_telegram(result)}"
        await send_safe(chat_id, msg, parse_mode="MarkdownV2")
        daily_messages[chat_id] = []
        reactions[chat_id] = []  # очистка реакций

async def send_raid(chat_id):
    msgs = daily_messages.get(chat_id, [])
    if len(msgs) < 10:
        return
    important = filter_important_messages(msgs, max_to_select=20)
    log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in important)
    result = generate_raid(log, chat_id)
    await send_safe(chat_id, result)

# ========== АДМИНСКИЕ КОМАНДЫ ==========
async def process_admin_command(update):
    text = update.message.text or ""
    if text.startswith("/add_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /add_chat -100XXXXXX"); return
        try:
            c = int(parts[1]); chats = load_chats()
            if c not in chats:
                chats.append(c); save_chats(chats); await send_safe(ADMIN_ID, f"✅ Чат {c} добавлен!")
            else:
                await send_safe(ADMIN_ID, "⚠️ Уже в списке.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID.")

    elif text.startswith("/remove_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /remove_chat -100XXXXXX"); return
        try:
            c = int(parts[1]); chats = load_chats()
            if c in chats:
                chats.remove(c); save_chats(chats); await send_safe(ADMIN_ID, f"✅ Чат {c} удалён.")
            else:
                await send_safe(ADMIN_ID, "⚠️ Не найден.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID.")

    elif text.startswith("/list_chats"):
        chats = load_chats()
        await send_safe(ADMIN_ID, "📋 Чаты:\n" + "\n".join(f"  - {c}" for c in chats) if chats else "📋 Нет чатов.")

    elif text.startswith("/setname") or text.startswith("/setdesc"):
        cmd = "name" if text.startswith("/setname") else "desc"
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await send_safe(ADMIN_ID, f"❌ /{ 'setname' if cmd=='name' else 'setdesc' } user_id/username Значение"); return
        identifier = parts[1].strip()
        value = parts[2].strip()
        # Пытаемся получить ID
        uid = None
        if identifier.startswith('@'):
            # Ищем в логах пользователя с таким username (без @)
            uname = identifier[1:].lower()
            for chat_msgs in daily_messages.values():
                for m in chat_msgs:
                    if m.get("author", "").lower() == uname:
                        uid = str(m.get("user_id"))
                        break
                if uid: break
            if not uid:
                await send_safe(ADMIN_ID, f"❌ Пользователь @{uname} не найден в логах. Пусть напишет что-нибудь в чат.")
                return
        else:
            try:
                uid = str(int(identifier))
            except ValueError:
                await send_safe(ADMIN_ID, "❌ Неверный ID или username.")
                return

        names = load_names()
        if uid not in names:
            names[uid] = {}
        if cmd == "name":
            names[uid]["name"] = value
            await send_safe(ADMIN_ID, f"✅ Прозвище для {uid} установлено: {value}")
        else:
            names[uid]["description"] = value
            await send_safe(ADMIN_ID, f"✅ Описание для {uid} установлено: {value}")
        save_names(names)

    elif text.startswith("/removename") or text.startswith("/removedesc"):
        cmd = "name" if text.startswith("/removename") else "desc"
        key = "name" if cmd == "name" else "description"
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, f"❌ /{ 'removename' if cmd=='name' else 'removedesc' } user_id/username"); return
        identifier = parts[1]
        uid = None
        if identifier.startswith('@'):
            uname = identifier[1:].lower()
            for chat_msgs in daily_messages.values():
                for m in chat_msgs:
                    if m.get("author", "").lower() == uname:
                        uid = str(m.get("user_id"))
                        break
                if uid: break
            if not uid:
                await send_safe(ADMIN_ID, f"❌ Пользователь @{uname} не найден.")
                return
        else:
            try:
                uid = str(int(identifier))
            except ValueError:
                await send_safe(ADMIN_ID, "❌ Неверный ID или username.")
                return
        names = load_names()
        if uid in names and key in names[uid]:
            del names[uid][key]
            if not names[uid]:  # удалить пустую запись
                del names[uid]
            save_names(names)
            await send_safe(ADMIN_ID, f"✅ {key.capitalize()} удалён.")
        else:
            await send_safe(ADMIN_ID, "⚠️ Нечего удалять.")

    elif text.startswith("/list_names"):
        names = load_names()
        if not names:
            await send_safe(ADMIN_ID, "📋 Прозвищ нет.")
            return
        lines = ["📋 Прозвища и описания:"]
        for uid, data in names.items():
            name = data.get("name", "—")
            desc = data.get("description", "—")
            lines.append(f"• `{uid}`: **{name}** — {desc}")
        await send_safe(ADMIN_ID, "\n".join(lines))

    elif text.startswith("/settime"):
        parts = text.split()
        if len(parts) < 2 or not re.match(r'^\d{1,2}:\d{2}$', parts[1]):
            await send_safe(ADMIN_ID, "❌ /settime ЧЧ:ММ (МСК)"); return
        h, m = map(int, parts[1].split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            await send_safe(ADMIN_ID, "❌ 0-23, 0-59."); return
        s = load_settings(); s["send_hour"], s["send_minute"] = h, m; save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Сводка в {h:02d}:{m:02d} МСК")

    elif text.startswith("/mood"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, f"Текущий: {load_settings().get('mood', 'hard')}\nlight, medium, hard, ultra"); return
        mood = parts[1].lower()
        if mood not in MOOD_STYLES:
            await send_safe(ADMIN_ID, "❌ light, medium, hard, ultra"); return
        s = load_settings(); s["mood"] = mood; save_settings(s)
        await send_safe(ADMIN_ID, f"✅ {mood.upper()}")

    elif text.startswith("/raid_timer"):
        parts = text.split()
        if len(parts) == 1:
            s = load_settings()
            mn = s.get("raid_min_hours", 2); mx = s.get("raid_max_hours", 12)
            await send_safe(ADMIN_ID, f"Интервал: от {mn} до {mx} ч.\n/raid_timer МИН МАКС"); return
        if len(parts) < 3:
            await send_safe(ADMIN_ID, "❌ /raid_timer МИН МАКС"); return
        try:
            mn = float(parts[1]); mx = float(parts[2])
            if mn <= 0 or mx <= 0 or mn > mx: raise ValueError
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Положительные, МИН ≤ МАКС."); return
        s = load_settings(); s["raid_min_hours"] = mn; s["raid_max_hours"] = mx; save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Интервал: {mn}-{mx} ч.")

    elif text.startswith("/raid"):
        parts = text.split()
        if len(parts) > 1 and parts[1] in ("on", "off"):
            s = load_settings(); s["raid_enabled"] = parts[1] == "on"; save_settings(s)
            await send_safe(ADMIN_ID, f"✅ Наезды {'ВКЛ' if parts[1] == 'on' else 'ОТКЛ'}.")
        elif len(parts) > 1 and parts[1] == "now":
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
        if not msgs:
            await send_safe(ADMIN_ID, "❌ Нет сообщений."); return
        if len(msgs) > 30:
            msgs = filter_important_messages(msgs, max_to_select=30)
        log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in msgs)
        await send_safe(ADMIN_ID, f"🧪 Сводка ({len(msgs)} сообщений)...")
        result = generate_zyablograf(log, cid)
        await send_safe(ADMIN_ID, f"{get_greeting()}\n\n{format_for_telegram(result)}", parse_mode="MarkdownV2")

    elif text.startswith("/status"):
        s = load_settings()
        lines = ["📊 Статистика:"]
        total = 0
        for cid, msgs in daily_messages.items():
            lines.append(f"  Чат {cid}: {len(msgs)} сообщений")
            total += len(msgs)
        if not daily_messages: lines.append("  Пусто.")
        lines += [
            f"\nВсего: {total}",
            f"Время: {s['send_hour']:02d}:{s['send_minute']:02d} МСК",
            f"Мат: {s.get('mood', 'hard').upper()}",
            f"Словарь: {get_dict_stats()}",
            f"Наезды: {'вкл' if s.get('raid_enabled', True) else 'выкл'}",
            f"Интервал рейдов: {s.get('raid_min_hours', 2)}-{s.get('raid_max_hours', 12)} ч.",
        ]
        await send_safe(ADMIN_ID, "\n".join(lines))

    elif text.startswith("/reset"):
        parts = text.split()
        cid = int(parts[1]) if len(parts) > 1 else None
        if cid:
            daily_messages[cid] = []; reactions[cid] = []
            await send_safe(ADMIN_ID, f"🗑️ Лог чата {cid} сброшен.")
        else:
            daily_messages.clear(); reactions.clear()
            await send_safe(ADMIN_ID, "🗑️ Все логи сброшены.")

    elif text.startswith("/backup"):
        chats = load_chats()
        names = load_names()
        s = load_settings()
        backup = []
        backup.append(f"/add_chat {c}" for c in chats)
        for uid, data in names.items():
            if "name" in data:
                backup.append(f"/setname {uid} {data['name']}")
            if "description" in data:
                backup.append(f"/setdesc {uid} {data['description']}")
        backup.append(f"/settime {s['send_hour']:02d}:{s['send_minute']:02d}")
        backup.append(f"/mood {s.get('mood', 'hard')}")
        backup.append(f"/raid_timer {s.get('raid_min_hours', 2)} {s.get('raid_max_hours', 12)}")
        backup_text = "🛠 Команды для восстановления:\n" + "\n".join(backup)
        await send_safe(ADMIN_ID, backup_text)

    elif text.startswith("/prompt_show"):
        parts = text.split(); pt = parts[1] if len(parts) > 1 else "main"
        if pt not in ("main", "raid"): await send_safe(ADMIN_ID, "❌ main или raid."); return
        s = load_settings()
        key = "custom_main_prompt" if pt == "main" else "custom_raid_prompt"
        await send_safe(ADMIN_ID, f"📝 {pt}: {s.get(key, 'стандартный')}"[:4000])

    elif text.startswith("/prompt_set"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await send_safe(ADMIN_ID, "❌ /prompt_set main/raid ТЕКСТ"); return
        pt, prompt_text = parts[1], parts[2]
        if pt not in ("main", "raid"): await send_safe(ADMIN_ID, "❌ main или raid."); return
        s = load_settings()
        s["custom_main_prompt" if pt == "main" else "custom_raid_prompt"] = prompt_text
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Промпт {pt} установлен!")

    elif text.startswith("/prompt_reset"):
        pt = text.split()[1] if len(text.split()) > 1 else "main"
        if pt not in ("main", "raid"): await send_safe(ADMIN_ID, "❌ main или raid."); return
        s = load_settings()
        s["custom_main_prompt" if pt == "main" else "custom_raid_prompt"] = None
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Промпт {pt} сброшен.")

    elif text.startswith("/help"):
        s = load_settings()
        help_text = f"""
🛠 **ПАМЯТКА АДМИНИСТРАТОРА ЗЯБЛОГРАФА**

**📋 Управление чатами**
• `/add_chat -100XXXXXX` — добавить чат
• `/remove_chat -100XXXXXX` — удалить чат
• `/list_chats` — показать список

**🏷️ Прозвища и описания**
• `/setname ID/@user Прозвище` — задать прозвище
• `/setdesc ID/@user Описание` — добавить описание (например, «главный фембой»)
• `/removename ID/@user` — убрать прозвище
• `/removedesc ID/@user` — убрать описание
• `/list_names` — весь список

**⏰ Время сводки (МСК)**
• `/settime ЧЧ:ММ` (сейчас **{s['send_hour']:02d}:{s['send_minute']:02d}**)

**🔥 Уровень мата**
• `/mood light|medium|hard|ultra` (текущий: **{s.get('mood', 'hard').upper()}**)

**🤬 Наезды**
• `/raid on|off` — вкл/выкл
• `/raid now [чат]` — вручную
• `/raid_timer МИН МАКС` — интервал (сейчас {s.get('raid_min_hours', 2)}-{s.get('raid_max_hours', 12)} ч.)

**🧪 Тестирование и данные**
• `/test [чат] [кол-во]` — тест сводки
• `/status` — статистика
• `/reset [чат]` — сброс логов/реакций
• `/backup` — получить команды для восстановления всех настроек

**📚 Словарь:** {get_dict_stats()}
"""
        await send_safe(ADMIN_ID, help_text)

# ========== ПЛАНИРОВЩИКИ ==========
async def scheduler():
    while True:
        s = load_settings()
        now = msk_now()
        target = now.replace(hour=s["send_hour"], minute=s["send_minute"], second=0, microsecond=0)
        if now >= target:
            await send_daily_zyablograf()
            target += timedelta(days=1)
        sleep_sec = (target - msk_now()).total_seconds()
        if sleep_sec > 0:
            logger.info(f"Сон до {target.strftime('%H:%M')} МСК ({sleep_sec:.0f} с)")
            await asyncio.sleep(sleep_sec)

async def raid_scheduler():
    while True:
        s = load_settings()
        if not s.get("raid_enabled", True):
            await asyncio.sleep(3600)
            continue
        delay = random.randint(int(s.get("raid_min_hours", 2) * 3600), int(s.get("raid_max_hours", 12) * 3600))
        await asyncio.sleep(delay)
        s = load_settings()
        if not s.get("raid_enabled", True): continue
        chats = load_chats()
        if not chats: continue
        cid = random.choice(chats)
        if len(daily_messages.get(cid, [])) >= 10:
            await send_raid(cid)

# ========== ЗАПУСК ==========
async def main():
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username
    logger.info(f"Зяблограф @{BOT_USERNAME} запущен!")
    logger.info(f"Словарь: {get_dict_stats()}")
    for cid in load_chats():
        daily_messages.setdefault(cid, [])
        reactions.setdefault(cid, [])
    asyncio.create_task(scheduler())
    asyncio.create_task(raid_scheduler())
    offset = None
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30, allowed_updates=["message"])
            for u in updates:
                if u.message:
                    if u.message.chat.id == ADMIN_ID:
                        await process_admin_command(u)
                    else:
                        await handle_message(u.message)
                offset = u.update_id + 1
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
