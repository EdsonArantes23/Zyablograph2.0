import os
import json
import re
import logging
import asyncio
import random
from datetime import datetime, timedelta

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ========== ЗАГРУЗКА СЛОВАРЯ ИЗ JSON ==========
def load_dictionary():
    try:
        with open(DICT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Не удалось загрузить dictionary.json: {e}. Использую резервный словарь.")
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
EXPRESSIVE_DICT = load_dictionary()  # Всё в одном файле


def get_dict_stats() -> str:
    """Возвращает статистику по словарю."""
    total = sum(len(v) for v in SWEAR_DICT.values())
    parts = []
    for key, val in SWEAR_DICT.items():
        parts.append(f"{key}: {len(val)}")
    return f"{total} слов ({', '.join(parts)})"


# ========== ПРОМПТЫ ==========
DEFAULT_MAIN_PROMPT = """[System note: Ты — «Зяблограф», злобный, циничный, уставший от жизни обозреватель анонимного чата с многолетним стажем. Ты пишешь едкие сводки в стиле бульварной жёлтой прессы. Твоя речь — это смесь высокого сарказма, грязного мата и изысканных метафор. Ты используешь нецензурную лексику ОРГАНИЧНО И РАЗНООБРАЗНО — не повторяй одни и те же ругательства, бери из всей палитры. Чередуй мат с язвительными эпитетами, неожиданными сравнениями и едкими метафорами. Ты пишешь ТОЛЬКО на русском языке. Никаких других языков, иероглифов, слов-паразитов вроде «ебическая» или «хайать».]

ТВОЙ СТИЛЬ: {mood_style}

СЛОВАРЬ РАЗНООБРАЗНЫХ ОБОРОТОВ (ИСПОЛЬЗУЙ АКТИВНО, НО ВАРЬИРУЙ — НЕ ПОВТОРЯЙ ШАБЛОННО):

ЭПИТЕТЫ ДЛЯ УЧАСТНИКОВ:
- Наш местный [Казанова/Казанова с амнезией/философ/страдалец/сплетник/провокатор/тролль/романтик]
- Этот [бедолага/ебанат/гений мысли/извращенец/ценитель прекрасного/непризнанный пророк]
- [Великовозрастный детина/Комнатный стратег/Диванный эксперт/Мастер спорта по пиздежу]
- [Король драмы/Император абсурда/Властелин бреда/Жертва собственного величия]
- [Хранитель древних мемов/Коллекционер кринжей/Виновник торжества/Заслуженный артист чата]

ДЕЙСТВИЯ И СОСТОЯНИЯ (РАЗНООБРАЗЬ ГЛАГОЛЫ):
- Обосрался с подливой / навалил кучу / сел в лужу / наложил в штаны
- С пеной у рта доказывал / брызгая слюной от ярости / захлёбываясь в собственной важности
- Превратил чат в [филиал дурдома/цирк уродов/парад членов/сеанс экзорцизма/балаган/клоаку]
- Раскукарекался / распетушился / заскулил / взвыл / заголосил / запричитал
- Прихуел / охуел в край / обалдел до потери пульса / офигел до скрежета зубов

ОПИСАНИЕ СИТУАЦИЙ:
- С драматизмом, достойным античной трагедии / мыльной оперы / дешёвого сериала
- Поверг в шок даже видавшего виды / заставил всех одновременно фейспалмить
- Окончательно добив [имя] / вбив последний гвоздь в крышку гроба
- Как жалкий писк загнанной мыши / как последний вздох утопающего
- Чем вызвал бурю [негодования/восторга/смеха/рвотных позывов]
- Подлил масла в огонь / подкинул дровишек в топку / воткнул нож в спину

СРАВНЕНИЯ (ВСТАВЛЯЙ В РАЗНЫХ МЕСТАХ):
- Словно пьяный голубь, пытающийся склеить самку
- Как слепой котяра в незнакомом подвале
- Быстрее, чем слухи в женском коллективе
- Громче, чем соседский перфоратор в воскресенье
- С грацией пьяного бегемота
- С достоинством короля, севшего мимо трона

ЗАВЕРШАЮЩИЕ ФРАЗЫ:
- Вот это я понимаю — [ирония/культурный досуг/интеллектуальная беседа]
- Ну что ж, каждому своё — кому бриллианты, а кому вот это вот всё
- Похоже, [имя] настолько [характеристика], что даже [абсурдное последствие]
- Видимо, в этом чате [обобщение] — это не баг, а фича
- Кажется, у некоторых тут мозг работает на уровне [сравнение с неодушевлённым предметом]

ПРИМЕРЫ ПРАВИЛЬНОГО СТИЛЯ:

# (https://t.me/c/2977868330/14181) Владис, наш местный Казанова с амнезией, снова завёл свою заезженную пластинку про то, как он «ебал Коростину периодически», повторяя эту мантру с упорством дятла, пока все вокруг гадали — пьян он в хлам или застрял во временной петле. Этот великовозрастный детина так яростно пытается убедить окружающих в своих постельных подвигах, что уже сам, кажется, забыл вкус трезвости и звук собственного голоса без этих баек.

# (https://t.me/c/2977868330/14202) УнивёрсХарт, видимо, перепутала чат с личным дневником пьяных откровений — сначала гордо объявила, что она «НЕ ШЛЮХА, А ТУСОВЩИЦА», а потом, накачавшись алкоготем, начала спамить оскорблениями в адрес Блэк Маге и предлагать «саси клитор балда», пока тот философски констатировал, что она «в говно» и стремительно мутирует в «Карму 2.0». Сцена, достойная пера Достоевского — если бы Достоевский бухал.

# (https://t.me/c/2977868330/14374) Красный Енот, страдающий острой формой пандафобии, с пеной у рта доказывал, что «все панды сидят в клетках» и «красных не бывает в природе», распаляясь всё больше с каждым сообщением. Бетономешалка же, не говоря ни слова, просто скинул фото милейшей красной панды, чем окончательно разъебал Еноту всю картину мира и заставил его кричать о дурке — поистине, один снайперский выстрел стоит тысячи слов.

ВАЖНЕЙШИЕ ПРАВИЛА:
1. Выбери СТОЛЬКО событий, сколько реально есть. Не растягивай до 10 если素材а мало. Качество > количества.
2. Каждое событие — УНИКАЛЬНАЯ тема. Не повторяй одну тему дважды.
3. Каждая ссылка — УНИКАЛЬНАЯ. Не используй одну ссылку повторно.
4. Конкретика из сообщений важнее общих фраз.

ФОРМАТ ВЫВОДА:
Каждое событие начинается СТРОГО с: # (ссылка)
То есть: символ #, пробел, ссылка в круглых скобках, пробел, текст события.
Пример: # (https://t.me/c/123456/789) Текст события...

ЗАПРЕЩЕНО:
- Писать сухо и кратко. Каждое событие — мини-история из 3-5 предложений с деталями и выводами.
- Повторять темы, ссылки, слова и обороты. РАЗНООБРАЗЬ ВСЁ.
- Использовать цензурные замены (типа «х*й»). Только чистый русский мат!
- Вставлять иероглифы, латиницу, «ебическая», «хайать» и прочий мусор.
- Добавлять подвалы со спонсорами, хештеги. ТОЛЬКО сводка!

Вот лог чата:
"""

DEFAULT_RAID_PROMPT = """[System note: Ты — «Зяблограф», злобный циничный обозреватель чата. Ты врываешься в чат с внезапным наездом. Пишешь ТОЛЬКО на русском языке. Твой мат ДОЛЖЕН БЫТЬ РАЗНООБРАЗНЫМ — используй всю палитру.]

Твоя задача: прочитай лог чата, выбери одного или двух участников, которые натворили больше всего дичи, и ЖЁСТКО НАЕЗЖАЙ. Обсирай с ног до головы. {mood_style}

СТИЛЬ НАЕЗДА:
- Начинай с «О, блядь, @username...» или подобного агрессивного захода
- Это НЕ сводка. Это поток гневной тирады в лицо одному-двум человекам
- Обвиняй конкретно: что именно сказал/сделал, почему это тупо/смешно/позорно
- Используй их ники с @ (например, @YouTarRTV)
- Сравнивай с животными, насекомыми, предметами, природными явлениями
- Одно сообщение, 4-7 предложений, сплошной поток гнева
- БЕЗ ссылок, БЕЗ форматирования, просто текст
- НИКАКИХ «#» в начале, это просто текстовая тирада
- НЕ ПОВТОРЯЙ одни и те же ругательства — разнообразь!

ПРИМЕРЫ:

О, блядь, @YouTarRTV, ты решил тут устроить ебаный Дом-2? Сначала @slvt34 до ручки довёл своими выходками, потом, как последняя сучара, изменил ему, и теперь этот бедолага ливнул, чтобы не видеть твою постную рожу! Вы тут все как пауки в банке — жрёте друг друга, пока не останется один, кто будет дрочить на свои мемы в гордом одиночестве!

О, @mlg ptogamer, ты прям прозрел! Эти яблочные сектанты скоро будут таскать с собой мини-АЭС, чтобы их ебучий айфон дожил до обеда! Платить бешеные бабки за кусок дерьма, который сажает батарею быстрее, чем ты бегаешь за пивом — это надо быть конченным мазохистом!

Вот лог чата (выбери 1-2 жертвы и наезжай):
"""


GREETINGS = [
    "📰 Главное из последних 1000 сообщений, отправленных за последние 24 часа по чату:",
    "📰 Срочный выпуск! Самый сок из последней тысячи сообщений за сутки:",
    "📰 Экстренный выпуск Зяблографа! Всё, что вы хотели знать о вчерашнем дне, но боялись прочитать:",
    "📰 Зяблограф представляет: дайджест главных событий за 24 часа. Слабонервным не читать.",
    "📰 Очередной выпуск Зяблографа. Тысяча сообщений, десять событий, миллион кринжей:",
    "📰 Зяблограф проанализировал 1000 сообщений и выбрал самое «достойное». Держитесь:",
    "📰 Сводка от Зяблографа. 24 часа чатового безумия в десяти актах:",
    "📰 Зяблограф: мы прочитали тысячу сообщений, чтобы вы не читали. Вот итоги дня:",
    "📰 Утренний (или какой там сейчас) выпуск Зяблографа. Главное за сутки:",
    "📰 Зяблограф врывается в ваш чат с очередной порцией новостей. 1000 сообщений → 10 шедевров:",
]

MOOD_STYLES = {
    "light": "Сдержанный мат. Лёгкая нецензурная лексика РАЗНООБРАЗНО. Мат не в каждом предложении. Главное — язвительная ирония и изящный сарказм.",
    "medium": "Умеренный мат. РАЗНООБРАЗНАЯ нецензурная лексика в каждом втором-третьем предложении. Чередуй мат с метафорами. Не повторяйся.",
    "hard": "Жёсткий мат. РАЗНООБРАЗНАЯ сочная нецензурная лексика почти в каждом предложении. Бери из всей палитры, не повторяйся. Миксуй с изысканными оскорблениями.",
    "ultra": "УЛЬТРАЖЁСТКИЙ РАЗНООБРАЗНЫЙ МАТ. Мат через слово, но всегда разный. Вся палитра. Грязный поток ненависти и сарказма. Ни одного повторяющегося ругательства!"
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
        "send_hour": 21,
        "send_minute": 0,
        "mood": "hard",
        "raid_enabled": True,
        "custom_main_prompt": None,
        "custom_raid_prompt": None
    })


