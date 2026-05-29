# ===================================================================
# ЗЯБЛОГРАФ v4.2 — ВЕСТНИК STYLE EDITION
# FULL SINGLE FILE
# ===================================================================

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
import logging
import asyncio
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from openai import OpenAI
from telegram import Bot
from telegram.error import TelegramError

# ===================================================================
# CONFIG
# ===================================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "417850992"))
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("Укажи BOT_TOKEN")

if not OPENROUTER_API_KEY:
    raise RuntimeError("Укажи OPENROUTER_API_KEY")

DICT_FILE = "dictionary.json"
MESSAGES_FILE = "daily_messages.json"
MEMORY_FILE = "character_memory.json"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

bot = Bot(token=BOT_TOKEN)

# ===================================================================
# GLOBALS
# ===================================================================

daily_messages = {}
digest_sent_today = {}
character_memory = {}
bot_reply_memory = {}

# ===================================================================
# LOGGING
# ===================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("Zyablograf")
logging.getLogger("httpx").setLevel(logging.WARNING)

MSK_TZ = timezone(timedelta(hours=3))

def msk_now():
    return datetime.now(MSK_TZ)

# ===================================================================
# LOAD / SAVE
# ===================================================================

def save_data():
    try:
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "daily_messages": daily_messages,
                "bot_reply_memory": bot_reply_memory
            }, f, ensure_ascii=False)

        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(character_memory, f, ensure_ascii=False)

    except Exception as e:
        logger.error(f"SAVE ERROR: {e}")

def load_data():
    global daily_messages
    global character_memory
    global bot_reply_memory

    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        daily_messages = {
            int(k): v for k, v in data.get("daily_messages", {}).items()
        }

        bot_reply_memory = data.get("bot_reply_memory", {})

    except:
        logger.info("Starting fresh messages")

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            character_memory = json.load(f)

    except:
        character_memory = {}

# ===================================================================
# DICTIONARY
# ===================================================================

