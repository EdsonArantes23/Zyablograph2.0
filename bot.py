import os, json, re, base64, logging, asyncio, random
from datetime import datetime, timedelta, timezone
from openai import OpenAI
from telegram import Bot, Message
from telegram.error import TelegramError

# ========== НАСТРОЙКИ ==========
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
MSK_TZ = timezone(timedelta(hours=3))
def msk_now() -> datetime: return datetime.now(MSK_TZ)

# ========== СОХРАНЕНИЕ ==========
def save_messages_to_disk():
    try:
        with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
            json.dump({"messages": {str(k): v for k, v in daily_messages.items()}, "reactions": {str(k): v for k, v in reactions.items()}}, f, ensure_ascii=False)
    except Exception as e: logger.error(f"Save error: {e}")

def load_messages_from_disk():
    global daily_messages, reactions
    try:
        with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        daily_messages = {int(k): v for k, v in data.get("messages", {}).items()}
        reactions = {int(k): v for k, v in data.get("reactions", {}).items()}
    except: logger.info("Starting fresh")

# ========== СЛОВАРЬ ==========
def load_dictionary():
    try:
        with open(DICT_FILE, "r", encoding="utf-8") as f: return json.load(f)
    except: return {"существительные": ["пиздец", "хуй", "дебил"], "глаголы": ["обосрался", "охуел"]}
SWEAR_DICT = load_dictionary()

# ========== НАСТРОЙКИ ==========
def load_all_data():
    try: return json.loads(os.getenv(BOT_SETTINGS_KEY, "{}"))
    except: return {}
def save_all_data(data): os.environ[BOT_SETTINGS_KEY] = json.dumps(data, ensure_ascii=False)
def load_chats(): return load_all_data().get("chats", [])
def save_chats(chats): d = load_all_data(); d["chats"] = chats; save_all_data(d)
def load_names(): return load_all_data().get("names", {})
def save_names(names): d = load_all_data(); d["names"] = names; save_all_data(d)
def load_settings():
    defaults = {"send_hour": 18, "send_minute": 0, "mood": "hard", "raid_enabled": True, "raid_min_hours": 2, "raid_max_hours": 12}
    return {**defaults, **load_all_data().get("settings", {})}
def save_settings(s): d = load_all_data(); d["settings"] = s; save_all_data(d)

def get_display_name(user, include_meta: bool = True) -> str:
    names = load_names()
    uid = str(user.id)
    if uid not in names: return user.first_name or user.username or "Анон"
    name = names[uid].get("name") or user.first_name or user.username or "Анон"
    if not include_meta: return name
    parts = [f"@{name}"]
    gender = names[uid].get("gender")
    if gender: parts.append(f"[{'♂' if gender=='male' else '♀' if gender=='female' else '⚧'}]")
    desc = names[uid].get("description")
    if desc: parts.append(f", {desc}")
    return "".join(parts)

# ========== ПРОМПТЫ ==========
MOOD = {"light": "Лёгкий мат, ирония.", "medium": "Умеренный мат.", "hard": "ЖЁСТКИЙ МАТ ЧЕРЕЗ СЛОВО. Токсичность 100%. ОБСЁРАЙ, не пересказывай.", "ultra": "Ультра-жёсткий поток грязи."}
GREETINGS = ["📰 Главное из последних сообщений:", "📰 Экстренный выпуск Зяблографа!", "📰 Зяблограф выбрал самое «достойное»:", "📰 Зяблограф: главные события:"]
def get_mood(m): return MOOD.get(m, MOOD["hard"])
def get_greeting(): return random.choice(GREETINGS)

MAIN_PROMPT = """[System: Ты — «Зяблограф». Пишешь ТОЛЬКО на русском с матом и сарказмом.
ПРАВИЛА:
1. ФОРМАТ: Каждый абзац начинается СТРОГО: # (ССЫЛКА) Текст. Никаких #ТемаДня.
2. ГРУППИРОВКА: Все сообщения про одно событие — в ОДИН абзац. Без дублей.
3. СТИЛЬ: {mood} Мат через слово. Называй вещи своими именами.
4. ЯЗЫК: ТОЛЬКО РУССКИЙ. Без иероглифов.
5. ОБЪЁМ: 2-4 предложения на тему.
Вот лог чата:
"""
RAID_PROMPT = """[System: Ты — «Зяблограф», врываешься с наездом. Только русский мат.
{mood}
Выбери 1-2 героев и ЖЁСТКО НАЕЗЖАЙ. Начинай с «О, блядь, @username...». 4-7 предложений, без ссылок.
Вот лог:
"""