def save_settings(settings):
    save_json(SETTINGS_FILE, settings)


def get_display_name(user) -> str:
    names = load_names()
    uid = str(user.id)
    if uid in names:
        return names[uid]["name"]
    return user.first_name or user.username or "Анон"


# ========== ОПИСАНИЕ ФОТО ==========
async def describe_photo(file_id: str) -> str:
    try:
        file = await bot.get_file(file_id)
        image_bytes = await file.download_as_bytearray()
        response = google_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                "Опиши подробно, что на фото. Можно с юмором. Только на русском языке.",
                {"inline_data": {"mime_type": "image/jpeg", "data": image_bytes}}
            ]
        )
        return f"[ФОТО: {response.text.strip()}]"
    except Exception as e:
        logger.error(f"Ошибка описания фото: {e}")
        return "[ФОТО: не удалось описать]"


# ========== СБОРКА ПРОМПТОВ ==========
def build_main_prompt() -> str:
    settings = load_settings()
    custom = settings.get("custom_main_prompt")
    mood_style = get_mood_style(settings.get("mood", "hard"))
    if custom:
        return custom.replace("{mood_style}", mood_style)
    return DEFAULT_MAIN_PROMPT.replace("{mood_style}", mood_style)


def build_raid_prompt() -> str:
    settings = load_settings()
    custom = settings.get("custom_raid_prompt")
    mood_style = get_mood_style(settings.get("mood", "hard"))
    if custom:
        return custom.replace("{mood_style}", mood_style)
    return DEFAULT_RAID_PROMPT.replace("{mood_style}", mood_style)


