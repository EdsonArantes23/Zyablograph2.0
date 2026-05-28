#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, re, logging, asyncio, random
from datetime import datetime, timedelta, timezone
from openai import OpenAI
from telegram import Bot
from telegram.error import TelegramError

# =========================================================
# CONFIG
# =========================================================

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "417850992"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")
if not OPENROUTER_API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY missing")

DICT_FILE = "dictionary.json"
MESSAGES_FILE = "daily_messages.json"
BOT_SETTINGS_KEY = "BOT_SETTINGS"

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Zyablograf")

MSK_TZ = timezone(timedelta(hours=3))

def msk_now():
    return datetime.now(MSK_TZ)

# =========================================================
# STORAGE
# =========================================================

daily_messages: dict[int, list[dict]] = {}
reactions: dict[int, list[dict]] = {}

def save_messages():
    try:
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "messages": {str(k): v for k, v in daily_messages.items()},
                "reactions": {str(k): v for k, v in reactions.items()}
            }, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"save error: {e}")

def load_messages():
    global daily_messages, reactions
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        daily_messages = {int(k): v for k, v in data.get("messages", {}).items()}
        reactions = {int(k): v for k, v in data.get("reactions", {}).items()}
    except:
        daily_messages = {}
        reactions = {}

# =========================================================
# SETTINGS (ВАЖНО: исправлен баг с getenv JSON)
# =========================================================

def load_all():
    try:
        raw = os.getenv(BOT_SETTINGS_KEY, "{}")
        return json.loads(raw) if raw else {}
    except:
        return {}

def save_all(data):
    os.environ[BOT_SETTINGS_KEY] = json.dumps(data, ensure_ascii=False)

def load_chats():
    return load_all().get("chats", [])

def save_chats(chats):
    d = load_all()
    d["chats"] = chats
    save_all(d)

def load_names():
    return load_all().get("names", {})

def save_names(names):
    d = load_all()
    d["names"] = names
    save_all(d)

def load_settings():
    d = load_all()
    return d.get("settings", {
        "send_hour": 18,
        "send_minute": 0,
        "mood": "hard",
        "raid_enabled": True,
        "raid_min_hours": 2,
        "raid_max_hours": 12
    })

def save_settings(s):
    d = load_all()
    d["settings"] = s
    save_all(d)

# =========================================================
# USER DISPLAY
# =========================================================

def get_display_name(user, include_meta=True):
    names = load_names()
    uid = str(user.id)

    if uid not in names:
        return user.first_name or user.username or "Анон"

    name = names[uid].get("name") or user.first_name or user.username or "Анон"

    if not include_meta:
        return name

    parts = [f"@{name}"]

    g = names[uid].get("gender")
    if g:
        parts.append(f"[{g}]")

    desc = names[uid].get("description")
    if desc:
        parts.append(f", {desc}")

    return "".join(parts)

# =========================================================
# LLM
# =========================================================

async def call_llm(prompt):
    models = [
        "qwen/qwen-2.5-72b-instruct",
        "cognitivecomputations/dolphin-mixtral-8x7b",
        "meta-llama/llama-3.1-8b-instruct"
    ]

    for model in models:
        try:
            loop = asyncio.get_event_loop()

            res = await loop.run_in_executor(
                None,
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=1.0,
                    max_tokens=2000
                )
            )

            return res.choices[0].message.content

        except Exception as e:
            logger.error(f"{model}: {e}")

    return None

# =========================================================
# DIGEST
# =========================================================

async def send_digest(chat_id):
    msgs = daily_messages.get(chat_id, [])
    if len(msgs) < 5:
        return

    log = "\n".join(
        f"[{m['link']}] @{m['author']}: {m['text'][:150]}"
        for m in msgs[-80:]
    )

    prompt = f"""
Ты Зяблограф — сатирический Telegram-обозреватель.

Сделай жёсткий дайджест без выдумок.

ЛОГ:
{log}
"""

    result = await call_llm(prompt)
    if not result:
        return

    await bot.send_message(chat_id, result)

    daily_messages[chat_id] = []
    save_messages()

