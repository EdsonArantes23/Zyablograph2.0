#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ЗЯБЛОГРАФ X — Telegram дайджест-бот в стиле Вестника

УЛУЧШЕНИЯ:
- DeepSeek V3 + Dolphin fallback
- Few-shot prompting
- Нормальная фильтрация событий
- Дешёвый расход токенов
- Better narrative extraction
- Минимум цензуры
- Better chunking
- Better anti-repeat
"""

import os
import re
import json
import asyncio
import random
import logging

from datetime import datetime, timedelta, timezone

from openai import OpenAI

from telegram import Bot
from telegram.error import TelegramError


# =========================================================
# CONFIG
# =========================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY missing")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

bot = Bot(token=BOT_TOKEN)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger("zyablograf")

MSK_TZ = timezone(timedelta(hours=3))

MESSAGES_FILE = "messages.json"

MAX_BUFFER_MESSAGES = 1500

DIGEST_TRIGGER = 250

MAX_CONTEXT_MESSAGES = 120

# =========================================================
# STORAGE
# =========================================================

daily_messages = {}

def save_messages():

    with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(
            daily_messages,
            f,
            ensure_ascii=False
        )

def load_messages():

    global daily_messages

    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            daily_messages = json.load(f)

        daily_messages = {
            int(k): v
            for k, v in daily_messages.items()
        }

        logger.info("Messages loaded")

    except:
        logger.info("Fresh start")


# =========================================================
# TIME
# =========================================================

def now():
    return datetime.now(MSK_TZ)


# =========================================================
# HELPERS
# =========================================================

def clean_text(text: str) -> str:

    text = text.strip()

    text = re.sub(
        r"[^\x00-\x7Fа-яА-ЯёЁ0-9\s.,!?;:()«»\"'@#%&*+=/\\\-\n]",
        "",
        text
    )

    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def split_message(text, limit=3800):

    if len(text) <= limit:
        return [text]

    chunks = []

    current = ""

    for line in text.split("\n"):

        if len(current) + len(line) < limit:
            current += line + "\n"

        else:
            chunks.append(current)
            current = line + "\n"

    if current:
        chunks.append(current)

    return chunks


async def safe_send(chat_id, text):

    for chunk in split_message(text):

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode=None
            )

        except TelegramError as e:
            logger.error(e)

        await asyncio.sleep(1)


# =========================================================
# MESSAGE FILTER
# =========================================================

def score_message(text: str):

    score = 0

    t = text.lower()

    keywords = [
        "бля",
        "нахуй",
        "еб",
        "пизд",
        "сука",
        "идиот",
        "дебил",
        "шиз",
        "орал",
        "сдох",
        "угар",
        "ахах",
        "😂",
        "🤣",
        "@",
        "http",
        "почему",
        "зачем",
        "кринж",
        "пиздец",
        "конфликт",
    ]

    for k in keywords:
        if k in t:
            score += 2

    if len(text) > 120:
        score += 1

    if text.count("?") >= 2:
        score += 2

    if text.count("!") >= 2:
        score += 2

    return score


def extract_best_messages(messages):

    scored = []

    for m in messages:

        s = score_message(m["text"])

        scored.append((s, m))

    scored.sort(key=lambda x: x[0], reverse=True)

    selected = [x[1] for x in scored[:MAX_CONTEXT_MESSAGES]]

    selected.sort(key=lambda x: x["id"])

    return selected


# =========================================================
# FEW SHOT
# =========================================================

FEW_SHOT = """
ПРИМЕР СТИЛЯ:

# (https://t.me/c/123/555)
@vasya снова устроил в чате промышленную катастрофу уровня районной психбольницы. После трёх сообщений про крипту человек внезапно перешёл к теории мирового заговора и был коллективно послан нахуй всеми участниками дискуссии. Очевидцы сообщают, что уровень стыда превысил санитарные нормы.

# (https://t.me/c/123/556)
@petya влетел в обсуждение как пьяный политолог с федерального канала и начал объяснять экономику людям, у которых хотя бы есть работа. Итогом мероприятия стало массовое охуевание аудитории и локальный кризис здравого смысла.

# (https://t.me/c/123/557)
После двух часов коллективной шизофрении чат пришёл к выводу, что никто нихуя не понял, но виноват почему-то опять @sergey.
"""


# =========================================================
# PROMPT
# =========================================================

def build_prompt(log_text):

    return f"""
Ты — Зяблограф.

Ты делаешь сатирический Telegram-дайджест в стиле Вестника.

СТИЛЬ:
- токсичный
- саркастичный
- чёрный юмор
- мат разрешён
- журналистская подача
- гиперболизация
- абсурд
- edgy internet humor

