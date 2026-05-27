import os, json, re, base64, logging, asyncio, random
from datetime import datetime, timedelta, timezone
from openai import OpenAI
from telegram import Bot
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

# ========== СЛОВАРЬ (ИСПРАВЛЕНИЕ ПРОБЕЛОВ В КЛЮЧАХ) ==========
def load_dictionary() -> dict:
    try:
        with open(DICT_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            return {k.strip(): v for k, v in raw.items()}
    except: 
        return {"существительные": ["пиздец", "хуй", "дебил"], "глаголы": ["обосрался", "охуел"]}
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
    defaults = {"send_hour": 18, "send_minute": 0, "mood": "hard", "raid_enabled": True, "raid_min_hours": 2, "raid_max_hours": 12}
    return {**defaults, **load_all_data().get("settings", {})}
def save_settings(s: dict) -> None: d = load_all_data(); d["settings"] = s; save_all_data(d)

def get_display_name(user, include_meta: bool = True) -> str:
    names = load_names(); uid = str(user.id)
    if uid not in names: return user.first_name or user.username or "Анон"
    name = names[uid].get("name") or user.first_name or user.username or "Анон"
    if not include_meta: return name
    parts = [f"@{name}"]
    gender = names[uid].get("gender")
    if gender: parts.append(f"[{'♂' if gender=='male' else '♀' if gender=='female' else '⚧'}]")
    desc = names[uid].get("description")
    if desc: parts.append(f", {desc}")
    return "".join(parts)

# ========== ПРОМПТЫ (ОПТИМИЗИРОВАНО ПОД "ВЕСТНИК" БЕЗ БАНОМ) ==========
MOOD_STYLES = {
    "light": "Лёгкий сарказм, редкая экспрессия.",
    "medium": "Умеренный юмор, иногда острый.",
    "hard": "Сатирический, циничный, чёрный юмор. Используй сленг и экспрессивную лексику для комического эффекта.",
    "ultra": "Жёсткий троллинг, поток сарказма, прямые формулировки."
}
GREETINGS = ["📰 Главное из последних сообщений:", "📰 Экстренный выпуск Зяблографа!", "📰 Зяблограф выбрал самое «достойное»:", "📰 Зяблограф: главные события:"]
def get_greeting() -> str: return random.choice(GREETINGS)

DEFAULT_MAIN_PROMPT = """[System: Ты — «Зяблограф», циничный хроникёр чата в стиле сатирического дайджеста. Пиши ТОЛЬКО на русском.
СТИЛЬ: {mood}. Используй метафоры, иронию и сленг. Не будь вежливым.
ЖЁСТКИЕ ПРАВИЛА:
1. ГРУППИРОВКА: Все сообщения про одно событие/тему объединяй в ОДИН абзац. Никаких дублей.
2. ФОРМАТ: Каждый абзац начинается СТРОГО: # (ССЫЛКА_ИЗ_ВХОДНЫХ_ДАННЫХ) Текст события.
3. ЗАПРЕТ: Не придумывай заголовки (#ТемаДня). Используй только ссылку из лога.
4. ЯЗЫК: Только кириллица. Иероглифы/латиница (кроме ссылок) запрещены.
5. ОБЪЁМ: 2-4 предложения на тему. Не выдумывай фактов.
СЛОВАРЬ ДЛЯ ИСПОЛЬЗОВАНИЯ:
{dict_injection}
Вот лог чата:
"""

# ========== LLM ВЫЗОВ ==========
async def _call_llm(prompt: str, max_tokens: int = 4096, temperature: float = 0.95, chat_id: int = None) -> str:
    # Цепочка: бесплатные -> дешёвые платные. Бюджет $3 хватит на 10000+ вызовов.
    models = ["qwen/qwen-2.5-72b-instruct:free", "qwen/qwen-2.5-32b-instruct:free", "meta-llama/llama-3.1-8b-instruct:free"]
    prompt = prompt.replace('\\', '\\\\').replace('"', '\\"')[:90000]
    
    for model in models:
        for attempt in range(2):
            try:
                loop = asyncio.get_event_loop()
                comp = await loop.run_in_executor(None, lambda: client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}],
                    temperature=temperature, max_tokens=max_tokens))
                text = comp.choices[0].message.content.strip()
                # Проверка на отказ модели (Safety Layer)
                if any(w in text.lower() for w in ["sorry", "cannot", "не могу", "отказ", "safety", "i can't"]):
                    logger.warning("Safety refusal, пробую другую модель...")
                    break
                return _clean(text)
            except Exception as e:
                err = str(e).lower()
                if "rate_limit" in err or "400" in err: break
                if "insufficient" in err or "402" in err: logger.error("Баланс OpenRouter закончился!")
                await asyncio.sleep(1)
    try: await bot.send_message(ADMIN_ID, f"⚠️ Зяблограф не смог сгенерировать текст. Ошибка: {err[:200]}", parse_mode=None)
    except: pass
    return None