def load_dictionary():
    try:
        with open(DICT_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        return {
            k.strip(): [
                x.strip() for x in v if x.strip()
            ]
            for k, v in raw.items()
        }

    except:
        return {
            "метафоры": ["мешок с говном"],
            "эпитеты_для_людей": ["еблан"]
        }

SWEAR_DICT = load_dictionary()

# ===================================================================
# SETTINGS
# ===================================================================

SETTINGS = {
    "send_hour": 18,
    "send_minute": 0,
    "mood": "hard"
}

# ===================================================================
# HELPERS
# ===================================================================

def get_display_name(user):
    return user.first_name or user.username or "Анон"

def get_greeting():
    return random.choice([
        "📰 Экстренный выпуск Зяблографа!",
        "📰 Новости из помойки чата:",
        "📰 Зяблограф снова охуел:",
        "📰 Что происходило в этом цирке:"
    ])

# ===================================================================
# MEMORY SYSTEM
# ===================================================================

def update_character_memory(author, text):

    if author not in character_memory:
        character_memory[author] = {
            "traits": [],
            "topics": [],
            "last_seen": ""
        }

    mem = character_memory[author]

    lower = text.lower()

    triggers = {
        "качалка": ["зал", "жим", "трен", "бег"],
        "алкаш": ["пиво", "водка", "бух"],
        "дегенерат": ["нахуй", "ебать", "пиздец"],
        "ботовод": ["бот", "/"],
        "аниме": ["аниме", "манга"],
        "игроман": ["дота", "cs", "игра"]
    }

    for trait, words in triggers.items():
        if any(w in lower for w in words):
            if trait not in mem["traits"]:
                mem["traits"].append(trait)

    mem["last_seen"] = msk_now().isoformat()

# ===================================================================
# EVENT CLUSTERING
# ===================================================================

def cluster_messages(messages):

    clusters = defaultdict(list)

    keywords = {
        "спорт": ["бег", "зал", "жим", "кардио"],
        "боты": ["бот", "/", "команда"],
        "срач": ["нахуй", "ебать", "пошел"],
        "алкашка": ["пиво", "водка", "бух"],
        "игры": ["дота", "cs", "катка"]
    }

    for msg in messages:

        text = msg["text"].lower()

        matched = False

        for topic, words in keywords.items():
            if any(w in text for w in words):
                clusters[topic].append(msg)
                matched = True
                break

        if not matched:
            clusters["прочее"].append(msg)

    return list(clusters.values())

# ===================================================================
# PROMPT
# ===================================================================

def build_prompt(clusters):

    mood = SETTINGS["mood"]

    dict_words = []

    for cat in SWEAR_DICT.values():
        dict_words.extend(cat[:5])

    random.shuffle(dict_words)

    slang = ", ".join(dict_words[:25])

    memory_text = ""

    for user, mem in list(character_memory.items())[:20]:

        if mem["traits"]:
            memory_text += f"{user}: {', '.join(mem['traits'])}\n"

    cluster_text = ""

    for i, cluster in enumerate(clusters):

        cluster_text += f"\n=== СОБЫТИЕ {i+1} ===\n"

        for m in cluster[:15]:
            cluster_text += (
                f"[{m['link']}] "
                f"@{m['author']}: "
                f"{m['text']}\n"
            )

    return f"""
Ты — «Зяблограф».

Это НЕ токсичный школьник.
Это сатирический обозреватель чата.

ВАЖНО:
- Мат использовать редко и метко.
- Не пытайся впихнуть мат в каждое предложение.
- Главное — наблюдение, ирония и абсурд.
- Юмор должен строиться на поведении людей.
- Не повторяй одинаковые конструкции.
- Не называй всех подряд ебланами.
- Не делай каждый абзац одинаковым.
- Не выдумывай события.
- Если человек отвечает на старый наезд бота —
  ОБЯЗАТЕЛЬНО упомяни это как продолжение конфликта.

СТИЛЬ:
- как телеграм-вестник
- как саркастичный журналист
- как хроника дурдома

Используй иногда:
{slang}

ПАМЯТЬ ПЕРСОНАЖЕЙ:
{memory_text}

ФОРМАТ:
# (ссылка) текст

2-5 предложений на событие.

ЛОГ:
{cluster_text}
"""

# ===================================================================
# MODEL CALL
# ===================================================================

async def call_llm(prompt):

    models = [

        # ДЕШЕВЫЕ И ХОРОШИЕ

        "deepseek/deepseek-chat-v3-0324",
        "qwen/qwen-2.5-7b-instruct",

        # FALLBACK

        "nousresearch/hermes-3-llama-3.1-8b",
    ]

    for model in models:

        try:

            logger.info(f"Trying model: {model}")

            loop = asyncio.get_event_loop()

            completion = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    temperature=1.0,
                    max_tokens=1800
                )
            )

            text = completion.choices[0].message.content

            if text:
                return clean_output(text)

        except Exception as e:
            logger.error(f"{model}: {e}")

    return None

# ===================================================================
# CLEAN OUTPUT
# ===================================================================

def clean_output(text):

    text = text.strip()

    text = re.sub(
        r'[^\x00-\x7Fа-яА-ЯёЁ0-9\s.,!?;:()«»""\'\'\-—@#$/\n\r]',
        '',
        text
    )

    return text.strip()

# ===================================================================
# SEND SAFE
# ===================================================================

async def send_safe(cid, text):

    try:
        return await bot.send_message(
            cid,
            text,
            parse_mode=None
        )

    except TelegramError as e:
        logger.error(f"SEND ERROR: {e}")

# ===================================================================
# FILTER
# ===================================================================

def filter_messages(msgs, limit=120):

    hot = []

    regex = re.compile(
        r'бля|хуй|пизд|еб|сука|бот|зал|бег|дота|cs|нах',
        re.I
    )

    for m in msgs:

        if regex.search(m["text"]):
            hot.append(m)

    return (hot or msgs)[-limit:]

# ===================================================================
# DIGEST
# ===================================================================

async def generate_digest(cid):

    msgs = daily_messages.get(cid, [])

    if len(msgs) < 5:
        return None

    filtered = filter_messages(msgs)

    clusters = cluster_messages(filtered)

    prompt = build_prompt(clusters)

    return await call_llm(prompt)

async def send_digest(cid):

    result = await generate_digest(cid)

    if not result:
        return

    text = (
        f"{get_greeting()}\n\n"
        f"{result}"
    )

    parts = split_message(text)

    sent_messages = []

    for part in parts:

        sent = await send_safe(cid, part)

        if sent:
            sent_messages.append(sent.message_id)

        await asyncio.sleep(1)

    # SAVE BOT REPLIES
    bot_reply_memory[str(cid)] = sent_messages

    daily_messages[cid] = []

    save_data()