ПРАВИЛА:
- НЕ будь нейтральным
- НЕ пиши скучный summary
- НЕ используй официальный стиль
- НЕ морализируй
- НЕ извиняйся
- НЕ пиши "как ИИ"

ФОРМАТ:
Каждый абзац начинается так:

# (ссылка)

Потом идёт 2-5 предложений.

Разрешено:
- мат
- оскорбления
- токсичность
- жёсткие формулировки

Запрещено:
- повторять одно и то же
- выдумывать события
- писать слишком коротко

ВАЖНО:
Ты пишешь как циничный журналист Telegram-помойки.

{FEW_SHOT}

ЛОГ ЧАТА:

{log_text}
"""


# =========================================================
# LLM
# =========================================================

MODELS = [
    "deepseek/deepseek-chat-v3-0324",
    "cognitivecomputations/dolphin-mixtral-8x7b",
    "qwen/qwen-2.5-72b-instruct",
]

REFUSAL_PATTERNS = [
    "i cannot",
    "i can't",
    "i wont",
    "i'm sorry",
    "не могу",
    "не буду",
    "я не могу",
    "соблюдать",
    "политик",
    "safety",
    "harmful",
]

async def call_llm(prompt):

    loop = asyncio.get_event_loop()

    for model in MODELS:

        try:

            logger.info(f"Trying model: {model}")

            response = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Ты пишешь только на русском."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    temperature=1.15,
                    top_p=0.95,
                    max_tokens=1800
                )
            )

            text = response.choices[0].message.content

            if not text:
                continue

            lower = text.lower()

            refused = any(
                p in lower
                for p in REFUSAL_PATTERNS
            )

            if refused:
                logger.warning(f"Refusal from {model}")
                continue

            return clean_text(text)

        except Exception as e:

            logger.error(f"{model}: {e}")

            await asyncio.sleep(2)

    return None


# =========================================================
# DIGEST
# =========================================================

async def send_digest(chat_id):

    msgs = daily_messages.get(chat_id, [])

    if len(msgs) < 20:
        return

    msgs = msgs[-MAX_BUFFER_MESSAGES:]

    best = extract_best_messages(msgs)

    log_lines = []

    for m in best:

        line = (
            f"[{m['link']}] "
            f"@{m['author']}: "
            f"{m['text'][:400]}"
        )

        log_lines.append(line)

    log_text = "\n".join(log_lines)

    prompt = build_prompt(log_text)

    result = await call_llm(prompt)

    if not result:

        await safe_send(
            ADMIN_ID,
            "❌ Не удалось сгенерировать дайджест"
        )

        return

    header = random.choice([
        "📰 Экстренный выпуск Зяблографа",
        "📰 Главные катастрофы чата",
        "📰 Хроники коллективного безумия",
        "📰 Сводка психических происшествий",
    ])

    final_text = f"{header}\n\n{result}"

    await safe_send(chat_id, final_text)

    daily_messages[chat_id] = []

    save_messages()

    logger.info(f"Digest sent: {chat_id}")


# =========================================================
# MESSAGE HANDLER
# =========================================================

async def handle_message(msg):

    if not msg.from_user:
        return

    chat_id = msg.chat.id

    text = msg.text or msg.caption or ""

    if msg.photo:
        text += " [ФОТО]"

    if not text:
        text = "[стикер/войс]"

    author = (
        msg.from_user.username
        or msg.from_user.first_name
        or "anon"
    )

    link = (
        f"https://t.me/c/"
        f"{str(chat_id).replace('-100', '')}/"
        f"{msg.message_id}"
    )

    daily_messages.setdefault(chat_id, [])

    daily_messages[chat_id].append({
        "id": msg.message_id,
        "author": author,
        "text": text,
        "link": link,
        "timestamp": now().isoformat()
    })

    if len(daily_messages[chat_id]) > MAX_BUFFER_MESSAGES:

        daily_messages[chat_id] = daily_messages[chat_id][-MAX_BUFFER_MESSAGES:]

    save_messages()

    if len(daily_messages[chat_id]) >= DIGEST_TRIGGER:

        await send_digest(chat_id)


# =========================================================
# MAIN LOOP
# =========================================================

async def main():

    logger.info("Zyablograf started")

    load_messages()

    await bot.initialize()

    offset = None

    while True:

        try:

            updates = await bot.get_updates(
                offset=offset,
                timeout=30,
                allowed_updates=["message"]
            )

            for update in updates:

                offset = update.update_id + 1

                if not update.message:
                    continue

                await handle_message(update.message)

        except Exception as e:

            logger.error(e)

            await asyncio.sleep(5)


# =========================================================

if __name__ == "__main__":
    asyncio.run(main())