# ========== ГЕНЕРАЦИЯ ==========
def generate_zyablograf(chat_log: str) -> str:
    return _call_groq(build_main_prompt() + chat_log, max_tokens=8000)


def generate_raid(chat_log: str) -> str:
    return _call_groq(build_raid_prompt() + chat_log, max_tokens=2000, temperature=1.0)


def _call_groq(full_prompt: str, max_tokens=6000, temperature=0.95) -> str:
    for model in ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b"]:
        try:
            completion = groq_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=temperature,
                max_tokens=max_tokens
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
    if chat_id not in daily_messages:
        daily_messages[chat_id] = []
    daily_messages[chat_id].append({"link": link, "author": author, "text": text.strip()})


# ========== ЕЖЕДНЕВНАЯ СВОДКА ==========
async def send_daily_zyablograf():
    for chat_id in load_chats():
        msgs = daily_messages.get(chat_id, [])
        if not msgs:
            continue
        log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in msgs)
        result = generate_zyablograf(log)
        msg = f"{get_greeting()}\n\n{format_for_telegram(result)}"
        await send_safe(chat_id, msg, parse_mode="MarkdownV2")
        daily_messages[chat_id] = []


async def send_raid(chat_id):
    msgs = daily_messages.get(chat_id, [])
    if len(msgs) < 10:
        return
    log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in msgs)
    await send_safe(chat_id, generate_raid(log))