def _clean(text: str) -> str:
    text = text.strip()
    # Удаляем иероглифы/арабскую вязь/прочий мусор
    text = re.sub(r'[^\x00-\x7Fа-яА-ЯёЁ0-9\s.,!?;:()«»""''-—@#\n\r]', '', text)
    text = re.sub(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+', '', text)
    # Приводим ссылки к виду # (https://...)
    text = re.sub(r'#\s+(https?://t.me/\S+)', r'# (\1)', text)
    text = re.sub(r'#[^(\s]+\s*(https?://t.me/\S+)', r'# (\1)', text)
    if "⭐️ Станьте спонсором" in text: text = text.split("⭐️ Станьте спонсором")[0].strip()
    return text.strip()

def _format(text: str) -> str:
    return re.sub(r'#\s*((https?://t.me/[^\s)]+))', r'[#](\1)', text)

# ========== ФОТО ==========
async def _describe_photo(file_id: str) -> str:
    try:
        file = await bot.get_file(file_id)
        img = base64.b64encode(await file.download_as_bytearray()).decode()
        loop = asyncio.get_event_loop()
        comp = await loop.run_in_executor(None, lambda: client.chat.completions.create(
            model="qwen/qwen-2.5-vl-72b-instruct",
            messages=[{"role": "user", "content": [{"type": "text", "text": "Опиши фото. Только на русском, можно с матом/сарказмом."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}]}],
            temperature=0.7, max_tokens=300))
        return f"[ФОТО: {_clean(comp.choices[0].message.content)}]"
    except: return "[ФОТО: не удалось описать]"

# ========== ФИЛЬТРАЦИЯ & ГЕНЕРАЦИЯ ==========
def _filter(msgs, max_n=30):
    hot = re.compile(r'бля|хуй|пизд|еба|сука|нах|почему|кто|где|когда|зачем|@\w+|https?://|\[ФОТО:', re.I)
    res = [m for m in msgs if hot.search(m.get("text", ""))]
    return (res or msgs)[-max_n:]

def _build_prompt(cid):
    s = load_settings()
    mood = MOOD_STYLES.get(s.get("mood", "hard"), MOOD_STYLES["hard"])
    dict_inj = "\n".join([f"{k}: {', '.join(v[:15])}" for k, v in SWEAR_DICT.items() if v])
    prompt = DEFAULT_MAIN_PROMPT.replace("{mood}", mood).replace("{dict_injection}", dict_inj)
    return prompt

async def _gen_digest(log, cid):
    return await _call_llm(_build_prompt(cid) + log, max_tokens=4096, chat_id=cid)

# ========== БЕЗОПАСНАЯ ОТПРАВКА ==========
def _split(text, max_len=4000):
    if len(text) <= max_len: return [text]
    lines, parts, cur = text.split("\n"), [], ""
    for line in lines:
        if len(cur) + len(line) + 2 <= max_len: cur = (cur + "\n" + line).strip()
        else:
            if cur: parts.append(cur)
            cur = line
    if cur: parts.append(cur)
    return parts

def _escape_md(text):
    esc = r'_*~`>#+-=|{}.!'
    buf, i = [], 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text) and text[i+1] in esc: buf.append(text[i:i+2]); i += 2
        elif text[i] in esc: buf.append('\\' + text[i]); i += 1
        else: buf.append(text[i]); i += 1
    return ''.join(buf)

async def _send_safe(cid, text, parse_mode="MarkdownV2", thread=1):
    try:
        if parse_mode == "MarkdownV2": text = _escape_md(text)
        return await bot.send_message(cid, text, parse_mode=parse_mode, message_thread_id=thread)
    except TelegramError as e:
        if "thread" in str(e).lower(): return await _send_safe(cid, text, parse_mode, thread=None)
        if parse_mode: return await _send_safe(cid, text, None, thread)
        return None

# ========== ДАЙДЖЕСТ & ПРОВЕРКА ==========
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