# ========== LLM ВЫЗОВ ==========
async def _call_llm(prompt: str, max_tokens: int = 6000, temperature: float = 0.95, chat_id: int = None) -> str:
    model = "qwen/qwen-2.5-72b-instruct"
    prompt = prompt.replace('\\', '\\\\').replace('"', '\\"')[:100000]
    for attempt in range(3):
        try:
            comp = await asyncio.get_event_loop().run_in_executor(None, lambda: client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}],
                temperature=temperature, max_tokens=max_tokens))
            return _clean(comp.choices[0].message.content)
        except Exception as e:
            logger.error(f"LLM error: {e}")
            if "400" in str(e) or "401" in str(e) or "402" in str(e):
                try: await bot.send_message(ADMIN_ID, f"⚠️ OpenRouter ошибка: {e}", parse_mode=None)
                except: pass
                return None
            await asyncio.sleep(2)
    return None

def _clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[^\x00-\x7Fа-яА-ЯёЁ0-9\s.,!?;:()«»""''-—@#\n\r]', '', text)
    text = re.sub(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+', '', text)
    text = re.sub(r'#\s+(https?://t.me/\S+)', r'# (\1)', text)
    text = re.sub(r'#[^(\s]+\s*(https?://t.me/\S+)', r'# (\1)', text)
    if "⭐️ Станьте спонсором" in text: text = text.split("⭐️ Станьте спонсором")[0].strip()
    return text

def _format(text: str) -> str: return re.sub(r'#\s*((https?://t.me/[^\s)]+))', r'[#](\1)', text)

# ========== ФОТО ==========
async def _describe_photo(file_id: str) -> str:
    try:
        file = await bot.get_file(file_id)
        img = base64.b64encode(await file.download_as_bytearray()).decode()
        comp = await asyncio.get_event_loop().run_in_executor(None, lambda: client.chat.completions.create(
            model="qwen/qwen-2.5-vl-72b-instruct",
            messages=[{"role": "user", "content": [{"type": "text", "text": "Опиши фото. Только на русском, можно с матом."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}],
            temperature=0.7, max_tokens=300))
        return f"[ФОТО: {_clean(comp.choices[0].message.content)}]"
    except: return "[ФОТО: не удалось описать]"

# ========== ФИЛЬТРАЦИЯ ==========
def _filter(msgs, max_n=30):
    hot = re.compile(r'бля|хуй|пизд|еба|сука|нах|почему|кто|где|когда|зачем|@\w+|https?://|\[ФОТО:', re.I)
    res = [m for m in msgs if hot.search(m.get("text", ""))]
    return (res or msgs)[-max_n:]

# ========== ГЕНЕРАЦИЯ ==========
def _build_prompt(cid):
    s = load_settings()
    mood = get_mood(s.get("mood", "hard"))
    p = MAIN_PROMPT.replace("{mood}", mood)
    if s.get("mood") == "hard": p += "\n⚠️ РЕЖИМ HARD: МАТ ЧЕРЕЗ СЛОВО, ТОКСИЧНОСТЬ 100%."
    return p

async def _gen_digest(log, cid):
    res = await _call_llm(_build_prompt(cid) + log, max_tokens=8000, chat_id=cid)
    return res if res else "Зяблограф обосрался. Технический пиздец."

async def _gen_raid(log, cid):
    s = load_settings()
    mood = get_mood(s.get("mood", "hard"))
    p = RAID_PROMPT.replace("{mood}", mood)
    res = await _call_llm(p + log, max_tokens=2000, temperature=1.0, chat_id=cid)
    return res if res else "Зяблограф обосрался. Технический пиздец."

# ========== ОТПРАВКА ==========
def _split(text, max_len=4000):
    if len(text) <= max_len: return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 2 <= max_len: cur = (cur + "\n" + line).strip()
        else:
            if cur: parts.append(cur)
            cur = line
    if cur: parts.append(cur)
    return parts

async def _send_safe(cid, text, parse_mode="MarkdownV2", thread=1):
    try:
        if parse_mode == "MarkdownV2": text = _escape_md(text)
        return await bot.send_message(cid, text, parse_mode=parse_mode, message_thread_id=thread)
    except TelegramError as e:
        if "thread" in str(e).lower():
            try: return await bot.send_message(cid, text, parse_mode=parse_mode)
            except: pass
        if parse_mode:
            try: return await bot.send_message(cid, text, message_thread_id=thread)
            except: return await bot.send_message(cid, text)
        return None

def _escape_md(text):
    esc = r'_*~`>#+-=|{}.!'
    buf, i = [], 0
    while i < len(text):
        if text[i] in esc: buf.append('\\' + text[i]); i += 1
        else: buf.append(text[i]); i += 1
    return ''.join(buf)

# ========== ДАЙДЖЕСТ ==========
async def _send_digest(cid):
    msgs = daily_messages.get(cid, [])
    if len(msgs) < 5: return
    log = "\n".join(f"[{m['link']}] @{m['author']}: {m['text']}" for m in _filter(msgs, 30))
    res = await _gen_digest(log, cid)
    if not res: return
    full = f"{get_greeting()}\n\n{_format(res)}"
    for part in _split(full):
        await _send_safe(cid, part)
        await asyncio.sleep(1.5)
    daily_messages[cid] = []; reactions[cid] = []
    digest_sent_today.pop(cid, None)
    save_messages_to_disk()

async def _digest_checker():
    while True:
        await asyncio.sleep(60)
        now = msk_now(); s = load_settings()
        for cid in list(daily_messages.keys()):
            msgs = daily_messages.get(cid, [])
            if not msgs: continue
            if len(msgs) >= 1000: await _send_digest(cid); continue
            ts = msgs[0].get("timestamp")
            if ts and (now - datetime.fromisoformat(ts)).total_seconds() >= 86400:
                await _send_digest(cid); continue
            if now.hour == s["send_hour"] and now.minute == s["send_minute"] and digest_sent_today.get(cid) != now.date():
                await _send_digest(cid); digest_sent_today[cid] = now.date()

# ========== РЕЙДЫ ==========
async def _send_raid(cid):
    msgs = daily_messages.get(cid, [])
    if len(msgs) < 10: return
    log = "\n".join(f"[{m['link']}] @{m['author']}: {m['text']}" for m in _filter(msgs, 20))
    res = await _gen_raid(log, cid)
    if res: await _send_safe(cid, res, parse_mode=None)

async def _raid_scheduler():
    while True:
        s = load_settings()
        if not s.get("raid_enabled", True): await asyncio.sleep(600); continue
        await asyncio.sleep(random.randint(int(s["raid_min_hours"]*3600), int(s["raid_max_hours"]*3600)))
        chats = load_chats()
        if chats and len(daily_messages.get(chats[0], [])) >= 10: await _send_raid(chats[0])

# ========== ПОЛУЧЕНИЕ ID ПОЛЬЗОВАТЕЛЯ ==========
async def _get_user_info(msg: Message) -> str:
    """Извлекает информацию о пользователе из пересланного сообщения"""
    if msg.forward_origin:
        fo = msg.forward_origin
        if hasattr(fo, 'sender_user') and fo.sender_user:
            user = fo.sender_user
        elif hasattr(fo, 'chat') and fo.chat:
            return f"📢 Канал/чат: {fo.chat.title or 'Без названия'}\n🆔 ID: {fo.chat.id}"
        else:
            return "❌ Не удалось определить отправителя"
    elif msg.reply_to_message and msg.reply_to_message.from_user:
        user = msg.reply_to_message.from_user
    elif msg.from_user:
        user = msg.from_user
    else:
        return "❌ Не удалось определить пользователя"
    
    uid = str(user.id)
    names = load_names()
    meta = ""
    if uid in names:
        if names[uid].get("name"): meta += f"\n🏷️ Имя: {names[uid]['name']}"
        if names[uid].get("description"): meta += f"\n📝 Описание: {names[uid]['description']}"
        if names[uid].get("gender"): 
            g = names[uid]['gender']
            meta += f"\n⚧ Пол: {'♂ Муж' if g=='male' else '♀ Жен' if g=='female' else '⚧ Другое'}"
    
    username = f"@{user.username}" if user.username else "нет"
    fullname = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Без имени"
    
    return f"""🆔 ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:
👤 Имя: {fullname}
🔖 Юзернейм: {username}
🆔 ID: {user.id}
💬 Язык: {user.language_code or 'не указан'}
🤖 Бот: {'да' if user.is_bot else 'нет'}{meta}

💡 Копируй ID: `{user.id}`"""

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
    if msg.photo: text = f"{text}\n{await _describe_photo(msg.photo[-1].file_id)}" if text else await _describe_photo(msg.photo[-1].file_id)
    if not text: text = "[войс/стикер]"
    
    link = f"https://t.me/c/{str(cid).replace('-100','')}/{msg.message_id}"
    daily_messages.setdefault(cid, []).append({"link": link, "author": author, "text": text.strip(), "user_id": msg.from_user.id, "timestamp": msk_now().isoformat()})
    save_messages_to_disk()
    if len(daily_messages[cid]) >= 1000: await _send_digest(cid)

# ========== АДМИН-КОМАНДЫ ==========
async def _admin_cmd(msg):
    t = msg.text or ""
    cid = msg.chat.id
    
    # ========== НОВАЯ ФУНКЦИЯ: ПОЛУЧЕНИЕ ID ==========
    if t == "/id" or msg.forward_origin:
        info = await _get_user_info(msg)
        await _send_safe(ADMIN_ID, info, parse_mode=None)
        return
    
    if t.startswith("/add_chat"):
        p = t.split()
        if len(p) < 2: await _send_safe(ADMIN_ID, "❌ /add_chat -100XXXXXX", parse_mode=None); return
        try:
            c = int(p[1]); chats = load_chats()
            if c not in chats: chats.append(c); save_chats(chats); await _send_safe(ADMIN_ID, f"✅ Чат {c} добавлен!", parse_mode=None)
            else: await _send_safe(ADMIN_ID, "⚠️ Уже в списке.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Неверный ID.", parse_mode=None)
    elif t.startswith("/remove_chat"):
        p = t.split()
        if len(p) < 2: await _send_safe(ADMIN_ID, "❌ /remove_chat -100XXXXXX", parse_mode=None); return
        try:
            c = int(p[1]); chats = load_chats()
            if c in chats: chats.remove(c); save_chats(chats); await _send_safe(ADMIN_ID, f"✅ Чат {c} удалён.", parse_mode=None)
            else: await _send_safe(ADMIN_ID, "⚠️ Не найден.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Неверный ID.", parse_mode=None)
    elif t.startswith("/list_chats"):
        chats = load_chats()
        await _send_safe(ADMIN_ID, "📋 Чаты:\n" + "\n".join(f"  - {c}" for c in chats) if chats else "📋 Нет чатов.", parse_mode=None)
    elif t.startswith("/settime"):
        p = t.split()
        if len(p) < 2 or not re.match(r'^\d{1,2}:\d{2}$', p[1]): await _send_safe(ADMIN_ID, "❌ /settime ЧЧ:ММ", parse_mode=None); return
        h, m = map(int, p[1].split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59): await _send_safe(ADMIN_ID, "❌ 0-23, 0-59.", parse_mode=None); return
        s = load_settings(); s["send_hour"], s["send_minute"] = h, m; save_settings(s)
        await _send_safe(ADMIN_ID, f"✅ Сводка в {h:02d}:{m:02d} МСК", parse_mode=None)
    elif t.startswith("/mood"):
        p = t.split()
        if len(p) < 2: await _send_safe(ADMIN_ID, f"Текущий: {load_settings().get('mood','hard')}\nlight/medium/hard/ultra", parse_mode=None); return
        m = p[1].lower()
        if m in MOOD: s = load_settings(); s["mood"] = m; save_settings(s); await _send_safe(ADMIN_ID, f"✅ {m.upper()}", parse_mode=None)
        else: await _send_safe(ADMIN_ID, "❌ light/medium/hard/ultra", parse_mode=None)
    elif t.startswith("/raid_timer"):
        p = t.split()
        if len(p) < 3: await _send_safe(ADMIN_ID, "❌ /raid_timer МИН МАКС (часы)", parse_mode=None); return
        try:
            mn, mx = float(p[1]), float(p[2])
            if mn <= 0 or mx <= 0 or mn > mx: raise ValueError
            s = load_settings(); s["raid_min_hours"], s["raid_max_hours"] = mn, mx; save_settings(s)
            await _send_safe(ADMIN_ID, f"✅ Интервал рейдов: {mn}-{mx} ч.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Положительные, МИН ≤ МАКС.", parse_mode=None)
    elif t.startswith("/raid"):
        p = t.split()
        if len(p) > 1 and p[1] == "now":
            cid = int(p[2]) if len(p) > 2 else (load_chats() or [None])[0]
            if cid: await _send_raid(cid); await _send_safe(ADMIN_ID, f"🤬 Рейд в {cid}!", parse_mode=None)
            else: await _send_safe(ADMIN_ID, "❌ Нет чатов.", parse_mode=None)
        else:
            s = load_settings()
            await _send_safe(ADMIN_ID, f"Наезды: {'вкл' if s.get('raid_enabled',True) else 'выкл'}\n/raid on|off|now", parse_mode=None)
    elif t.startswith("/setname"):
        p = t.split(maxsplit=2)
        if len(p) < 3: await _send_safe(ADMIN_ID, '❌ /setname ID NAME\nПример: /setname 123456789 "Дима"', parse_mode=None); return
        try:
            uid, name = p[1], p[2].strip('"').strip("'")
            names = load_names()
            if uid not in names: names[uid] = {}
            names[uid]["name"] = name; save_names(names)
            await _send_safe(ADMIN_ID, f"✅ Имя для {uid}: «{name}»", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/setdesc"):
        p = t.split(maxsplit=2)
        if len(p) < 3: await _send_safe(ADMIN_ID, '❌ /setdesc ID DESC\nПример: /setdesc 123456789 "страдалец"', parse_mode=None); return
        try:
            uid, desc = p[1], p[2].strip('"').strip("'")
            names = load_names()
            if uid not in names: names[uid] = {}
            names[uid]["description"] = desc; save_names(names)
            await _send_safe(ADMIN_ID, f"✅ Описание для {uid}: «{desc}»", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/setgender"):
        p = t.split()
        if len(p) < 3: await _send_safe(ADMIN_ID, "❌ /setgender ID male|female|other", parse_mode=None); return
        uid, gender = p[1], p[2].lower()
        if gender not in ("male", "female", "other"): await _send_safe(ADMIN_ID, "❌ gender: male|female|other", parse_mode=None); return
        names = load_names()
        if uid not in names: names[uid] = {}
        names[uid]["gender"] = gender; save_names(names)
        g = "♂ Муж" if gender=="male" else "♀ Жен" if gender=="female" else "⚧ Другое"
        await _send_safe(ADMIN_ID, f"✅ Пол для {uid}: {g}", parse_mode=None)
    elif t.startswith("/removename"):
        p = t.split()
        if len(p) < 2: await _send_safe(ADMIN_ID, "❌ /removename ID", parse_mode=None); return
        try:
            uid = p[1]; names = load_names()
            if uid in names and "name" in names[uid]:
                del names[uid]["name"]
                if not names[uid]: del names[uid]
                save_names(names); await _send_safe(ADMIN_ID, f"✅ Имя для {uid} удалено.", parse_mode=None)
            else: await _send_safe(ADMIN_ID, f"⚠️ Нет имени для {uid}.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/removedesc"):
        p = t.split()
        if len(p) < 2: await _send_safe(ADMIN_ID, "❌ /removedesc ID", parse_mode=None); return
        try:
            uid = p[1]; names = load_names()
            if uid in names and "description" in names[uid]:
                del names[uid]["description"]
                if not names[uid]: del names[uid]
                save_names(names); await _send_safe(ADMIN_ID, f"✅ Описание для {uid} удалено.", parse_mode=None)
            else: await _send_safe(ADMIN_ID, f"⚠️ Нет описания для {uid}.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/removegender"):
        p = t.split()
        if len(p) < 2: await _send_safe(ADMIN_ID, "❌ /removegender ID", parse_mode=None); return
        try:
            uid = p[1]; names = load_names()
            if uid in names and "gender" in names[uid]:
                del names[uid]["gender"]
                if not names[uid]: del names[uid]
                save_names(names); await _send_safe(ADMIN_ID, f"✅ Пол для {uid} удалён.", parse_mode=None)
            else: await _send_safe(ADMIN_ID, f"⚠️ Нет пола для {uid}.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/bulk_names"):
        p = t.split(maxsplit=1)
        if len(p) < 2: await _send_safe(ADMIN_ID, "❌ /bulk_names JSON", parse_mode=None); return
        try:
            data = json.loads(p[1])
            result = await _bulk_update("name", {k: v.get("name") or v for k, v in data.items()})
            await _send_safe(ADMIN_ID, result, parse_mode=None)
        except json.JSONDecodeError: await _send_safe(ADMIN_ID, "❌ Неверный JSON.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/bulk_desc"):
        p = t.split(maxsplit=1)
        if len(p) < 2: await _send_safe(ADMIN_ID, "❌ /bulk_desc JSON", parse_mode=None); return
        try:
            data = json.loads(p[1])
            result = await _bulk_update("description", {k: v.get("desc") or v.get("description") for k, v in data.items()})
            await _send_safe(ADMIN_ID, result, parse_mode=None)
        except json.JSONDecodeError: await _send_safe(ADMIN_ID, "❌ Неверный JSON.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/bulk_gender"):
        p = t.split(maxsplit=1)
        if len(p) < 2: await _send_safe(ADMIN_ID, "❌ /bulk_gender JSON", parse_mode=None); return
        try:
            data = json.loads(p[1])
            result = await _bulk_update("gender", {k: v.get("gender") for k, v in data.items()})
            await _send_safe(ADMIN_ID, result, parse_mode=None)
        except json.JSONDecodeError: await _send_safe(ADMIN_ID, "❌ Неверный JSON.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/list_users"):
        p = t.split()
        uid = p[1] if len(p) > 1 else None
        result = await _list_users(uid)
        await _send_safe(ADMIN_ID, result, parse_mode=None)
    elif t.startswith("/test"):
        p = t.split(); cid = int(p[1]) if len(p) > 1 else (load_chats() or [None])[0]; cnt = int(p[2]) if len(p) > 2 else 10
        if not cid: await _send_safe(ADMIN_ID, "❌ Нет чатов.", parse_mode=None); return
        msgs = daily_messages.get(cid, [])
        if len(msgs) < 5: await _send_safe(ADMIN_ID, f"❌ Всего {len(msgs)} сообщ. (нужно ≥5).", parse_mode=None); return
        log = "\n".join(f"[{m['link']}] @{m['author']}: {m['text']}" for m in _filter(msgs[-min(cnt,len(msgs)):], 30))
        await _send_safe(ADMIN_ID, "🧪 Генерирую...", parse_mode=None)
        res = await _gen_digest(log, cid)
        if not res: return
        full = f"{get_greeting()}\n\n{_format(res)}"
        for part in _split(full): await _send_safe(ADMIN_ID, part)
    elif t.startswith("/status"):
        s = load_settings(); lines = ["📊 Статистика:"]
        for cid, msgs in daily_messages.items(): lines.append(f"  Чат {cid}: {len(msgs)} сообщ.")
        if not daily_messages: lines.append("  Пусто.")
        lines += [f"\nВремя: {s['send_hour']:02d}:{s['send_minute']:02d} МСК", f"Мат: {s.get('mood','hard').upper()}", f"Наезды: {'вкл' if s.get('raid_enabled',True) else 'выкл'}"]
        await _send_safe(ADMIN_ID, "\n".join(lines), parse_mode=None)
    elif t.startswith("/reset"):
        p = t.split(); cid = int(p[1]) if len(p) > 1 else None
        if cid: daily_messages[cid] = []; reactions[cid] = []
        else: daily_messages.clear(); reactions.clear()
        save_messages_to_disk(); await _send_safe(ADMIN_ID, "🗑️ Сброшено.", parse_mode=None)
    elif t.startswith("/backup"):
        chats, names, s = load_chats(), load_names(), load_settings()
        cmds = []
        for c in chats: cmds.append(f"/add_chat {c}")
        for uid, data in names.items():
            if "name" in data: cmds.append(f"/setname {uid} \"{data['name']}\"")
            if "description" in data: cmds.append(f"/setdesc {uid} \"{data['description']}\"")
            if "gender" in data: cmds.append(f"/setgender {uid} {data['gender']}")
        cmds.append(f"/settime {s['send_hour']:02d}:{s['send_minute']:02d}")
        cmds.append(f"/mood {s.get('mood', 'hard')}")
        await _send_safe(ADMIN_ID, "🛠 Команды восстановления:\n" + "\n".join(cmds), parse_mode=None)
    elif t.startswith("/help"):
        s = load_settings()
        await _send_safe(ADMIN_ID, f"""🛠 ЗЯБЛОГРАФ — ПОЛНАЯ СПРАВКА

📰 ДАЙДЖЕСТЫ
• ⏰ /settime ЧЧ:ММ — время сводки. Сейчас: {s['send_hour']:02d}:{s['send_minute']:02d}
• 🔄 Сработает: 1000 сообщений ИЛИ 24ч ИЛИ /settime

🤬 РЕЙДЫ
• 🤬 /raid on|off — вкл/выкл авто-наезды
• 🎯 /raid now [чат] — вызвать рейд сейчас
• 🕒 /raid_timer МИН МАКС — интервал (часы). Сейчас: {s.get('raid_min_hours',2)}–{s.get('raid_max_hours',12)} ч.

🏷️ ПОЛЬЗОВАТЕЛИ: ИМЕНА, ОПИСАНИЯ, ПОЛ
• 🏷️ /setname ID "Имя" — задать имя
• 📝 /setdesc ID "Описание" — добавить описание
• ⚧ /setgender ID male|female|other — задать пол
• ❌ /removename|removedesc|removegender ID — убрать
• 📦 /bulk_names|bulk_desc|bulk_gender JSON — массово
  Пример: /bulk_names {{"123":{{"name":"Дима"}}}}
• 📋 /list_users [ID] — показать всех или одного

🆔 ПОЛУЧЕНИЕ ID ПОЛЬЗОВАТЕЛЯ
• 🆔 /id — ответь на сообщение или перешли его боту
• Или просто перешли любое сообщение в ЛС боту
• Бот покажет: имя, юзернейм, ID, пол, описание

⚙️ ПРОЧЕЕ
• 🔥 /mood light|medium|hard|ultra — степень мата
• 📋 /add_chat|remove_chat|list_chats — управление чатами
• 🧪 /test [чат] [кол-во] — тестовая сводка
• /status | /reset | /backup

💡 Все команды — только в ЛС боту.""", parse_mode=None)

async def _bulk_update(field: str, data: dict) -> str:
    names = load_names(); count = 0
    for uid, val in data.items():
        if uid not in names: names[uid] = {}
        names[uid][field] = val; count += 1
    save_names(names)
    return f"✅ Обновлено {count} записей ({field})."

async def _list_users(uid: str = None) -> str:
    names = load_names()
    if uid:
        if uid not in names: return f"⚠️ Нет данных для {uid}."
        data = names[uid]
        g = {"male":"♂ Муж","female":"♀ Жен","other":"⚧ Другое"}.get(data.get("gender"), "—")
        return f"📋 {uid}:\n  • Имя: {data.get('name', '—')}\n  • Описание: {data.get('description', '—')}\n  • Пол: {g}"
    if not names: return "📋 Нет кастомных данных."
    lines = ["📋 Все пользователи:"]
    for uid, data in sorted(names.items()):
        name = data.get("name", "—"); desc = data.get("description", "—")
        gender = {"male":"♂","female":"♀","other":"⚧"}.get(data.get("gender"), "—")
        lines.append(f"  • {uid}: «{name}» {gender} — {desc}")
    return "\n".join(lines)

# ========== ЗАПУСК ==========
async def main():
    logger.info("Зяблограф запущен! Используем ПЛАТНЫЕ модели OpenRouter (лимитов нет).")
    await bot.initialize()
    load_messages_from_disk()
    for cid in load_chats(): daily_messages.setdefault(cid, []); reactions.setdefault(cid, [])
    asyncio.create_task(_digest_checker())
    asyncio.create_task(_raid_scheduler())
    
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
            logger.error(f"Ошибка: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