# ===================================================================
# SPLIT
# ===================================================================

def split_message(text, max_len=4000):

    if len(text) <= max_len:
        return [text]

    parts = []
    current = ""

    for line in text.split("\n"):

        if len(current) + len(line) < max_len:
            current += "\n" + line

        else:
            parts.append(current)
            current = line

    if current:
        parts.append(current)

    return parts

# ===================================================================
# HANDLE MESSAGE
# ===================================================================

async def handle_message(msg):

    cid = msg.chat.id

    if not msg.from_user:
        return

    text = msg.text or msg.caption or ""

    if not text:
        text = "[медиа]"

    author = get_display_name(msg.from_user)

    update_character_memory(author, text)

    # CALLBACK MEMORY
    callback_context = ""

    if msg.reply_to_message:

        if msg.reply_to_message.from_user:

            if msg.reply_to_message.from_user.id == bot.id:

                callback_context = (
                    f"ОТВЕТ НА ДАЙДЖЕСТ: {text}"
                )

    link = (
        f"https://t.me/c/"
        f"{str(cid).replace('-100','')}/"
        f"{msg.message_id}"
    )

    daily_messages.setdefault(cid, []).append({

        "author": author,
        "text": text,
        "link": link,
        "timestamp": msk_now().isoformat(),
        "callback": callback_context

    })

    save_data()

# ===================================================================
# PERIODIC CHECK
# ===================================================================

async def periodic_checker():

    while True:

        await asyncio.sleep(60)

        now = msk_now()

        for cid, msgs in list(daily_messages.items()):

            if not msgs:
                continue

            # EVERY 24H
            first_time = datetime.fromisoformat(
                msgs[0]["timestamp"]
            )

            if (
                now - first_time
            ).total_seconds() >= 86400:

                await send_digest(cid)

            # FIXED TIME
            if (
                now.hour == SETTINGS["send_hour"]
                and now.minute == SETTINGS["send_minute"]
            ):

                if digest_sent_today.get(cid) != now.date():

                    await send_digest(cid)

                    digest_sent_today[cid] = now.date()

# ===================================================================
# ADMIN
# ===================================================================

async def admin_cmd(msg):

    t = msg.text or ""

    if t.startswith("/test"):

        chats = list(daily_messages.keys())

        if not chats:
            return await send_safe(
                ADMIN_ID,
                "Нет чатов"
            )

        cid = chats[0]

        await send_safe(
            ADMIN_ID,
            "🧪 Генерирую..."
        )

        result = await generate_digest(cid)

        if not result:
            return await send_safe(
                ADMIN_ID,
                "Ошибка"
            )

        for part in split_message(result):
            await send_safe(
                ADMIN_ID,
                part
            )

    elif t.startswith("/status"):

        lines = []

        for cid, msgs in daily_messages.items():
            lines.append(
                f"{cid}: {len(msgs)}"
            )

        await send_safe(
            ADMIN_ID,
            "\n".join(lines) or "Пусто"
        )

    elif t.startswith("/help"):

        await send_safe(
            ADMIN_ID,
            """
🤖 ЗЯБЛОГРАФ v4.2

/test — тестовый дайджест
/status — статистика
/help — помощь

Вестник-style digest engine
Event clustering
Character memory
Callback memory
"""
        )

# ===================================================================
# MAIN
# ===================================================================

async def main():

    logger.info("STARTING ZYABLOGRAF V4.2")

    load_data()

    await bot.initialize()

    asyncio.create_task(periodic_checker())

    offset = None

    while True:

        try:

            updates = await bot.get_updates(
                offset=offset,
                timeout=30,
                allowed_updates=["message"]
            )

            for u in updates:

                if not u.message:
                    continue

                if u.message.chat.id == ADMIN_ID:

                    await admin_cmd(u.message)

                else:

                    await handle_message(u.message)

                offset = u.update_id + 1

        except Exception as e:

            logger.error(f"MAIN LOOP ERROR: {e}")

            await asyncio.sleep(5)

# ===================================================================
# START
# ===================================================================

if __name__ == "__main__":
    asyncio.run(main())