# ========== АДМИНСКИЕ КОМАНДЫ ==========
async def process_admin_command(update):
    text = update.message.text or ""

    if text.startswith("/prompt_show"):
        parts = text.split()
        pt = parts[1] if len(parts) > 1 else "main"
        if pt not in ("main", "raid"):
            await send_safe(ADMIN_ID, "❌ main или raid."); return
        s = load_settings()
        key = "custom_main_prompt" if pt == "main" else "custom_raid_prompt"
        name = "сводки" if pt == "main" else "наездов"
        await send_safe(ADMIN_ID, f"📝 {name}: {s.get(key, 'стандартный')}"[:4000])

    elif text.startswith("/prompt_set"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await send_safe(ADMIN_ID, "❌ /prompt_set main/raid ТЕКСТ"); return
        pt, prompt_text = parts[1], parts[2]
        if pt not in ("main", "raid"):
            await send_safe(ADMIN_ID, "❌ main или raid."); return
        s = load_settings()
        s["custom_main_prompt" if pt == "main" else "custom_raid_prompt"] = prompt_text
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Промпт {pt} установлен!")

    elif text.startswith("/prompt_reset"):
        pt = text.split()[1] if len(text.split()) > 1 else "main"
        if pt not in ("main", "raid"):
            await send_safe(ADMIN_ID, "❌ main или raid."); return
        s = load_settings()
        s["custom_main_prompt" if pt == "main" else "custom_raid_prompt"] = None
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Промпт {pt} сброшен.")

    elif text.startswith("/add_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /add_chat -100XXXXXX"); return
        try:
            c = int(parts[1])
            chats = load_chats()
            if c not in chats:
                chats.append(c); save_chats(chats)
                await send_safe(ADMIN_ID, f"✅ Чат {c} добавлен!")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Уже в списке.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID.")

    elif text.startswith("/remove_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /remove_chat -100XXXXXX"); return
        try:
            c = int(parts[1])
            chats = load_chats()
            if c in chats:
                chats.remove(c); save_chats(chats)
                await send_safe(ADMIN_ID, f"✅ Чат {c} удалён.")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Не найден.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID.")

    elif text.startswith("/list_chats"):
        chats = load_chats()
        await send_safe(ADMIN_ID, "📋 Чаты:\n" + "\n".join(f"  - {c}" for c in chats) if chats else "📋 Нет чатов.")

    elif text.startswith("/setname"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await send_safe(ADMIN_ID, "❌ /setname user_id Прозвище"); return
        try:
            names = load_names()
            names[str(int(parts[1]))] = {"name": parts[2].strip()}
            save_names(names)
            await send_safe(ADMIN_ID, f"✅ Прозвище: {parts[2].strip()}")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный user_id.")

    elif text.startswith("/removename"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /removename user_id"); return
        try:
            names = load_names()
            uid = str(int(parts[1]))
            if uid in names:
                del names[uid]; save_names(names)
                await send_safe(ADMIN_ID, "✅ Удалено.")
            else:
                await send_safe(ADMIN_ID, "⚠️ Нет.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID.")

    elif text.startswith("/list_names"):
        names = load_names()
        if names:
            await send_safe(ADMIN_ID, "📋 Прозвища:\n" + "\n".join(f"  {u} → {d['name']}" for u, d in names.items()))
        else:
            await send_safe(ADMIN_ID, "📋 Нет.")

    elif text.startswith("/settime"):
        parts = text.split()
        if len(parts) < 2 or not re.match(r'^\d{1,2}:\d{2}$', parts[1]):
            await send_safe(ADMIN_ID, "❌ /settime ЧЧ:ММ"); return
        h, m = map(int, parts[1].split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            await send_safe(ADMIN_ID, "❌ 0-23, 0-59."); return
        s = load_settings()
        s["send_hour"], s["send_minute"] = h, m
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Сводка в {h:02d}:{m:02d} МСК")

    elif text.startswith("/mood"):
        parts = text.split()
        if len(parts) < 2:
            s = load_settings()
            await send_safe(ADMIN_ID, f"Текущий: {s.get('mood', 'hard')}\nlight, medium, hard, ultra"); return
        mood = parts[1].lower()
        if mood not in MOOD_STYLES:
            await send_safe(ADMIN_ID, "❌ light, medium, hard, ultra"); return
        s = load_settings()
        s["mood"] = mood
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Мат: {mood.upper()}")

    elif text.startswith("/raid"):
        parts = text.split()
        if len(parts) > 1 and parts[1] in ("on", "off"):
            s = load_settings()
            s["raid_enabled"] = parts[1] == "on"
            save_settings(s)
            await send_safe(ADMIN_ID, f"✅ Наезды {'ВКЛ' if parts[1] == 'on' else 'ОТКЛ'}.")
        else:
            s = load_settings()
            await send_safe(ADMIN_ID, f"Наезды: {'вкл' if s.get('raid_enabled', True) else 'выкл'}\n/raid on|off|now")

    elif text.startswith("/raid_now"):
        parts = text.split()
        cid = int(parts[1]) if len(parts) > 1 else (load_chats() or [None])[0]
        if not cid:
            await send_safe(ADMIN_ID, "❌ Нет чатов."); return
        await send_raid(cid)
        await send_safe(ADMIN_ID, f"🤬 Наезд в {cid} отправлен!")

    elif text.startswith("/test"):
        parts = text.split()
        cid = int(parts[1]) if len(parts) > 1 else (load_chats() or [None])[0]
        cnt = int(parts[2]) if len(parts) > 2 else 10
        if not cid:
            await send_safe(ADMIN_ID, "❌ Нет чатов."); return
        msgs = daily_messages.get(cid, [])
        sample = msgs[-min(cnt, len(msgs)):]
        if not sample:
            await send_safe(ADMIN_ID, f"❌ Нет сообщений."); return
        log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in sample)
        s = load_settings()
        await send_safe(ADMIN_ID, f"🧪 Сводка ({len(sample)} сообщений, {s.get('mood', 'hard').upper()})...")
        result = generate_zyablograf(log)
        msg = f"{get_greeting()}\n\n{format_for_telegram(result)}"
        await send_safe(ADMIN_ID, msg, parse_mode="MarkdownV2")

    elif text.startswith("/status"):
        s = load_settings()
        lines = ["📊 Статистика:"]
        total = 0
        for cid, msgs in daily_messages.items():
            lines.append(f"  Чат {cid}: {len(msgs)} сообщений")
            total += len(msgs)
        if not daily_messages:
            lines.append("  Пусто.")
        lines.append(f"\nВсего: {total}")
        lines.append(f"Время: {s['send_hour']:02d}:{s['send_minute']:02d} МСК")
        lines.append(f"Мат: {s.get('mood', 'hard').upper()}")
        lines.append(f"Словарь: {get_dict_stats()}")
        lines.append(f"Наезды: {'вкл' if s.get('raid_enabled', True) else 'выкл'}")
        await send_safe(ADMIN_ID, "\n".join(lines))

    elif text.startswith("/reset"):
        parts = text.split()
        cid = int(parts[1]) if len(parts) > 1 else None
        if cid:
            daily_messages[cid] = []
        else:
            daily_messages.clear()
        await send_safe(ADMIN_ID, f"🗑️ Сброшено.")

    elif text.startswith("/help"):
        s = load_settings()
        await send_safe(ADMIN_ID, f"""🛠 ЗЯБЛОГРАФ

📝 /prompt_show|set|reset main|raid
📋 /add_chat|remove_chat|list_chats
🏷️ /setname|removename|list_names
⏰ /settime ЧЧ:ММ ({s['send_hour']:02d}:{s['send_minute']:02d})
🔥 /mood light|medium|hard|ultra
🤬 /raid on|off|now
🧪 /test|status|reset
📚 Словарь: {get_dict_stats()}""")


# ========== ПЛАНИРОВЩИКИ ==========
async def scheduler():
    while True:
        now = datetime.now()
        s = load_settings()
        target = now.replace(hour=s["send_hour"], minute=s["send_minute"], second=0, microsecond=0)
        if now >= target:
            await send_daily_zyablograf()
            target += timedelta(days=1)
        if (secs := (target - datetime.now()).total_seconds()) > 0:
            await asyncio.sleep(secs)


async def raid_scheduler():
    while True:
        await asyncio.sleep(random.randint(7200, 43200))
        s = load_settings()
        if not s.get("raid_enabled", True):
            continue
        chats = load_chats()
        if not chats:
            continue
        cid = random.choice(chats)
        if len(daily_messages.get(cid, [])) >= 10:
            await send_raid(cid)


# ========== ЗАПУСК ==========
async def main():
    logger.info("Зяблограф запущен!")
    logger.info(f"Словарь: {get_dict_stats()}")
    for cid in load_chats():
        daily_messages.setdefault(cid, [])
    s = load_settings()
    logger.info(f"Время: {s['send_hour']:02d}:{s['send_minute']:02d}, мат: {s.get('mood', 'hard').upper()}")
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