async def _check_and_send_if_needed(cid):
    msgs = daily_messages.get(cid, [])
    if not msgs: return False
    if len(msgs) >= 1000: await _send_digest(cid); return True
    ts = msgs[0].get("timestamp")
    if ts and (msk_now() - datetime.fromisoformat(ts)).total_seconds() >= 86400: await _send_digest(cid); return True
    return False

async def _digest_periodic_checker():
    while True:
        await asyncio.sleep(60)
        now, s = msk_now(), load_settings()
        for cid in list(daily_messages.keys()):
            msgs = daily_messages.get(cid, [])
            if not msgs: continue
            if len(msgs) >= 1000: await _send_digest(cid); continue
            if msgs[0].get("timestamp") and (now - datetime.fromisoformat(msgs[0]["timestamp"])).total_seconds() >= 86400: await _send_digest(cid); continue
            if now.hour == s["send_hour"] and now.minute == s["send_minute"] and digest_sent_today.get(cid) != now.date():
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
    if msg.photo: text = f"{text}\n{await _describe_photo(msg.photo[-1].file_id)}" if text else await _describe_photo(msg.photo[-1].file_id)
    if not text: text = "[войс/стикер]"

    link = f"https://t.me/c/{str(cid).replace('-100','')}/{msg.message_id}"
    daily_messages.setdefault(cid, []).append({"link": link, "author": author, "text": text.strip(), "user_id": msg.from_user.id, "timestamp": msk_now().isoformat()})
    save_messages_to_disk()
    await _check_and_send_if_needed(cid)

# ========== АДМИН-КОМАНДЫ ==========
async def _get_user_info(msg) -> str:
    user = None
    if msg.forward_origin:
        fo = msg.forward_origin
        if hasattr(fo, 'sender_user') and fo.sender_user: user = fo.sender_user
        elif hasattr(fo, 'chat') and fo.chat: return f"📢 Канал: {fo.chat.title or 'Без имени'}\n🆔 ID: `{fo.chat.id}`"
    elif msg.reply_to_message and msg.reply_to_message.from_user: user = msg.reply_to_message.from_user
    elif msg.from_user: user = msg.from_user
    if not user: return "❌ Не удалось определить пользователя"
    uid, names = str(user.id), load_names()
    meta = ""
    if uid in names:
        if names[uid].get("name"): meta += f"\n🏷️ Имя: {names[uid]['name']}"
        if names[uid].get("description"): meta += f"\n📝 Описание: {names[uid]['description']}"
        if names[uid].get("gender"): meta += f"\n⚧ Пол: {'♂ Муж' if names[uid]['gender']=='male' else '♀ Жен' if names[uid]['gender']=='female' else '⚧ Другое'}"
    return f"""🆔 ИНФОРМАЦИЯ:
👤 Имя: {user.first_name or ''} {user.last_name or ''}
🔖 Юзернейм: @{user.username or 'нет'}
🆔 ID: `{user.id}`{meta}"""