# =========================================================
# RAID
# =========================================================

async def send_raid(cid):
    msgs = daily_messages.get(cid, [])
    if len(msgs) < 5:
        return

    log = "\n".join(
        f"@{m['author']}: {m['text'][:120]}"
        for m in msgs[-40:]
    )

    prompt = f"""
Жёсткий наезд на чат.

ЛОГ:
{log}
"""

    res = await call_llm(prompt)
    if res:
        await bot.send_message(cid, res)

# =========================================================
# ADMIN COMMANDS (ИСПРАВЛЕНО: from_user.id вместо chat.id)
# =========================================================

async def admin_cmd(msg):
    t = msg.text or ""
    p = t.split()

    # ===== ID =====
    if t == "/id":
        user = msg.reply_to_message.from_user if msg.reply_to_message else msg.from_user
        await bot.send_message(msg.chat.id, str(user.id))
        return

    # ===== CHATS =====
    if t.startswith("/add_chat"):
        c = int(p[1])
        chats = load_chats()
        if c not in chats:
            chats.append(c)
            save_chats(chats)
        return

    if t.startswith("/remove_chat"):
        c = int(p[1])
        chats = load_chats()
        if c in chats:
            chats.remove(c)
            save_chats(chats)
        return

    if t == "/list_chats":
        await bot.send_message(msg.chat.id, str(load_chats()))
        return

    # ===== SETTINGS =====
    if t.startswith("/settime"):
        h, m = map(int, p[1].split(":"))
        s = load_settings()
        s["send_hour"] = h
        s["send_minute"] = m
        save_settings(s)
        return

    if t.startswith("/mood"):
        s = load_settings()
        s["mood"] = p[1]
        save_settings(s)
        return

    # ===== RAID =====
    if t.startswith("/raid"):
        s = load_settings()

        if p[1] == "on":
            s["raid_enabled"] = True

        elif p[1] == "off":
            s["raid_enabled"] = False

        elif p[1] == "now":
            cid = int(p[2]) if len(p) > 2 else (load_chats() or [None])[0]
            if cid:
                await send_raid(cid)

        save_settings(s)
        return

    # ===== RESET =====
    if t.startswith("/reset"):
        cid = int(p[1]) if len(p) > 1 else None
        if cid:
            daily_messages[cid] = []
        else:
            daily_messages.clear()
        save_messages()
        return

    # ===== STATUS =====
    if t == "/status":
        s = load_settings()
        await bot.send_message(
            msg.chat.id,
            f"msgs: {sum(len(v) for v in daily_messages.values())}\n"
            f"chats: {len(load_chats())}\n"
            f"mood: {s['mood']}\n"
            f"raid: {s['raid_enabled']}"
        )
        return

# =========================================================
# MESSAGE HANDLER
# =========================================================

async def handle_message(msg):
    cid = msg.chat.id

    if cid not in load_chats() and msg.from_user.id != ADMIN_ID:
        return

    text = msg.text or msg.caption or "[empty]"

    daily_messages.setdefault(cid, []).append({
        "author": get_display_name(msg.from_user),
        "text": text,
        "link": f"https://t.me/c/{str(cid).replace('-100','')}/{msg.message_id}"
    })

    save_messages()

# =========================================================
# MAIN LOOP
# =========================================================

async def main():
    await bot.initialize()
    load_messages()

    offset = None

    logger.info("Zyablograf started")

    while True:
        updates = await bot.get_updates(offset=offset, timeout=30)

        for u in updates:
            if not u.message:
                continue

            offset = u.update_id + 1
            msg = u.message

            # FIX: правильная проверка админа
            if msg.from_user and msg.from_user.id == ADMIN_ID and msg.chat.type == "private":
                await admin_cmd(msg)
            else:
                await handle_message(msg)

if __name__ == "__main__":
    asyncio.run(main())
