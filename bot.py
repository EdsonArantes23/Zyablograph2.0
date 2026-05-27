#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ЗЯБЛОГРАФ — циничный хроникёр чата в стиле сатирического дайджеста
"""

import os, sys, json, re, base64, logging, asyncio, random, time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any

from openai import OpenAI, APIError, RateLimitError, AuthenticationError, APIConnectionError
from telegram import Bot, Update, Message
from telegram.error import TelegramError, BadRequest, Forbidden, NetworkError
from telegram.ext import filters

# ========== КОНФИГУРАЦИЯ ==========
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "417850992"))
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("❌ Укажи BOT_TOKEN в переменных окружения Bothost!")
if not OPENROUTER_API_KEY:
    raise RuntimeError("❌ Укажи OPENROUTER_API_KEY в переменных окружения Bothost!")

# Файлы данных
DICT_FILE = "dictionary.json"
MESSAGES_FILE = "daily_messages.json"
BOT_SETTINGS_KEY = "BOT_SETTINGS"

# Настройки LLM
LLM_MODELS = [
    "qwen/qwen-2.5-72b-instruct:free",
    "qwen/qwen-2.5-32b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "google/gemma-2-9b-it:free"
]
LLM_MAX_TOKENS = 4096
LLM_TEMPERATURE = 0.95
LLM_TIMEOUT = 120

# Настройки дайджеста
DIGEST_TRIGGER_MESSAGES = 1000
DIGEST_TRIGGER_HOURS = 24
DIGEST_MIN_MESSAGES = 5

# Настройки рейдов
RAID_ENABLED_DEFAULT = True
RAID_MIN_HOURS = 2
RAID_MAX_HOURS = 12

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("zyablograf.log", encoding="utf-8", mode="a"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Zyablograf")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    timeout=LLM_TIMEOUT
)
bot = Bot(token=BOT_TOKEN)

# Глобальные хранилища
daily_messages: Dict[int, List[Dict]] = {}
reactions: Dict[int, List[Dict]] = {}
digest_sent_today: Dict[int, datetime.date] = {}
last_raid_time: Dict[int, datetime] = {}

# Часовой пояс МСК
MSK_TZ = timezone(timedelta(hours=3))

def msk_now() -> datetime:
    """Текущее время в Москве"""
    return datetime.now(MSK_TZ)

# ========== СОХРАНЕНИЕ ДАННЫХ ==========
def save_messages_to_disk() -> None:
    """Сохраняет сообщения и реакции на диск"""
    try:
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "messages": {str(k): v for k, v in daily_messages.items()},
                "reactions": {str(k): v for k, v in reactions.items()}
            }, f, ensure_ascii=False, indent=2)
        logger.debug(f"✓ Saved {len(daily_messages)} chats to disk")
    except Exception as e:
        logger.error(f"✗ Save error: {e}", exc_info=True)

def load_messages_from_disk() -> None:
    """Загружает сообщения и реакции с диска"""
    global daily_messages, reactions
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        daily_messages = {int(k): v for k, v in data.get("messages", {}).items()}
        reactions = {int(k): v for k, v in data.get("reactions", {}).items()}
        logger.info(f"✓ Loaded {len(daily_messages)} chats from disk")
    except FileNotFoundError:
        logger.info("ℹ Starting fresh - no saved messages")
    except Exception as e:
        logger.error(f"✗ Load error: {e}", exc_info=True)

# ========== СЛОВАРЬ ==========
def load_dictionary() -> Dict[str, List[str]]:
    """Загружает словарь экспрессивной лексики"""
    try:
        with open(DICT_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # strip ключей и значений для надёжности
        return {k.strip(): [v.strip() for v in vals] for k, vals in raw.items()}
    except Exception as e:
        logger.warning(f"⚠ Dict load error: {e}, using fallback")
        return {
            "существительные": ["пиздец", "хуй", "дебил", "кринж", "треш"],
            "глаголы": ["обосрался", "охуел", "заебал", "потроллил", "разъебал"],
            "прилагательные": ["ебанутый", "отбитый", "конченный", "хуёвый", "сраный"],
            "наречия": ["пиздецки", "хуёво", "дико", "люто", "бешено"],
            "эпитеты_для_людей": ["великовозрастный детина", "комнатный стратег", "диванный эксперт"],
            "метафоры": ["как слон в посудной лавке", "как пьяный голубь", "как рыба об лёд"],
            "сравнения": ["быстрее, чем слухи в женском коллективе", "тупее, чем пробка от графина"],
            "деепричастные_обороты": ["брызгая слюной от ярости", "захлёбываясь в собственной важности"]
        }

SWEAR_DICT = load_dictionary()

# ========== НАСТРОЙКИ БОТА ==========
def load_all_data() -> Dict:
    """Загружает все настройки из env"""
    try:
        return json.loads(os.getenv(BOT_SETTINGS_KEY, "{}"))
    except Exception as e:
        logger.warning(f"⚠ Settings load error: {e}")
        return {}

def save_all_data(data: Dict) -> None:
    """Сохраняет настройки в env"""
    try:
        os.environ[BOT_SETTINGS_KEY] = json.dumps(data, ensure_ascii=False)
    except Exception as e:
        logger.error(f"✗ Settings save error: {e}")

def load_chats() -> List[int]:
    return load_all_data().get("chats", [])

def save_chats(chats: List[int]) -> None:
    d = load_all_data()
    d["chats"] = chats
    save_all_data(d)

def load_names() -> Dict[str, Dict]:
    return load_all_data().get("names", {})

def save_names(names: Dict[str, Dict]) -> None:
    d = load_all_data()
    d["names"] = names
    save_all_data(d)

def load_settings() -> Dict:
    defaults = {
        "send_hour": 18,
        "send_minute": 0,
        "mood": "hard",  # light, medium, hard, ultra
        "raid_enabled": RAID_ENABLED_DEFAULT,
        "raid_min_hours": RAID_MIN_HOURS,
        "raid_max_hours": RAID_MAX_HOURS
    }
    return {**defaults, **load_all_data().get("settings", {})}

def save_settings(s: Dict) -> None:
    d = load_all_data()
    d["settings"] = s
    save_all_data(d)

def get_display_name(user, include_meta: bool = True) -> str:
    """Формирует отображаемое имя пользователя"""
    names = load_names()
    uid = str(user.id)
    
    if uid not in names:
        return user.first_name or user.username or "Анон"
    
    name = names[uid].get("name") or user.first_name or user.username or "Анон"
    if not include_meta:
        return name
    
    parts = [f"@{name}"]
    gender = names[uid].get("gender")
    if gender:
        parts.append(f"[{'♂' if gender=='male' else '♀' if gender=='female' else '⚧'}]")
    desc = names[uid].get("description")
    if desc:
        parts.append(f", {desc}")
    return "".join(parts)

# ========== ПРОМПТЫ ==========
MOOD_STYLES = {
    "light": "Лёгкий сарказм, редкая экспрессия, минимум мата. Ирония без злобы.",
    "medium": "Умеренный юмор, иногда острый, допустим сленг и экспрессивная лексика.",
    "hard": "Сатирический, циничный, чёрный юмор. Используй сленг и слова из словаря для комического эффекта.",
    "ultra": "Жёсткий троллинг, поток сарказма, прямые формулировки без цензуры. Максимальная экспрессия."
}

GREETINGS = [
    "📰 Главное из последних сообщений:",
    "📰 Экстренный выпуск Зяблографа!",
    "📰 Зяблограф выбрал самое «достойное»:",
    "📰 Зяблограф: главные события:",
    "📰 Дайджест чата от Зяблографа:",
    "📰 Хроника безумия от Зяблографа:",
    "📰 Вестник чатового хаоса:"
]

def get_greeting() -> str:
    return random.choice(GREETINGS)

def _build_prompt(messages: List[Dict], mood: str, chat_dict: Dict) -> str:
    """Собирает промпт для LLM"""
    mood_desc = MOOD_STYLES.get(mood, MOOD_STYLES["hard"])
    
    # Формируем словарь для инъекции
    dict_injection = []
    for cat in ["существительные", "глаголы", "прилагательные", "наречия", "метафоры", "сравнения"]:
        if cat in chat_dict:
            dict_injection.extend(random.sample(chat_dict[cat], min(5, len(chat_dict[cat]))))
    dict_str = ", ".join(dict_injection[:20])
    
    # Формируем лог сообщений
    log_lines = []
    for m in messages:
        link = m.get("link", "")
        author = m.get("author", "Анон")
        text = m.get("text", "")[:200]  # обрезаем для промпта
        log_lines.append(f"[{link}] @{author}: {text}")
    
    log_block = "\n".join(log_lines[-50:])  # последние 50 сообщений
    
    return f"""[System: Ты — «Зяблограф», циничный хроникёр чата в стиле сатирического дайджеста. Пиши ТОЛЬКО на русском.

СТИЛЬ: {mood_desc}
Используй метафоры, иронию, сленг и слова из словаря для комического эффекта. Не будь вежливым.

ЖЁСТКИЕ ПРАВИЛА:
1. ГРУППИРОВКА: Все сообщения про одно событие/тему объединяй в ОДИН абзац. Никаких дублей.
2. ФОРМАТ: Каждый абзац начинается СТРОГО: # (ССЫЛКА_ИЗ_ВХОДНЫХ_ДАННЫХ) Текст события.
3. ЗАПРЕТ: Не придумывай заголовки (#ТемаДня). Используй только ссылку из лога.
4. ЯЗЫК: Только кириллица. Иероглифы/латиница (кроме ссылок) запрещены.
5. ОБЪЁМ: 2-4 предложения на тему. Не выдумывай фактов.

СЛОВАРЬ ДЛЯ ИСПОЛЬЗОВАНИЯ (выбирай уместные):
{dict_str}

Вот лог чата (каждая строка — одно сообщение):
{log_block}

Сгенерируй дайджест в требуемом формате:"""

# ========== LLM ВЫЗОВ ==========
async def _call_llm(prompt: str, chat_id: Optional[int] = None) -> Optional[str]:
    """Вызывает LLM с цепочкой моделей и ретраями"""
    prompt_len = len(prompt)
    logger.info(f"🤖 LLM call: chat={chat_id}, prompt_len={prompt_len}, models={len(LLM_MODELS)}")
    
    if prompt_len > 85000:
        logger.warning(f"⚠ Prompt truncated: {prompt_len} → 85000 chars")
        prompt = prompt[:85000]
    
    for model_idx, model in enumerate(LLM_MODELS, 1):
        for attempt in range(1, 3):
            try:
                logger.debug(f"→ Attempt {attempt}/2 with {model} (try #{model_idx})")
                start = time.time()
                
                loop = asyncio.get_event_loop()
                comp = await loop.run_in_executor(None, lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=LLM_TEMPERATURE,
                    max_tokens=LLM_MAX_TOKENS,
                    extra_headers={"HTTP-Referer": "https://t.me/zyablograf_bot"}
                ))
                elapsed = time.time() - start
                
                text = comp.choices[0].message.content.strip()
                logger.info(f"✓ {model} responded in {elapsed:.2f}s | tokens≈{len(text)//4}")
                
                # Проверка на отказ модели
                refusal_words = ["sorry", "cannot", "не могу", "отказ", "safety", "i can't", "unable", "policy"]
                if any(w in text.lower() for w in refusal_words):
                    logger.warning(f"⚠ Model {model} refused (safety filter?)")
                    continue
                
                return _clean_llm_output(text)
                
            except RateLimitError as e:
                logger.warning(f"⚠ Rate limit on {model}: {e}")
                break  # Переход к следующей модели
            except AuthenticationError as e:
                logger.error(f"✗ Auth error on {model}: {e}")
                return None
            except APIConnectionError as e:
                logger.warning(f"⚠ Connection error on {model}: {e}")
                await asyncio.sleep(2 ** attempt)
            except APIError as e:
                err = str(e).lower()
                if "insufficient" in err or "402" in err:
                    logger.error(f"✗ Balance error on {model}: {e}")
                    return None
                logger.warning(f"⚠ API error on {model}: {e}", exc_info=True)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"✗ Unexpected error on {model}: {e}", exc_info=True)
                await asyncio.sleep(1)
    
    logger.error(f"✗ All LLM attempts failed for chat {chat_id}")
    return None

def _clean_llm_output(text: str) -> str:
    """Чистит вывод LLM от мусора"""
    text = text.strip()
    
    # Удаляем иероглифы/арабскую вязь, но оставляем ссылки и русский
    text = re.sub(r'[^\x00-\x7Fа-яА-ЯёЁ0-9\s.,!?;:()«»""''\-—@#$/\n\r]', '', text)
    text = re.sub(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+', '', text)
    
    # Приводим ссылки к виду # (https://...)
    text = re.sub(r'#\s+(https?://t\.me/\S+)', r'# (\1)', text)
    text = re.sub(r'#[^(\s]+\s*(https?://t\.me/\S+)', r'# (\1)', text)
    
    # Удаляем спонсорские блоки если попали
    if "⭐️ Станьте спонсором" in text:
        text = text.split("⭐️ Станьте спонсором")[0].strip()
    
    return text.strip()

def _escape_md(text: str) -> str:
    """Экранирует текст для MarkdownV2"""
    # Экранируем спецсимволы, но не трогаем ссылки в формате # (https://...)
    chars = r'_*[]()~`>#+-=|{}.!'
    result = []
    i = 0
    while i < len(text):
        # Пропускаем ссылки
        if text[i:i+2] == '# (':
            # Находим конец ссылки
            end = text.find(')', i)
            if end != -1:
                result.append(text[i:end+1])
                i = end + 1
                continue
        if text[i] in chars:
            result.append('\\' + text[i])
        else:
            result.append(text[i])
        i += 1
    return ''.join(result)

# ========== ОТПРАВКА С ЗАЩИТОЙ ==========
async def _send_safe(cid: int, text: str, parse_mode: Optional[str] = "MarkdownV2", 
                     thread: Optional[int] = None) -> Optional[Message]:
    """Отправляет сообщение с авто-фоллбэком при ошибках"""
    if not isinstance(text, str):
        text = str(text)
    
    logger.debug(f"Sending to chat {cid}: parse_mode={parse_mode}, thread={thread}, len={len(text)}")
    logger.debug(f"Text preview: {text[:200]}...")
    
    try:
        if parse_mode == "MarkdownV2":
            text = _escape_md(text)
            logger.debug("Escaped for MarkdownV2")
        
        result = await bot.send_message(
            cid, text, parse_mode=parse_mode, 
            message_thread_id=thread, disable_web_page_preview=True
        )
        logger.info(f"✓ Sent to {cid} (msg_id={result.message_id})")
        return result
        
    except TelegramError as e:
        err_msg = str(e)
        err_lower = err_msg.lower()
        
        logger.warning(f"✗ Send failed to {cid}: {err_msg} | parse_mode={parse_mode}, thread={thread}")
        
        # Детализация причин
        if "thread" in err_lower or "message_thread_id" in err_lower:
            logger.debug(f"→ Retrying without thread_id for chat {cid}")
            return await _send_safe(cid, text, parse_mode, thread=None)
            
        if parse_mode and ("markdown" in err_lower or "parse" in err_lower or "entity" in err_lower):
            logger.debug(f"→ Retrying without parse_mode for chat {cid}")
            return await _send_safe(cid, text, None, thread)
            
        if "message is too long" in err_lower:
            logger.error(f"✗ Message too long for chat {cid}: {len(text)} chars (limit ~4096)")
            # Пробуем разбить
            for part in _split_message(text):
                await _send_safe(cid, part, None, thread)
            return None
            
        if "chat not found" in err_lower or "forbidden" in err_lower:
            logger.error(f"✗ Chat {cid} unavailable (removed bot / private)")
            # Удаляем чат из списка
            chats = load_chats()
            if cid in chats:
                chats.remove(cid)
                save_chats(chats)
                logger.info(f"Removed chat {cid} from list")
                
        if "flood wait" in err_lower or "429" in err_lower:
            wait_sec = re.search(r"(\d+) seconds", err_msg)
            if wait_sec:
                wait = int(wait_sec.group(1)) + 1
                logger.warning(f"⏳ Rate limit: wait {wait}s")
                await asyncio.sleep(wait)
                return await _send_safe(cid, text, parse_mode, thread)
                
        logger.error(f"✗ Unhandled TelegramError for {cid}: {err_msg}", exc_info=True)
        return None

def _split_message(text: str, max_len: int = 4000) -> List[str]:
    """Разбивает длинное сообщение на части"""
    parts = []
    while len(text) > max_len:
        # Ищем ближайший перенос строки
        split_at = text.rfind('\n', 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        parts.append(text)
    return parts

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
def _filter_messages(msgs: List[Dict], max_per_user: int = 3) -> List[Dict]:
    """Фильтрует сообщения: не больше N на пользователя"""
    user_counts: Dict[str, int] = {}
    result = []
    for m in msgs:
        uid = str(m.get("user_id", ""))
        if user_counts.get(uid, 0) < max_per_user:
            result.append(m)
            user_counts[uid] = user_counts.get(uid, 0) + 1
    return result

async def _handle_msg(msg: Message) -> None:
    """Обрабатывает входящее сообщение"""
    cid = msg.chat.id
    uid = msg.from_user.id if msg.from_user else None
    text = msg.text or msg.caption or ""
    
    logger.debug(f"📨 Message: chat={cid}, user={uid}, text_len={len(text)}, has_media={bool(msg.photo or msg.video)}")
    
    # Пропуск системных / своих
    if uid == bot.id or (text and text.startswith("/")):
        logger.debug("→ Skipping (bot command or self)")
        return
        
    # Обрезка текста для лога
    text_preview = text[:150] + ("..." if len(text) > 150 else "")
    logger.debug(f"→ Preview: {text_preview!r}")
    
    # Формируем ссылку
    link = f"https://t.me/c/{str(cid).replace('-100','')}/{msg.message_id}"
    author = get_display_name(msg.from_user, include_meta=False) if msg.from_user else "Анон"
    
    logger.debug(f"→ Saved: link={link}, author={author}")
    
    # Сохраняем сообщение
    daily_messages.setdefault(cid, []).append({
        "link": link,
        "author": author,
        "text": text.strip(),
        "user_id": uid,
        "timestamp": msk_now().isoformat()
    })
    
    # Ограничиваем историю
    if len(daily_messages[cid]) > 2000:
        daily_messages[cid] = daily_messages[cid][-1500:]
    
    save_messages_to_disk()
    logger.info(f"✓ Message saved for chat {cid} (total: {len(daily_messages[cid])})")
    
    # Проверяем триггеры
    await _check_and_send_if_needed(cid)

async def _check_and_send_if_needed(cid: int) -> None:
    """Проверяет, пора ли отправлять дайджест"""
    msgs = daily_messages.get(cid, [])
    if len(msgs) < DIGEST_MIN_MESSAGES:
        return
    
    settings = load_settings()
    now = msk_now()
    today = now.date()
    
    # Проверка по времени
    send_time = now.replace(
        hour=settings["send_hour"], 
        minute=settings["send_minute"], 
        second=0, microsecond=0
    )
    
    # Если уже отправляли сегодня — пропускаем
    if digest_sent_today.get(cid) == today:
        return
    
    # Триггеры: 1) время, 2) кол-во сообщений, 3) 24 часа с последнего
    should_send = False
    
    # Триггер по времени
    if now >= send_time and (digest_sent_today.get(cid) != today):
        should_send = True
        logger.info(f"⏰ Time trigger for chat {cid}")
    
    # Триггер по количеству
    if len(msgs) >= DIGEST_TRIGGER_MESSAGES:
        should_send = True
        logger.info(f"📊 Count trigger for chat {cid}: {len(msgs)} msgs")
    
    # Триггер по времени с последнего дайджеста
    last_sent = digest_sent_today.get(cid)
    if last_sent and (now.date() - last_sent).days >= 1:
        should_send = True
        logger.info(f"⏱ 24h trigger for chat {cid}")
    
    if should_send:
        await _send_digest(cid)

async def _send_digest(cid: int) -> None:
    """Генерирует и отправляет дайджест"""
    logger.info(f"📰 Generating digest for chat {cid}")
    
    msgs = daily_messages.get(cid, [])
    if len(msgs) < DIGEST_MIN_MESSAGES:
        logger.warning(f"⚠ Not enough messages for digest: {len(msgs)}")
        return
    
    # Фильтруем и берём последние
    filtered = _filter_messages(msgs[-500:], max_per_user=3)
    logger.info(f"✓ Filtered {len(filtered)} messages for digest")
    
    settings = load_settings()
    prompt = _build_prompt(filtered, settings["mood"], SWEAR_DICT)
    
    result = await _call_llm(prompt, chat_id=cid)
    if not result:
        logger.error("✗ LLM failed to generate digest")
        await _send_safe(ADMIN_ID, f"⚠ Зяблограф не смог сгенерировать дайджест для чата {cid}", parse_mode=None)
        return
    
    # Формируем финальное сообщение
    greeting = get_greeting()
    full_text = f"{greeting}\n\n{_escape_md(result)}"
    
    # Отправляем частями если нужно
    for part in _split_message(full_text):
        await _send_safe(cid, part, parse_mode="MarkdownV2")
    
    # Обновляем метки
    digest_sent_today[cid] = msk_now().date()
    daily_messages[cid] = []  # очищаем после отправки
    save_messages_to_disk()
    
    logger.info(f"✓ Digest sent to chat {cid}")

# ========== ПЕРИОДИЧЕСКАЯ ПРОВЕРКА ==========
async def _digest_periodic_checker() -> None:
    """Фоновая задача: проверка расписания каждую минуту"""
    logger.info("🔄 Starting periodic digest checker")
    while True:
        try:
            now = msk_now()
            for cid in list(daily_messages.keys()):
                msgs = daily_messages.get(cid, [])
                if len(msgs) >= DIGEST_MIN_MESSAGES:
                    await _check_and_send_if_needed(cid)
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"✗ Periodic checker error: {e}", exc_info=True)
            await asyncio.sleep(60)

# ========== АДМИН-КОМАНДЫ ==========
async def _admin_cmd(msg: Message) -> None:
    """Обрабатывает команды админа"""
    text = (msg.text or "").strip()
    if not isinstance(text, str):
        text = str(text)
    
    logger.info(f"🔧 Admin command from {msg.from_user.id}: {text[:100]}")
    
    try:
        # === Управление чатами ===
        if text.startswith("/add_chat"):
            p = text.split()
            cid = int(p[1]) if len(p) > 1 else msg.reply_to_message.chat.id if msg.reply_to_message else None
            if not cid:
                await _send_safe(ADMIN_ID, "❌ /add_chat ID", parse_mode=None)
                return
            chats = load_chats()
            if cid not in chats:
                chats.append(cid)
                save_chats(chats)
                daily_messages.setdefault(cid, [])
                reactions.setdefault(cid, [])
                await _send_safe(ADMIN_ID, f"✅ Чат {cid} добавлен", parse_mode=None)
            else:
                await _send_safe(ADMIN_ID, f"⚠ Чат {cid} уже в списке", parse_mode=None)
                
        elif text.startswith("/remove_chat"):
            p = text.split()
            cid = int(p[1]) if len(p) > 1 else msg.reply_to_message.chat.id if msg.reply_to_message else None
            if not cid:
                await _send_safe(ADMIN_ID, "❌ /remove_chat ID", parse_mode=None)
                return
            chats = load_chats()
            if cid in chats:
                chats.remove(cid)
                save_chats(chats)
                await _send_safe(ADMIN_ID, f"✅ Чат {cid} удалён", parse_mode=None)
            else:
                await _send_safe(ADMIN_ID, f"⚠ Чат {cid} не в списке", parse_mode=None)
                
        elif text.startswith("/list_chats"):
            chats = load_chats()
            if not chats:
                await _send_safe(ADMIN_ID, "📋 Нет чатов", parse_mode=None)
            else:
                lines = ["📋 Мониторинг:"] + [f"  • {c} ({len(daily_messages.get(c,[]))} msg)" for c in chats]
                await _send_safe(ADMIN_ID, "\n".join(lines), parse_mode=None)
        
        # === Настройки времени ===
        elif text.startswith("/settime"):
            p = text.split()
            if len(p) < 2:
                await _send_safe(ADMIN_ID, "❌ /settime ЧЧ:ММ", parse_mode=None)
                return
            try:
                hh, mm = map(int, p[1].split(":"))
                s = load_settings()
                s["send_hour"], s["send_minute"] = hh, mm
                save_settings(s)
                await _send_safe(ADMIN_ID, f"✅ Время дайджеста: {hh:02d}:{mm:02d} МСК", parse_mode=None)
            except:
                await _send_safe(ADMIN_ID, "❌ Формат: /settime 18:00", parse_mode=None)
        
        # === Настройка стиля ===
        elif text.startswith("/mood"):
            p = text.split()
            if len(p) < 2 or p[1] not in MOOD_STYLES:
                await _send_safe(ADMIN_ID, f"❌ /mood {'|'.join(MOOD_STYLES.keys())}", parse_mode=None)
                return
            s = load_settings()
            s["mood"] = p[1]
            save_settings(s)
            await _send_safe(ADMIN_ID, f"✅ Стиль: {p[1].upper()}", parse_mode=None)
        
        # === Рейды ===
        elif text.startswith("/raid"):
            p = text.split()
            if len(p) < 2:
                s = load_settings()
                status = "вкл" if s.get("raid_enabled", True) else "выкл"
                await _send_safe(ADMIN_ID, f"⚔️ Рейды: {status}\n/raid on|off|now", parse_mode=None)
                return
            if p[1] == "on":
                s = load_settings()
                s["raid_enabled"] = True
                save_settings(s)
                await _send_safe(ADMIN_ID, "✅ Рейды включены", parse_mode=None)
            elif p[1] == "off":
                s = load_settings()
                s["raid_enabled"] = False
                save_settings(s)
                await _send_safe(ADMIN_ID, "✅ Рейды выключены", parse_mode=None)
            elif p[1] == "now":
                # Принудительный рейд
                for cid in load_chats():
                    await _send_digest(cid)
                await _send_safe(ADMIN_ID, "🚀 Рейд запущен", parse_mode=None)
        
        # === Управление пользователями ===
        elif any(text.startswith(cmd) for cmd in ["/setname", "/setdesc", "/setgender"]):
            cmd = "/setname" if text.startswith("/setname") else "/setdesc" if text.startswith("/setdesc") else "/setgender"
            p = text.split(maxsplit=2)
            if len(p) < 3:
                await _send_safe(ADMIN_ID, f"❌ {cmd} ID ЗНАЧЕНИЕ", parse_mode=None)
                return
            try:
                uid, val = p[1], p[2].strip('"').strip("'")
                key = "name" if "name" in cmd else "description" if "desc" in cmd else "gender"
                names = load_names()
                if uid not in names:
                    names[uid] = {}
                names[uid][key] = val.lower() if key == "gender" else val
                save_names(names)
                await _send_safe(ADMIN_ID, f"✅ {key} для {uid}: «{val}»", parse_mode=None)
            except Exception as e:
                await _send_safe(ADMIN_ID, f"❌ Ошибка: {e}", parse_mode=None)
                
        elif any(text.startswith(cmd) for cmd in ["/removename", "/removedesc", "/removegender"]):
            cmd = "/removename" if text.startswith("/removename") else "/removedesc" if text.startswith("/removedesc") else "/removegender"
            p = text.split()
            key = "name" if "name" in cmd else "description" if "desc" in cmd else "gender"
            if len(p) < 2:
                await _send_safe(ADMIN_ID, f"❌ {cmd} ID", parse_mode=None)
                return
            try:
                uid = p[1]
                names = load_names()
                if uid in names and key in names[uid]:
                    del names[uid][key]
                    save_names(names)
                    await _send_safe(ADMIN_ID, f"✅ {key} для {uid} удалено", parse_mode=None)
                else:
                    await _send_safe(ADMIN_ID, f"⚠ Нет данных для {uid}", parse_mode=None)
            except:
                await _send_safe(ADMIN_ID, "❌ Ошибка", parse_mode=None)
                
        elif text.startswith("/list_users"):
            p = text.split()
            uid = p[1] if len(p) > 1 else None
            names = load_names()
            if uid:
                if uid not in names:
                    await _send_safe(ADMIN_ID, f"⚠ Нет данных для {uid}", parse_mode=None)
                    return
                d = names[uid]
                g = {"male":"♂ Муж","female":"♀ Жен","other":"⚧ Другое"}.get(d.get("gender"), "—")
                await _send_safe(ADMIN_ID, f"📋 {uid}:\n  • Имя: {d.get('name', '—')}\n  • Описание: {d.get('description', '—')}\n  • Пол: {g}", parse_mode=None)
            elif not names:
                await _send_safe(ADMIN_ID, "📋 Нет кастомных данных", parse_mode=None)
            else:
                lines = ["📋 Все пользователи:"]
                for u, d in sorted(names.items()):
                    lines.append(f"  • {u}: «{d.get('name','—')}» {d.get('gender','—')} — {d.get('description','—')}")
                await _send_safe(ADMIN_ID, "\n".join(lines), parse_mode=None)
        
        # === Тест и статус ===
        elif text.startswith("/test"):
            p = text.split()
            cid = int(p[1]) if len(p) > 1 else (load_chats() or [None])[0]
            cnt = int(p[2]) if len(p) > 2 else 10
            if not cid:
                await _send_safe(ADMIN_ID, "❌ Нет чатов", parse_mode=None)
                return
            msgs = daily_messages.get(cid, [])
            if len(msgs) < DIGEST_MIN_MESSAGES:
                await _send_safe(ADMIN_ID, f"❌ Всего {len(msgs)} сообщ. (нужно ≥{DIGEST_MIN_MESSAGES})", parse_mode=None)
                return
            log = "\n".join(f"[{m['link']}] @{m['author']}: {m['text'][:100]}" for m in _filter_messages(msgs[-min(cnt,len(msgs)):], 3))
            await _send_safe(ADMIN_ID, "🧪 Генерирую тест...", parse_mode=None)
            result = await _call_llm(_build_prompt(_filter_messages(msgs[-min(cnt,len(msgs)):], 3), load_settings()["mood"], SWEAR_DICT), chat_id=cid)
            if not result:
                return
            full = f"{get_greeting()}\n\n{_escape_md(result)}"
            for part in _split_message(full):
                await _send_safe(ADMIN_ID, part)
                
        elif text.startswith("/status"):
            s = load_settings()
            lines = ["📊 Статистика:"]
            for cid, msgs in daily_messages.items():
                lines.append(f"  Чат {cid}: {len(msgs)} сообщ.")
            if not daily_messages:
                lines.append("  Пусто.")
            lines += [
                f"\n⏰ Время: {s['send_hour']:02d}:{s['send_minute']:02d} МСК",
                f"🎭 Стиль: {s.get('mood','hard').upper()}",
                f"⚔️ Рейды: {'вкл' if s.get('raid_enabled',True) else 'выкл'}"
            ]
            await _send_safe(ADMIN_ID, "\n".join(lines), parse_mode=None)
            
        elif text.startswith("/reset"):
            p = text.split()
            cid = int(p[1]) if len(p) > 1 else None
            if cid:
                daily_messages[cid] = []
                reactions[cid] = []
            else:
                daily_messages.clear()
                reactions.clear()
            save_messages_to_disk()
            await _send_safe(ADMIN_ID, "🗑️ Сброшено", parse_mode=None)
            
        elif text.startswith("/help"):
            s = load_settings()
            help_text = f"""🛠 ЗЯБЛОГРАФ — ПОЛНАЯ СПРАВКА

📰 ДАЙДЖЕСТЫ
• ⏰ /settime ЧЧ:ММ — время сводки. Сейчас: {s['send_hour']:02d}:{s['send_minute']:02d}
• 🔄 Сработает: {DIGEST_TRIGGER_MESSAGES} сообщений ИЛИ {DIGEST_TRIGGER_HOURS}ч ИЛИ /settime

🤬 РЕЙДЫ
• ⚔️ /raid on|off — вкл/выкл авто-наезды
• 🚀 /raid now — принудительный запуск

🏷️ ПОЛЬЗОВАТЕЛИ
• 🏷️ /setname ID "Имя" | 📝 /setdesc ID "Описание" | ⚧ /setgender ID male|female|other
• ❌ /removename|removedesc|removegender ID — убрать
• 📋 /list_users [ID] — показать всех или одного

⚙️ ПРОЧЕЕ
• 🎭 /mood light|medium|hard|ultra
• 📋 /add_chat|remove_chat|list_chats
• 🧪 /test [чат] [кол-во]
• /status | /reset"""
            await _send_safe(ADMIN_ID, help_text, parse_mode=None)
        
        # === /id — показать юзер айди ===
        elif text.startswith("/id"):
            if msg.reply_to_message and msg.reply_to_message.from_user:
                uid = msg.reply_to_message.from_user.id
                name = get_display_name(msg.reply_to_message.from_user)
                await _send_safe(ADMIN_ID, f"🆔 {name}: {uid}", parse_mode=None)
            else:
                await _send_safe(ADMIN_ID, "🆔 Ответь на сообщение или перешли его мне", parse_mode=None)
                
    except Exception as e:
        logger.error(f"✗ Error in admin command '{text}': {e}", exc_info=True)
        await _send_safe(ADMIN_ID, f"❌ Ошибка: {e}", parse_mode=None)

# ========== ЗАПУСК ==========
async def main() -> None:
    """Точка входа"""
    logger.info("🚀 Зяблограф запущен! OpenRouter (:free), бюджет оптимизирован.")
    
    await bot.initialize()
    load_messages_from_disk()
    
    # Инициализация чатов
    for cid in load_chats():
        daily_messages.setdefault(cid, [])
        reactions.setdefault(cid, [])
    
    # Запуск фоновых задач
    asyncio.create_task(_digest_periodic_checker())
    
    # Основной цикл поллинга
    offset = None
    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=30,
                allowed_updates=["message"]
            )
            for u in updates:
                if u.message:
                    if u.message.chat.id == ADMIN_ID:
                        await _admin_cmd(u.message)
                    else:
                        await _handle_msg(u.message)
                offset = u.update_id + 1
        except Forbidden:
            logger.error("✗ Bot was blocked! Stopping.")
            break
        except NetworkError as e:
            logger.warning(f"⚠ Network error: {e}, retrying in 5s")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"✗ Update error: {e}", exc_info=True)
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