async def _admin_cmd(msg):
    t = msg.text or ""
    if t == "/id" or msg.forward_origin: await _send_safe(ADMIN_ID, await _get_user_info(msg), parse_mode=None); return
        
    if t.startswith("/add_chat"):
        p = t.split()
        if len(p)<2: await _send_safe(ADMIN_ID, "❌ /add_chat -100XXXXXX", parse_mode=None); return
        try:
            c = int(p[1]); chats = load_chats()
            if c not in chats: chats.append(c); save_chats(chats); await _send_safe(ADMIN_ID, f"✅ Чат {c} добавлен!", parse_mode=None)
            else: await _send_safe(ADMIN_ID, "⚠️ Уже в списке.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Неверный ID.", parse_mode=None)
    elif t.startswith("/remove_chat"):
        p = t.split()
        if len(p)<2: await _send_safe(ADMIN_ID, "❌ /remove_chat -100XXXXXX", parse_mode=None); return
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
        if len(p)<2 or not re.match(r'^\d{1,2}:\d{2}$', p[1]): await _send_safe(ADMIN_ID, "❌ /settime ЧЧ:ММ", parse_mode=None); return
        h,m = map(int, p[1].split(":"))
        if not(0<=h<=23 and 0<=m<=59): await _send_safe(ADMIN_ID, "❌ 0-23, 0-59.", parse_mode=None); return
        s = load_settings(); s["send_hour"], s["send_minute"] = h, m; save_settings(s)
        await _send_safe(ADMIN_ID, f"✅ Сводка в {h:02d}:{m:02d} МСК", parse_mode=None)
    elif t.startswith("/mood"):
        p = t.split()
        if len(p)<2: await _send_safe(ADMIN_ID, f"Текущий: {load_settings().get('mood','hard')}\nlight/medium/hard/ultra", parse_mode=None); return
        m = p[1].lower()
        if m in MOOD_STYLES: s = load_settings(); s["mood"] = m; save_settings(s); await _send_safe(ADMIN_ID, f"✅ {m.upper()}", parse_mode=None)
        else: await _send_safe(ADMIN_ID, "❌ light/medium/hard/ultra", parse_mode=None)
    elif t.startswith("/raid_timer"):
        p = t.split()
        if len(p)<3: await _send_safe(ADMIN_ID, "❌ /raid_timer МИН МАКС (часы)", parse_mode=None); return
        try:
            mn,mx = float(p[1]),float(p[2])
            if mn<=0 or mx<=0 or mn>mx: raise ValueError
            s = load_settings(); s["raid_min_hours"], s["raid_max_hours"] = mn, mx; save_settings(s)
            await _send_safe(ADMIN_ID, f"✅ Интервал рейдов: {mn}-{mx} ч.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Положительные, МИН ≤ МАКС.", parse_mode=None)
    elif t.startswith("/raid"):
        p = t.split()
        if len(p)>1 and p[1]=="now":
            cid = int(p[2]) if len(p)>2 else (load_chats() or [None])[0]
            if cid: await _send_safe(cid, "🤬 РЕЙД В ЭТОМ ЧАТЕ!", parse_mode=None) # Заглушка
            else: await _send_safe(ADMIN_ID, "❌ Нет чатов.", parse_mode=None)
        else:
            s = load_settings()
            await _send_safe(ADMIN_ID, f"Наезды: {'вкл' if s.get('raid_enabled',True) else 'выкл'}\n/raid on|off|now", parse_mode=None)
    elif t.startswith("/setname|setdesc|setgender".split("|")):
        cmd = "/setname" if t.startswith("/setname") else "/setdesc" if t.startswith("/setdesc") else "/setgender"
        p = t.split(maxsplit=2)
        if len(p)<3: await _send_safe(ADMIN_ID, f"❌ {cmd} ID ЗНАЧЕНИЕ", parse_mode=None); return
        try:
            uid, val = p[1], p[2].strip('"').strip("'")
            key = "name" if "name" in cmd else "description" if "desc" in cmd else "gender"
            names = load_names()
            if uid not in names: names[uid] = {}
            names[uid][key] = val.lower() if key=="gender" else val
            save_names(names)
            await _send_safe(ADMIN_ID, f"✅ {key} для {uid}: «{val}»", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/removename|removedesc|removegender".split("|")):
        cmd = "/removename" if t.startswith("/removename") else "/removedesc" if t.startswith("/removedesc") else "/removegender"
        p = t.split(); key = "name" if "name" in cmd else "description" if "desc" in cmd else "gender"
        if len(p)<2: await _send_safe(ADMIN_ID, f"❌ {cmd} ID", parse_mode=None); return
        try:
            uid = p[1]; names = load_names()
            if uid in names and key in names[uid]: del names[uid][key]; save_names(names); await _send_safe(ADMIN_ID, f"✅ {key} для {uid} удалено.", parse_mode=None)
            else: await _send_safe(ADMIN_ID, f"⚠️ Нет данных для {uid}.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка.", parse_mode=None)
    elif t.startswith("/bulk_names|bulk_desc|bulk_gender".split("|")):
        cmd = "/bulk_names" if t.startswith("/bulk_names") else "/bulk_desc" if t.startswith("/bulk_desc") else "/bulk_gender"
        p = t.split(maxsplit=1)
        if len(p)<2: await _send_safe(ADMIN_ID, f"❌ {cmd} JSON", parse_mode=None); return
        try:
            data = json.loads(p[1]); names = load_names(); count = 0
            key = "name" if "names" in cmd else "description" if "desc" in cmd else "gender"
            for uid, val in data.items():
                if uid not in names: names[uid] = {}
                names[uid][key] = val.get(key) or val if isinstance(val, dict) else val
                count += 1
            save_names(names); await _send_safe(ADMIN_ID, f"✅ Обновлено {count} записей.", parse_mode=None)
        except: await _send_safe(ADMIN_ID, "❌ Ошибка JSON.", parse_mode=None)
    elif t.startswith("/list_users"):
        p = t.split(); uid = p[1] if len(p)>1 else None; names = load_names()
        if uid:
            if uid not in names: await _send_safe(ADMIN_ID, f"⚠️ Нет данных для {uid}.", parse_mode=None); return
            d = names[uid]; g = {"male":"♂ Муж","female":"♀ Жен","other":"⚧ Другое"}.get(d.get("gender"), "—")
            await _send_safe(ADMIN_ID, f"📋 {uid}:\n  • Имя: {d.get('name', '—')}\n  • Описание: {d.get('description', '—')}\n  • Пол: {g}", parse_mode=None)
        elif not names: await _send_safe(ADMIN_ID, "📋 Нет кастомных данных.", parse_mode=None)
        else:
            lines = ["📋 Все пользователи:"]
            for u, d in sorted(names.items()): lines.append(f"  • {u}: «{d.get('name','—')}» {d.get('gender','—')} — {d.get('description','—')}")
            await _send_safe(ADMIN_ID, "\n".join(lines), parse_mode=None)
    elif t.startswith("/test"):
        p = t.split(); cid=int(p[1]) if len(p)>1 else (load_chats() or [None])[0]; cnt=int(p[2]) if len(p)>2 else 10
        if not cid: await _send_safe(ADMIN_ID, "❌ Нет чатов.", parse_mode=None); return
        msgs = daily_messages.get(cid, [])
        if len(msgs)<5: await _send_safe(ADMIN_ID, f"❌ Всего {len(msgs)} сообщ. (нужно ≥5).", parse_mode=None); return
        log = "\n".join(f"[{m['link']}] @{m['author']}: {m['text']}" for m in _filter(msgs[-min(cnt,len(msgs)):], 30))
        await _send_safe(ADMIN_ID, "🧪 Генерирую...", parse_mode=None)
        res = await _gen_digest(log, cid)
        if not res: return
        full = f"{get_greeting()}\n\n{_format(res)}"
        for part in _split(full): await _send_safe(ADMIN_ID, part)
    elif t.startswith("/status"):
        s = load_settings(); lines = ["📊 Статистика:"]
        for cid,msgs in daily_messages.items(): lines.append(f"  Чат {cid}: {len(msgs)} сообщ.")
        if not daily_messages: lines.append("  Пусто.")
        lines += [f"\nВремя: {s['send_hour']:02d}:{s['send_minute']:02d} МСК", f"Мат: {s.get('mood','hard').upper()}", f"Наезды: {'вкл' if s.get('raid_enabled',True) else 'выкл'}"]
        await _send_safe(ADMIN_ID, "\n".join(lines), parse_mode=None)
    elif t.startswith("/reset"):
        p=t.split(); cid=int(p[1]) if len(p)>1 else None
        if cid: daily_messages[cid]=[]; reactions[cid]=[]
        else: daily_messages.clear(); reactions.clear()
        save_messages_to_disk(); await _send_safe(ADMIN_ID, "🗑️ Сброшено.", parse_mode=None)
    elif t.startswith("/help"):
        s = load_settings()
        await _send_safe(ADMIN_ID, f"""🛠 ЗЯБЛОГРАФ — ПОЛНАЯ СПРАВКА

📰 ДАЙДЖЕСТЫ
• ⏰ /settime ЧЧ:ММ — время сводки. Сейчас: {s['send_hour']:02d}:{s['send_minute']:02d}
• 🔄 Сработает: 1000 сообщений ИЛИ 24ч ИЛИ /settime

🤬 РЕЙДЫ
• 🤬 /raid on|off — вкл/выкл авто-наезды
• 🕒 /raid_timer МИН МАКС — интервал (часы). Сейчас: {s.get('raid_min_hours',2)}–{s.get('raid_max_hours',12)} ч.

🏷️ ПОЛЬЗОВАТЕЛИ
• 🏷️ /setname ID "Имя" | 📝 /setdesc ID "Описание" | ⚧ /setgender ID male|female|other
• 📦 /bulk_names|bulk_desc|bulk_gender JSON — массово
• ❌ /removename|removedesc|removegender ID — убрать
• 📋 /list_users [ID] — показать всех или одного

🆔 УЗНАТЬ ID
• 🆔 /id — ответь на сообщение или перешли его боту

⚙️ ПРОЧЕЕ
• 🔥 /mood light|medium|hard|ultra
• 📋 /add_chat|remove_chat|list_chats
• 🧪 /test [чат] [кол-во]
• /status | /reset""", parse_mode=None)

# ========== ЗАПУСК ==========
async def main() -> None:
    logger.info("Зяблограф запущен! OpenRouter (:free), бюджет оптимизирован.")
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
            logger.error(f"Ошибка: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
