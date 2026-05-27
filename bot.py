#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Зяблограф — сатирический дайджест-бот в стиле Вестника"""

import os, json, re, base64, logging, asyncio, random, time
from datetime import datetime, timedelta, timezone
from openai import OpenAI
from telegram import Bot
from telegram.error import TelegramError

# ========== КОНФИГУРАЦИЯ ==========
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "417850992"))
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TOKEN")
if not BOT_TOKEN: raise RuntimeError("Укажи BOT_TOKEN в Bothost!")
if not OPENROUTER_API_KEY: raise RuntimeError("Укажи OPENROUTER_API_KEY в Bothost!")

DICT_FILE = "dictionary.json"
MESSAGES_FILE = "daily_messages.json"
BOT_SETTINGS_KEY = "BOT_SETTINGS"

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
bot = Bot(token=BOT_TOKEN)

daily_messages: dict[int, list[dict]] = {}
reactions: dict[int, list[dict]] = {}
digest_sent_today: dict[int, datetime.date] = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logger = logging.getLogger("Zyablograf")
MSK_TZ = timezone(timedelta(hours=3))
def msk_now() -> datetime: return datetime.now(MSK_TZ)

# ========== СОХРАНЕНИЕ ДАННЫХ ==========
def save_messages_to_disk():
    try:
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump({"messages": {str(k): v for k, v in daily_messages.items()}, 
                       "reactions": {str(k): v for k, v in reactions.items()}}, f, ensure_ascii=False)
    except Exception as e: logger.error(f"Save error: {e}")

def load_messages_from_disk():
    global daily_messages, reactions
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        daily_messages = {int(k): v for k, v in data.get("messages", {}).items()}
        reactions = {int(k): v for k, v in data.get("reactions", {}).items()}
        logger.info(f"✓ Loaded {sum(len(v) for v in daily_messages.values())} messages")
    except FileNotFoundError: logger.info("ℹ Starting fresh")
    except Exception as e: logger.error(f"Load error: {e}")

# ========== СЛОВАРЬ (ИСПРАВЛЕНИЕ ПРОБЕЛОВ) ==========
def load_dictionary() -> dict:
    try:
        with open(DICT_FILE, "r", encoding="utf-8") as f: raw = json.load(f)
        return {k.strip(): [v.strip() for v in vals if v.strip()] for k, vals in raw.items()}
    except:
        return {"существительные": ["пиздец", "хуй"], "глаголы": ["обосрался"]}
SWEAR_DICT = load_dictionary()

# ========== НАСТРОЙКИ ==========
def load_all_data() -> dict:
    try: return json.loads(os.getenv(BOT_SETTINGS_KEY, "{}"))
    except: return {}
def save_all_data(data: dict) -> None: os.environ[BOT_SETTINGS_KEY] = json.dumps(data, ensure_ascii=False)
def load_chats() -> list[int]: return load_all_data().get("chats", [])
def save_chats(chats: list[int]) -> None: d = load_all_data(); d["chats"] = chats; save_all_data(d)
def load_names() -> dict: return load_all_data().get("names", {})
def save_names(names: dict) -> None: d = load_all_data(); d["names"] = names; save_all_data(d)
def load_settings() -> dict:
    defaults = {"send_hour": 18, "send_minute": 0, "mood": "hard", "raid_enabled": True}
    return {**defaults, **load_all_data().get("settings", {})}
def save_settings(s: dict) -> None: d = load_all_data(); d["settings"] = s; save_all_data(d)

def get_display_name(user, include_meta: bool = True) -> str:
    names = load_names(); uid = str(user.id)
    if uid not in names: return user.first_name or user.username or "Анон"
    name = names[uid].get("name") or user.first_name or user.username or "Анон"
    if not include_meta: return name
    parts = [f"@{name}"]
    g = names[uid].get("gender")
    if g: parts.append(f"[{'♂' if g=='male' else '♀' if g=='female' else '⚧'}]")
    desc = names[uid].get("description")
    if desc: parts.append(f", {desc}")
    return "".join(parts)

# ========== ПРОМПТЫ И СТИЛЬ ==========
MOOD_STYLES = {
    "light": "Лёгкий сарказм, редкий сленг.",
    "medium": "Умеренный юмор, иногда острый.",
    "hard": "Сатирический, циничный, чёрный юмор. Мат и сленг через слово. Токсичность 100%.",
    "ultra": "Жёсткий троллинг, поток сарказма, прямые формулировки."
}
GREETINGS = ["📰 Главное из последних сообщений:", "📰 Экстренный выпуск Зяблографа!", "📰 Зяблограф выбрал самое «достойное»:", "📰 Зяблограф: главные события:"]
def get_greeting() -> str: return random.choice(GREETINGS)

