import os
import json
import re
import base64
import logging
import asyncio
import random
from datetime import datetime, timedelta, timezone
from openai import OpenAI
from telegram import Bot
from telegram.error import TelegramError

# ========== НАСТРОЙКИ ==========
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "417850992"))
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Токен бота не найден! Укажи его в поле «Токен» на Bothost.")
if not OPENROUTER_API_KEY:
    raise RuntimeError("Укажи OPENROUTER_API_KEY в Bothost!")

DICT_FILE = "dictionary.json"
MESSAGES_FILE = "daily_messages.json"
BOT_SETTINGS_KEY = "BOT_SETTINGS"

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
bot = Bot(token=BOT_TOKEN)

daily_messages: dict[int, list[dict]] = {}
reactions: dict[int, list[dict]] = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
MSK_TZ = timezone(timedelta(hours=3))

def msk_now() -> datetime:
    return datetime.now(MSK_TZ)

# ========== СОХРАНЕНИЕ СООБЩЕНИЙ НА ДИСК ==========
def save_messages_to_disk() -> None:
    try:
        data = {
            "messages": {str(k): v for k, v in daily_messages.items()},
            "reactions": {str(k): v for k, v in reactions.items()},
        }
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения сообщений на диск: {e}")

def load_messages_from_disk() -> None:
    global daily_messages, reactions
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        daily_messages = {int(k): v for k, v in data.get("messages", {}).items()}
        reactions = {int(k): v for k, v in data.get("reactions", {}).items()}
        total = sum(len(v) for v in daily_messages.values())
        logger.info(f"Восстановлено {total} сообщений с диска.")
    except FileNotFoundError:
        logger.info("Файл сообщений не найден — начинаем с нуля.")
    except Exception as e:
        logger.warning(f"Не удалось загрузить сообщения с диска: {e}")

# ========== ЗАГРУЗКА СЛОВАРЯ ==========
def load_dictionary() -> dict:
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
            "деепричастные_обороты": ["с грацией пьяного бегемота"],
        }

SWEAR_DICT: dict = load_dictionary()

def get_dict_stats() -> str:
    total = sum(len(v) for v in SWEAR_DICT.values())
    parts = [f"{k}: {len(v)}" for k, v in SWEAR_DICT.items()]
    return f"{total} слов ({', '.join(parts)})"