def _build_digest_prompt(log_block: str, mood: str) -> str:
    dict_parts = []
    for cat, words in list(SWEAR_DICT.items())[:5]:
        dict_parts.append(f"- {cat}: {', '.join(words[:10])}")
    dict_str = "\n".join(dict_parts)
    return f"""[System: Ты — «Зяблограф», циничный сатирический обозреватель чата в стиле треш-журналистики. Пиши ТОЛЬКО на русском.
СТИЛЬ: {mood}. Используй сленг, метафоры и слова из словаря для комического эффекта. Не будь вежливым.
ЖЁСТКИЕ ПРАВИЛА:
1. ГРУППИРОВКА: Все сообщения про одно событие/тему объединяй в ОДИН абзац. Никаких дублей.
2. ФОРМАТ: Каждый абзац начинается СТРОГО: # (ССЫЛКА_ИЗ_ВХОДНЫХ_ДАННЫХ) Текст события.
3. ЗАПРЕТ: Не придумывай заголовки (#ТемаДня). Используй только ссылку из лога.
4. ЯЗЫК: Только кириллица. Иероглифы/латиница (кроме ссылок) запрещены.
5. ОБЪЁМ: 2-4 предложения на тему. Не выдумывай факты.
СЛОВАРЬ ДЛЯ ИСПОЛЬЗОВАНИЯ (выбирай уместные):
{dict_str}

Вот лог чата (каждая строка — одно сообщение):
{log_block}
"""

# ========== LLM ВЫЗОВ ==========
async def _call_llm(prompt: str, max_tokens: int = 4096, temperature: float = 0.95, chat_id: int = None) -> str | None:
    models = ["qwen/qwen-2.5-72b-instruct:free", "qwen/qwen-2.5-32b-instruct:free", "meta-llama/llama-3.1-8b-instruct:free"]
    prompt = prompt.replace('\\', '\\\\').replace('"', '\\"')[:80000]
    
    for model in models:
        for attempt in range(2):
            try:
                loop = asyncio.get_event_loop()
                comp = await loop.run_in_executor(None, lambda: client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}],
                    temperature=temperature, max_tokens=max_tokens,
                    extra_headers={"HTTP-Referer": "https://t.me/zyablograf_bot"}
                ))
                text = comp.choices[0].message.content.strip()
                if any(w in text.lower() for w in ["sorry", "cannot", "не могу", "отказ", "safety", "i can't"]):
                    logger.warning(f"⚠ Refusal on {model}")
                    break
                return _clean_output(text)
            except Exception as e:
                err = str(e).lower()
                if "400" in err or "401" in err: break
                if "402" in err or "insufficient" in err: logger.error("✗ Balance low!"); break
                if "rate_limit" in err or "429" in err: await asyncio.sleep(10); break
                await asyncio.sleep(2)
    try: await bot.send_message(ADMIN_ID, f"⚠️ Зяблограф не смог сгенерировать текст. Проверь баланс/лог.", parse_mode=None)
    except: pass
    return None

def _clean_output(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[^\x00-\x7Fа-яА-ЯёЁ0-9\s.,!?;:()«»""''\-—@#$/\n\r]', '', text)
    text = re.sub(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+', '', text)
    text = re.sub(r'#\s+(https?://t\.me/\S+)', r'# (\1)', text)
    if "⭐️ Станьте спонсором" in text: text = text.split("⭐️ Станьте спонсором")[0].strip()
    return text.strip()

def _escape_md(text: str) -> str:
    esc = r'_*~`>#+-=|{}.![]()'
    buf, i = [], 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text) and text[i+1] in esc: buf.append(text[i:i+2]); i += 2
        elif text[i] in esc: buf.append('\\' + text[i]); i += 1
        else: buf.append(text[i]); i += 1
    return ''.join(buf)

def _split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len: return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 2 <= max_len: cur = (cur + "\n" + line).strip()
        else:
            if cur: parts.append(cur)
            cur = line
    if cur: parts.append(cur)
    return parts

async def _send_safe(cid: int, text: str, parse_mode: str | None = "MarkdownV2", thread: int | None = 1):
    try:
        if parse_mode == "MarkdownV2": text = _escape_md(text)
        return await bot.send_message(cid, text, parse_mode=parse_mode, message_thread_id=thread)
    except TelegramError as e:
        err = str(e).lower()
        if "thread" in err: return await _send_safe(cid, text, parse_mode, None)
        if parse_mode and ("markdown" in err or "parse" in err): return await _send_safe(cid, text, None, thread)
        return None

# ========== ФИЛЬТРАЦИЯ И ДАЙДЖЕСТ ==========
def _filter_messages(msgs: list[dict], max_n: int = 30) -> list[dict]:
    hot = re.compile(r'бля|хуй|пизд|еба|сука|нах|почему|кто|где|когда|зачем|@\w+|https?://|\[ФОТО:', re.I)
    res = [m for m in msgs if hot.search(m.get("text", ""))]
    return (res or msgs)[-max_n:]

async def _send_digest(cid: int) -> None:
    msgs = daily_messages.get(cid, [])
    if len(msgs) < 5: return
    filtered = _filter_messages(msgs[-500:], 30)
    log = "\n".join(f"[{m['link']}] @{m['author']}: {m['text'][:150]}" for m in filtered)
    s = load_settings()
    mood = MOOD_STYLES.get(s.get("mood", "hard"), MOOD_STYLES["hard"])
    res = await _call_llm(_build_digest_prompt(log, mood), chat_id=cid)
    if not res: return
    
    full = f"{get_greeting()}\n\n{res}"
    for part in _split_message(full, 4000):
        await _send_safe(cid, part)
        await asyncio.sleep(1.5)
    daily_messages[cid] = []; reactions[cid] = []
    digest_sent_today.pop(cid, None)
    save_messages_to_disk()
    logger.info(f"✓ Digest sent to {cid}")

async def _check_and_send_if_needed(cid: int) -> bool:
    msgs = daily_messages.get(cid, [])
    if not msgs: return False
    if len(msgs) >= 1000:
        logger.info(f"📊 1000 msgs trigger → {cid}")
        await _send_digest(cid); return True
    ts = msgs[0].get("timestamp")
    if ts and (msk_now() - datetime.fromisoformat(ts)).total_seconds() >= 86400:
        logger.info(f"⏱ 24h trigger → {cid}")
        await _send_digest(cid); return True
    return False

async def _digest_periodic_checker():
    while True:
        await asyncio.sleep(60)
        now, s = msk_now(), load_settings()
        for cid in list(daily_messages.keys()):
            if await _check_and_send_if_needed(cid): continue
            if now.hour == s["send_hour"] and now.minute == s["send_minute"] and digest_sent_today.get(cid) != now.date():
                logger.info(f"⏰ Time trigger → {cid}")
                await _send_digest(cid); digest_sent_today[cid] = now.date()

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def _handle_msg(msg):
    cid = msg.chat.id
    if cid not in load_chats() or not msg.from_user: return
    if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == bot.id:
        reactions.setdefault(cid, []).append({"author": get_display_name(msg.from_user), "text": (msg.text or msg.caption or "[без текста]").strip()})
    
    author = get_display_name(msg.from_user)
    text = msg.text or msg.caption or ""
    if msg.forward_origin:
        fo = msg.forward_origin
        if hasattr(fo, "sender_user") and fo.sender_user: author = f"↪️ {get_display_name(fo.sender_user)}"
        elif hasattr(fo, "chat") and fo.chat: author = f"↪️ {fo.chat.title or 'Канал'}"
    if msg.photo: text = f"{text}\n[ФОТО]" if text else "[ФОТО]"
    if not text: text = "[войс/стикер]"
    
    link = f"https://t.me/c/{str(cid).replace('-100','')}/{msg.message_id}"
    daily_messages.setdefault(cid, []).append({"link": link, "author": author, "text": text.strip(), "user_id": msg.from_user.id, "timestamp": msk_now().isoformat()})
    save_messages_to_disk()
    await _check_and_send_if_needed(cid)

# ========== АДМИН-КОМАНДЫ ==========
async def _admin_cmd(msg):
    cmd = (msg.text or "").strip()
    if not cmd: return
    logger.info(f"🔧 Admin command: {cmd[:100]}")
    
    try:
        if cmd == "/id" or msg.forward_origin:
            user = None
            if msg.forward_origin:
                fo = msg.forward_origin
                if hasattr(fo, 'sender_user') and fo.sender_user: user = fo.sender_user
                elif hasattr(fo, 'chat') and fo.chat: return await _send_safe(ADMIN_ID, f"📢 Канал: {fo.chat.title}\n🆔 ID: `{fo.chat.id}`", parse_mode=None)
            elif msg.reply_to_message and msg.reply_to_message.from_user: user = msg.reply_to_message.from_user
            elif msg.from_user: user = msg.from_user
            if not user: return await _send_safe(ADMIN_ID, "❌ Не удалось определить пользователя", parse_mode=None)
            uid, names = str(user.id), load_names()
            meta = ""
            if uid in names:
                if names[uid].get("name"): meta += f"\n🏷️ Имя: {names[uid]['name']}"
                if names[uid].get("description"): meta += f"\n📝 Описание: {names[uid]['description']}"
                if names[uid].get("gender"): meta += f"\n⚧ Пол: {'♂ Муж' if names[uid]['gender']=='male' else '♀ Жен' if names[uid]['gender']=='female' else '⚧ Другое'}"
            return await _send_safe(ADMIN_ID, f"🆔 {user.first_name} {user.last_name or ''}\n🔖 @{user.username or 'нет'}\n🆔 ID: `{uid}`{meta}", parse_mode=None)

        p = cmd.split()
        if cmd.startswith("/add_chat"):
            c = int(p[1]) if len(p)>1 else (msg.reply_to_message.chat.id if msg.reply_to_message else None)
            if not c: return await _send_safe(ADMIN_ID, "❌ /add_chat ID", parse_mode=None)
            chats = load_chats()
            if c not in chats: chats.append(c); save_chats(chats); await _send_safe(ADMIN_ID, f"✅ Чат {c} добавлен!", parse_mode=None)
            else: await _send_safe(ADMIN_ID, "⚠️ Уже в списке", parse_mode=None)
        elif cmd.startswith("/remove_chat"):
            c = int(p[1]) if len(p)>1 else None
            if c: chats = load_chats(); chats.remove(c) if c in chats else None; save_chats(chats); await _send_safe(ADMIN_ID, f"✅ Чат {c} удалён", parse_mode=None)
        elif cmd.startswith("/list_chats"):
            chats = load_chats()
            await _send_safe(ADMIN_ID, "📋 Чаты:\n" + "\n".join(f"  • {c} ({len(daily_messages.get(c,[]))} msg)" for c in chats) if chats else "📋 Нет чатов", parse_mode=None)
        elif cmd.startswith("/settime"):
            if len(p)<2 or not re.match(r'^\d{1,2}:\d{2}$', p[1]): return await _send_safe(ADMIN_ID, "❌ /settime ЧЧ:ММ", parse_mode=None)
            h,m = map(int, p[1].split(":"))
            if 0<=h<=23 and 0<=m<=59: s=load_settings(); s["send_hour"],s["send_minute"]=h,m; save_settings(s); await _send_safe(ADMIN_ID, f"✅ Время: {h:02d}:{m:02d} МСК", parse_mode=None)
        elif cmd.startswith("/mood"):
            if len(p)<2: return await _send_safe(ADMIN_ID, f"Текущий: {load_settings().get('mood','hard')}\nlight/medium/hard/ultra", parse_mode=None)
            if p[1].lower() in MOOD_STYLES: s=load_settings(); s["mood"]=p[1].lower(); save_settings(s); await _send_safe(ADMIN_ID, f"✅ {p[1].upper()}", parse_mode=None)
        elif cmd.startswith("/setname") or cmd.startswith("/setdesc") or cmd.startswith("/setgender"):
            if len(p)<3: return await _send_safe(ADMIN_ID, f"❌ {p[0]} ID ЗНАЧЕНИЕ", parse_mode=None)
            uid, val = p[1], p[2].strip('"').strip("'")
            key = "name" if "name" in p[0] else "description" if "desc" in p[0] else "gender"
            names = load_names(); names.setdefault(uid, {}); names[uid][key] = val.lower() if key=="gender" else val
            save_names(names); await _send_safe(ADMIN_ID, f"✅ {key} для {uid}: «{val}»", parse_mode=None)
        elif cmd.startswith("/removename") or cmd.startswith("/removedesc") or cmd.startswith("/removegender"):
            if len(p)<2: return await _send_safe(ADMIN_ID, f"❌ {p[0]} ID", parse_mode=None)
            uid, key = p[1], "name" if "name" in p[0] else "description" if "desc" in p[0] else "gender"
            names = load_names()
            if uid in names and key in names[uid]: del names[uid][key]; save_names(names); await _send_safe(ADMIN_ID, f"✅ Удалено", parse_mode=None)
            else: await _send_safe(ADMIN_ID, f"⚠️ Нет данных", parse_mode=None)
        elif cmd.startswith("/list_users"):
            uid = p[1] if len(p)>1 else None; names = load_names()
            if uid:
                if uid not in names: return await _send_safe(ADMIN_ID, f"⚠️ Нет данных для {uid}", parse_mode=None)
                d = names[uid]; g = {"male":"♂","female":"♀","other":"⚧"}.get(d.get("gender"), "—")
                await _send_safe(ADMIN_ID, f"📋 {uid}:\n  • Имя: {d.get('name','—')}\n  • Описание: {d.get('description','—')}\n  • Пол: {g}", parse_mode=None)
            else:
                lines = ["📋 Пользователи:"]
                for u,d in sorted(names.items()): lines.append(f"  • {u}: «{d.get('name','—')}» {d.get('gender','—')} — {d.get('description','—')}")
                await _send_safe(ADMIN_ID, "\n".join(lines), parse_mode=None)
        elif cmd.startswith("/test"):
            cid = int(p[1]) if len(p)>1 else (load_chats() or [None])[0]; cnt = int(p[2]) if len(p)>2 else 10
            if not cid: return await _send_safe(ADMIN_ID, "❌ Нет чатов", parse_mode=None)
            msgs = daily_messages.get(cid, [])
            if len(msgs)<5: return await _send_safe(ADMIN_ID, f"❌ Всего {len(msgs)} сообщ. (нужно ≥5)", parse_mode=None)
            await _send_safe(ADMIN_ID, "🧪 Генерирую...", parse_mode=None)
            await _send_digest(cid)
        elif cmd.startswith("/status"):
            s = load_settings(); lines = ["📊 Статистика:"]
            for cid,msgs in daily_messages.items(): lines.append(f"  Чат {cid}: {len(msgs)} сообщ.")
            if not daily_messages: lines.append("  Пусто.")
            lines += [f"\n⏰ Время: {s['send_hour']:02d}:{s['send_minute']:02d} МСК", f"🎭 Стиль: {s.get('mood','hard').upper()}"]
            await _send_safe(ADMIN_ID, "\n".join(lines), parse_mode=None)
        elif cmd.startswith("/reset"):
            cid = int(p[1]) if len(p)>1 else None
            if cid: daily_messages[cid]=[]; reactions[cid]=[]
            else: daily_messages.clear(); reactions.clear()
            save_messages_to_disk(); await _send_safe(ADMIN_ID, "🗑️ Сброшено", parse_mode=None)
        elif cmd.startswith("/help"):
            s = load_settings()
            await _send_safe(ADMIN_ID, f"""🛠 ЗЯБЛОГРАФ — ПОЛНАЯ СПРАВКА
📰 ДАЙДЖЕСТЫ: ⏰ /settime ЧЧ:ММ (сейчас {s['send_hour']:02d}:{s['send_minute']:02d}) | Триггеры: 1000 сообщ. ИЛИ 24ч ИЛИ /settime
🤬 РЕЙДЫ: /raid on|off | /raid_timer МИН МАКС (часы)
🏷️ ПОЛЬЗОВАТЕЛИ: /setname ID "Имя" | /setdesc ID "Описание" | /setgender ID male|female|other | /list_users [ID]
⚙️ ПРОЧЕЕ: /mood light|medium|hard|ultra | /add_chat|remove_chat|list_chats | /test [чат] [кол-во] | /status | /reset
💡 Все команды — только в ЛС боту.""", parse_mode=None)
    except Exception as e:
        logger.error(f"✗ Admin cmd error: {e}")
        await _send_safe(ADMIN_ID, f"❌ Ошибка: {e}", parse_mode=None)

# ========== ЗАПУСК ==========
async def main():
    logger.info("🚀 Зяблограф запущен! Бюджет оптимизирован (:free модели).")
    await bot.initialize()
    load_messages_from_disk()
    for cid in load_chats(): daily_messages.setdefault(cid, []); reactions.setdefault(cid, [])
    asyncio.create_task(_digest_periodic_checker())
    
    offset = None
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30, allowed_updates=["message"])
            for u in updates:
                if u.message:
                    if u.message.chat.id == ADMIN_ID: await _admin_cmd(u.message)
                    else: await _handle_msg(u.message)
                offset = u.update_id + 1
        except Exception as e:
            logger.error(f"Update error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