# ========== ХРАНЕНИЕ НАСТРОЕК ==========
def load_all_data() -> dict:
    raw = os.getenv(BOT_SETTINGS_KEY, "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("BOT_SETTINGS повреждён, начинаю с нуля")
        return {}

def save_all_data(data: dict) -> None:
    os.environ[BOT_SETTINGS_KEY] = json.dumps(data, ensure_ascii=False)

def load_chats() -> list[int]: return load_all_data().get("chats", [])
def save_chats(chats: list[int]) -> None:
    data = load_all_data(); data["chats"] = chats; save_all_data(data)

def load_names() -> dict: return load_all_data().get("names", {})
def save_names(names: dict) -> None:
    data = load_all_data(); data["names"] = names; save_all_data(data)

def load_settings() -> dict:
    defaults = {
        "mood": "hard",
        "raid_enabled": True,
        "raid_min_hours": 2,
        "raid_max_hours": 12,
        "custom_main_prompt": None,
        "custom_raid_prompt": None,
    }
    stored = load_all_data().get("settings", {})
    return {**defaults, **stored}

def save_settings(settings: dict) -> None:
    data = load_all_data(); data["settings"] = settings; save_all_data(data)

def get_display_name(user) -> str:
    names = load_names()
    uid = str(user.id)
    if uid in names and "name" in names[uid]:
        return names[uid]["name"]
    return user.first_name or user.username or "Анон"

# ========== ИНЖЕКЦИЯ СЛОВ ==========
def inject_smart_words(prompt: str, chat_id: int | None = None) -> str:
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
DEFAULT_MAIN_PROMPT = """[System note: Ты — «Зяблограф», циничный обозреватель чата. Пишешь ТОЛЬКО на чистом русском с матом и сарказмом.]
{mood_style}
🔥 ЖЁСТКИЕ ПРАВИЛА (НЕ НАРУШАТЬ):
1. СЕМАНТИЧЕСКАЯ ГРУППИРОВКА: Если сообщения обсуждают одну тему/событие (даже разными словами) — это ОДНО событие. Не разбивай.
2. БЕЗ ДУБЛЕЙ: Каждую тему упоминай ровно один раз. Даже если тема всплывает в 20 сообщениях — объедини в один пункт.
3. ФОРМАТ: Каждое событие начинается СТРОГО с # (ссылка) — ссылка в круглых скобках, без пробела после #.
4. ЯЗЫК: ТОЛЬКО РУССКИЙ. Иероглифы, пиньинь, латиница (кроме ссылок/мата) — ЗАПРЕЩЕНЫ.
5. ОБЪЁМ: 1 тема = 2-4 предложения. Не выдумывай. Если тем мало — пиши сколько есть.
Вот лог чата:
"""

DEFAULT_RAID_PROMPT = """[System note: Ты — «Зяблограф», врываешься в чат с наездом. Только русский мат.]
{mood_style}
Прочитай лог, выбери 1-2 участников, натворивших дичи, и ЖЁСТКО НАЕЗЖАЙ.
ПРАВИЛА:
1. НЕ ПОВТОРЯЙ ТЕМЫ. Выбери главных «героев» и сосредоточься на их косяках. Не распыляйся.
2. Начинай с «О, блядь, @username...». Одно сообщение, 4-7 предложений, без ссылок.
3. ТОЛЬКО РУССКИЙ. Иероглифы и латиница (кроме ссылок/мата) — ЗАПРЕЩЕНЫ.
Вот лог чата:
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

def get_greeting() -> str: return random.choice(GREETINGS)
def get_mood_style(mood: str) -> str: return MOOD_STYLES.get(mood, MOOD_STYLES["hard"])

# ========== ЭКРАНИРОВАНИЕ MARKDOWNV2 ==========
def escape_markdown(text: str) -> str:
    escape_chars = r'_*~`>#+-=|{}.!'
    link_pattern = re.compile(r'([.?](https?://[^)]+))')
    parts = link_pattern.split(text)
    result = []
    for part in parts:
        if link_pattern.match(part):
            result.append(part)
        else:
            buf = []
            i = 0
            while i < len(part):
                if part[i] == '\\' and i + 1 < len(part) and part[i + 1] in escape_chars:
                    buf.append(part[i:i + 2])
                    i += 2
                elif part[i] in escape_chars:
                    buf.append('\\' + part[i])
                    i += 1
                else:
                    buf.append(part[i])
                    i += 1
            result.append(''.join(buf))
    return ''.join(result)

# ========== УМНОЕ ДЕЛЕНИЕ ДЛИННЫХ СООБЩЕНИЙ ==========
def split_by_paragraphs(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len: return [text]
    paragraphs = text.split("\n\n")
    parts = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_len:
            current = (current + "\n\n" + para).strip()
        else:
            if current: parts.append(current)
            current = ""
            if len(para) > max_len:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sentence in sentences:
                    if len(current) + len(sentence) + 2 <= max_len:
                        current = (current + "  " + sentence).strip()
                    else:
                        if current: parts.append(current)
                        current = sentence
            else:
                current = para
    if current: parts.append(current)
    return parts

# ========== БЕЗОПАСНАЯ ОТПРАВКА ==========
async def send_safe(chat_id: int, text: str, parse_mode: str | None = None, thread_id: int = 1):
    try:
        if parse_mode == "MarkdownV2":
            text = escape_markdown(text)
        return await bot.send_message(chat_id, text, parse_mode=parse_mode, message_thread_id=thread_id)
    except TelegramError as e:
        err = str(e).lower()
        if "thread" in err or "message_thread_id" in err:
            try: return await bot.send_message(chat_id, text, parse_mode=parse_mode)
            except: return None
        elif parse_mode:
            try: return await bot.send_message(chat_id, text, message_thread_id=thread_id)
            except: return None
        return None

# ========== ОПИСАНИЕ ФОТО (VISION) ==========
async def describe_photo(file_id: str) -> str:
    try:
        file = await bot.get_file(file_id)
        image_bytes = await file.download_as_bytearray()
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
        data_url = f"data:image/jpeg;base64,{image_base64}"
        
        loop = asyncio.get_event_loop()
        completion = await loop.run_in_executor(None, lambda: client.chat.completions.create(
            model="qwen/qwen-2.5-vl-72b-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Опиши подробно, что на фото. Можно с юмором. Только на русском."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            }],
            temperature=0.7, max_tokens=300,
        ))
        desc = completion.choices[0].message.content.strip()
        return f"[ФОТО: {desc}]"
    except Exception as e:
        logger.error(f"Ошибка фото Vision: {e}")
        return "[ФОТО: не удалось описать]"

# ========== ФИЛЬТРАЦИЯ ==========
def filter_important_messages(messages: list[dict], max_to_select: int = 30) -> list[dict]:
    important = []
    hot_words = [r'\b(?:бля|хуй|пизд|еба|сука|нах|сос|чмо|пидр|гандон|долб|муда|скотин|говн|жоп|сра|сса|перд|дрис)\w*\b']
    hot_patterns = [r'/\w+', r'@\w+', r'https?://', r'\[ФОТО:', r'\b(?:почему|кто|где|когда|зачем|какого)\b']
    combined = re.compile('|'.join(hot_words + hot_patterns), re.IGNORECASE)
    for msg in messages:
        if len(important) >= max_to_select: break
        if combined.search(msg.get("text", "")): important.append(msg)
    if len(important) < 5: important = messages[-max_to_select:]
    return important[-max_to_select:]

# ========== СБОРКА ПРОМПТОВ И ГЕНЕРАЦИЯ ==========
def build_main_prompt(chat_id: int | None = None) -> str:
    s = load_settings()
    custom = s.get("custom_main_prompt")
    mood = get_mood_style(s.get("mood", "hard"))
    prompt = custom.replace("{mood_style}", mood) if custom else DEFAULT_MAIN_PROMPT.replace("{mood_style}", mood)
    return inject_smart_words(prompt, chat_id)

def build_raid_prompt(chat_id: int | None = None) -> str:
    s = load_settings()
    custom = s.get("custom_raid_prompt")
    mood = get_mood_style(s.get("mood", "hard"))
    prompt = custom.replace("{mood_style}", mood) if custom else DEFAULT_RAID_PROMPT.replace("{mood_style}", mood)
    return inject_smart_words(prompt, chat_id)

async def _call_llm(prompt: str, max_tokens: int = 6000, temperature: float = 0.95) -> str:
    model = "qwen/qwen-2.5-72b-instruct"
    loop = asyncio.get_event_loop()
    for attempt in range(3):
        try:
            completion = await loop.run_in_executor(None, lambda: client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}],
                temperature=temperature, max_tokens=max_tokens,
            ))
            return clean_output(completion.choices[0].message.content)
        except Exception as e:
            err = str(e).lower()
            if "rate_limit" in err or "429" in err:
                await asyncio.sleep(10 * (attempt + 1))
                continue
            await asyncio.sleep(2)
    return "Зяблограф обосрался. Технический пиздец."

async def generate_zyablograf(chat_log: str, chat_id: int | None = None) -> str:
    prompt = build_main_prompt(chat_id) + chat_log
    return await _call_llm(prompt, max_tokens=8000)

async def generate_raid(chat_log: str, chat_id: int | None = None) -> str:
    prompt = build_raid_prompt(chat_id) + chat_log
    return await _call_llm(prompt, max_tokens=2000, temperature=1.0)

def clean_output(text: str) -> str:
    text = text.strip()
    # Строгая фильтрация: оставляем только кириллицу, латиницу (для ссылок/мата), цифры, базовую пунктуацию
    text = re.sub(r'[^\x00-\x7Fа-яА-ЯёЁ0-9\s.,!?;:()«»""''-—@#\n\r]', '', text)
    # Явный вырез иероглифов/азиатских символов
    text = re.sub(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+', '', text)
    text = re.sub(r'#\s+(https?://t.me/\S+)', r'# (\1)', text)
    if "⭐️ Станьте спонсором" in text:
        text = text.split("⭐️ Станьте спонсором")[0].strip()
    return text

def format_for_telegram(text: str) -> str:
    return re.sub(r'#\s*((https?://t.me/[^\s)]+))', r'[#](\1)', text)

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def handle_message(message) -> None:
    chat_id = message.chat.id
    if chat_id not in load_chats(): return
    if message.from_user is None: return

    if (message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot.id):
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
        "user_id": message.from_user.id,
        "timestamp": msk_now().isoformat()
    })
    save_messages_to_disk()

    # Условие 1: 1000 сообщений
    if len(daily_messages[chat_id]) >= 1000:
        logger.info(f"Чат {chat_id}: 1000 сообщений → дайджест")
        await _send_digest_for_chat(chat_id)

# ========== ОТПРАВКА ДАЙДЖЕСТОВ ==========
async def _send_digest_for_chat(chat_id: int) -> None:
    msgs = daily_messages.get(chat_id, [])
    if len(msgs) < 5: return
    important = filter_important_messages(msgs, 30)
    log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in important)
    result = await generate_zyablograf(log, chat_id)
    greeting = get_greeting()
    formatted = format_for_telegram(result)
    full = f"{greeting}\n\n{formatted}"
    MAX = 4000
    if len(full) <= MAX:
        await send_safe(chat_id, full, parse_mode="MarkdownV2", thread_id=1)
    else:
        parts = split_by_paragraphs(formatted, MAX - 100)
        for i, p in enumerate(parts):
            await send_safe(chat_id, f"{greeting}\n\n{p}" if i==0 else p, parse_mode="MarkdownV2", thread_id=1)
            if i < len(parts)-1: await asyncio.sleep(1)
    # Очистка буфера
    daily_messages[chat_id] = []
    reactions[chat_id] = []
    save_messages_to_disk()

# Фоновая проверка 24 часов (если чат затих)
async def digest_periodic_checker() -> None:
    while True:
        await asyncio.sleep(300)  # Проверка каждые 5 минут
        for chat_id in list(daily_messages.keys()):
            msgs = daily_messages.get(chat_id, [])
            if not msgs: continue
            first_ts = msgs[0].get("timestamp")
            if first_ts:
                first_time = datetime.fromisoformat(first_ts)
                if (msk_now() - first_time).total_seconds() >= 24 * 3600:
                    logger.info(f"Чат {chat_id}: прошло 24ч → дайджест")
                    await _send_digest_for_chat(chat_id)

# ========== РЕЙДЫ ==========
async def send_raid(chat_id: int) -> None:
    msgs = daily_messages.get(chat_id, [])
    if len(msgs) < 10: return
    important = filter_important_messages(msgs, 20)
    log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in important)
    result = await generate_raid(log, chat_id)
    await send_safe(chat_id, result, thread_id=1)

async def raid_scheduler() -> None:
    while True:
        s = load_settings()
        if not s.get("raid_enabled", True):
            await asyncio.sleep(600)
            continue
        delay = random.randint(
            int(s.get("raid_min_hours", 2) * 3600),
            int(s.get("raid_max_hours", 12) * 3600),
        )
        await asyncio.sleep(delay)
        chats = load_chats()
        if chats and len(daily_messages.get(chats[0], [])) >= 10:
            await send_raid(chats[0])

# ========== АДМИНСКИЕ КОМАНДЫ ==========
async def process_admin_command(update) -> None:
    text = update.message.text or ""
    if text.startswith("/add_chat"):
        parts = text.split()
        if len(parts) < 2: await send_safe(ADMIN_ID, "❌ /add_chat -100XXXXXX"); return
        try:
            c = int(parts[1]); chats = load_chats()
            if c not in chats: chats.append(c); save_chats(chats); await send_safe(ADMIN_ID, f"✅ Чат {c} добавлен!")
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

    elif text.startswith("/list_chats"):
        chats = load_chats()
        await send_safe(ADMIN_ID, "📋 Чаты:\n" + "\n".join(f"  - {c}" for c in chats) if chats else "📋 Нет чатов.")

    elif text.startswith("/mood"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, f"Текущий: {load_settings().get('mood', 'hard')}\nlight/medium/hard/ultra")
            return
        mood = parts[1].lower()
        if mood in MOOD_STYLES:
            s = load_settings(); s["mood"] = mood; save_settings(s)
            await send_safe(ADMIN_ID, f"✅ {mood.upper()}")
        else: await send_safe(ADMIN_ID, "❌ light, medium, hard, ultra")

    elif text.startswith("/raid_timer"):
        parts = text.split()
        if len(parts) < 3: await send_safe(ADMIN_ID, "❌ /raid_timer МИН МАКС"); return
        try:
            mn, mx = float(parts[1]), float(parts[2])
            if mn <= 0 or mx <= 0 or mn > mx: raise ValueError
            s = load_settings(); s["raid_min_hours"], s["raid_max_hours"] = mn, mx; save_settings(s)
            await send_safe(ADMIN_ID, f"✅ Интервал рейдов: {mn}-{mx} ч.")
        except ValueError: await send_safe(ADMIN_ID, "❌ Положительные, МИН ≤ МАКС.")

    elif text.startswith("/raid"):
        parts = text.split()
        if len(parts) > 1 and parts[1] == "now":
            cid = int(parts[2]) if len(parts) > 2 else (load_chats() or [None])[0]
            if cid: await send_raid(cid); await send_safe(ADMIN_ID, f"🤬 Рейд в {cid}!")
            else: await send_safe(ADMIN_ID, "❌ Нет чатов.")
        else:
            s = load_settings()
            await send_safe(ADMIN_ID, f"Наезды: {'вкл' if s.get('raid_enabled', True) else 'выкл'}\n/raid on|off|now")

    elif text.startswith("/test"):
        parts = text.split()
        cid = int(parts[1]) if len(parts) > 1 else (load_chats() or [None])[0]
        cnt = int(parts[2]) if len(parts) > 2 else 10
        if not cid: await send_safe(ADMIN_ID, "❌ Нет чатов."); return
        msgs = daily_messages.get(cid, [])
        if len(msgs) < 5:
            await send_safe(ADMIN_ID, f"❌ Всего {len(msgs)} сообщений. Нужно минимум 5.")
            return
        msgs = msgs[-min(cnt, len(msgs))]
        if len(msgs) > 30: msgs = filter_important_messages(msgs, 30)
        log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in msgs)
        await send_safe(ADMIN_ID, f"🧪 Генерирую сводку по {len(msgs)} сообщениям...")
        result = await generate_zyablograf(log, cid)
        greeting = get_greeting(); formatted = format_for_telegram(result)
        full = f"{greeting}\n\n{formatted}"
        if len(full) <= 4000: await send_safe(ADMIN_ID, full, parse_mode="MarkdownV2")
        else:
            test_parts = split_by_paragraphs(formatted, 3900)
            for i, part in enumerate(test_parts):
                await send_safe(ADMIN_ID, f"{greeting}\n\n{part}" if i==0 else part, parse_mode="MarkdownV2")
                if i < len(test_parts)-1: await asyncio.sleep(1)

    elif text.startswith("/status"):
        s = load_settings(); lines = ["📊 Статистика:"]
        total = sum(len(v) for v in daily_messages.values())
        lines.append(f"Всего сообщений: {total}")
        lines += [f"Мат: {s.get('mood', 'hard').upper()}", f"Словарь: {get_dict_stats()}", f"Наезды: {'вкл' if s.get('raid_enabled', True) else 'выкл'}"]
        await send_safe(ADMIN_ID, "\n".join(lines))

    elif text.startswith("/reset"):
        parts = text.split(); cid = int(parts[1]) if len(parts) > 1 else None
        if cid: daily_messages[cid] = []; reactions[cid] = []
        else: daily_messages.clear(); reactions.clear()
        save_messages_to_disk(); await send_safe(ADMIN_ID, "🗑️ Сброшено.")

    elif text.startswith("/help"):
        s = load_settings()
        await send_safe(ADMIN_ID, f"""🛠 ЗЯБЛОГРАФ
📋 /add_chat|remove_chat|list_chats
🔥 /mood light|medium|hard|ultra
🤬 /raid on|off|now | /raid_timer МИН МАКС
🧪 /test [чат] [кол-во]
/status | /reset
📚 {get_dict_stats()}
⚡ Триггеры: 1000 сообщений ИЛИ 24 часа""")

# ========== ЗАПУСК ==========
async def main() -> None:
    logger.info("Зяблограф запущен!")
    await bot.initialize()
    load_messages_from_disk()
    for cid in load_chats():
        daily_messages.setdefault(cid, [])
        reactions.setdefault(cid, [])
    
    asyncio.create_task(digest_periodic_checker())
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
