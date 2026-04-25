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

groq_client = Groq(api_key=GROQ_API_KEY)
google_client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=BOT_TOKEN)

daily_messages = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ========== РАСШИРЕННЫЙ СЛОВАРЬ МАТА ==========
SWEAR_DICT = {
    "существительные": [
        "пиздец", "хуй", "пизда", "залупа", "еблан", "уёбище", "мразь", "гандон", "пидорас", "выблядок",
        "долбоёб", "хуесос", "мудак", "скотина", "падла", "сучара", "чертила", "дебил", "кретин", "имбецил",
        "шизоид", "дегенерат", "отморозок", "уродина", "чмо", "чмырь", "шваль", "падлюка", "говнюк", "жополиз",
        "хренотень", "параша", "говно", "дерьмо", "срань", "блевотень", "убожество", "ничтожество", "отребье",
        "гнида", "стервец", "прохвост", "шантрапа", "шобла", "шушера", "сброд", "отребье", "отребье",
        "ссанина", "бздёж", "пердёж", "дристня", "блевотина", "харкота", "сопля", "гной", "чирей", "фурункул"
    ],
    "глаголы": [
        "обосрался", "наложил в штаны", "навалил кучу", "насрал в душу", "облевал", "обоссал",
        "выхуярился", "защеканился", "залупился", "упоролся", "долбанулся", "ёбнулся", "прихуел",
        "охуел", "ахуел", "обалдел", "опупел", "офигел", "охренел", "озверел", "взбеленился",
        "взъерошился", "зашебуршился", "заколготился", "захорохорился", "распетушился", "раскукарекался",
        "заскулил", "завыл", "захныкал", "заныл", "запричитал", "разорался", "заголосил", "заверещал",
        "затявкал", "забрехал", "забулькал", "забубнил", "затараторил", "раззвонил", "раструбил"
    ],
    "прилагательные": [
        "ебанутый", "отбитый", "конченный", "ёбнутый", "хуёвый", "пиздецовый", "говённый", "сраный",
        "ущербный", "недоделанный", "бракованный", "трёхнутый", "бешеный", "буйный", "ненормальный",
        "неадекватный", "нездоровый", "больной", "хворый", "юродливый", "блаженный", "скорбный",
        "убогий", "сирый", "нищий духом", "плоский", "пресный", "постный", "никакой", "вялый",
        "дряблый", "хлипкий", "щуплый", "плюгавый", "тщедушный", "невзрачный", "жалкий", "ничтожный"
    ],
    "наречия": [
        "пиздецки", "хуёво", "ёбнуто", "всрато", "говённо", "срамно", "позорно", "убого",
        "дико", "лютово", "зверски", "бешено", "неистово", "яростно", "безумно", "дичайше",
        "жутко", "страшно", "ужасно", "чудовищно", "катастрофически", "фатально", "смертельно",
        "до усрачки", "до посинения", "до хрипоты", "до скрежета", "до дрожи", "до трясучки"
    ]
}

# ========== РАСШИРЕННЫЙ СЛОВАРЬ НЕМАТЕРНЫХ ВЫРАЗИТЕЛЬНЫХ СРЕДСТВ ==========
EXPRESSIVE_DICT = {
    "эпитеты_для_людей": [
        "великовозрастный детина", "комнатный стратег", "диванный эксперт", "непризнанный гений",
        "вечный страдалец", "потомственный клоун", "заслуженный артист чата", "мастер спорта по пиздежу",
        "виртуоз клавиатуры", "повелитель скриншотов", "хранитель древних мемов", "коллекционер кринжей",
        "светоч мысли", "кладезь мудрости", "ходячий анекдот", "живой мем", "король драмы",
        "император абсурда", "властелин бреда", "герой нашего времени", "жертва собственного величия",
        "адепт секты свидетелей дивана", "последователь учения «и так сойдёт»", "носитель сакрального знания",
        "рыцарь печального образа", "Дон Кихот на минималках", "Наполеон в изгнании", "непризнанный пророк"
    ],
    "метафоры": [
        "как слон в посудной лавке", "как пьяный голубь, пытающийся склеить самку",
        "как рыба об лёд", "как слепой котяра в незнакомом подвале",
        "словно белка в колесе", "будто ёжик в тумане", "как баран на новые ворота",
        "словно корова языком слизала", "как будто мешок с картошкой уронили",
        "будто кто-то насрал в вентилятор", "как в замедленной съёмке",
        "словно в театре абсурда", "как в дешёвом цирке", "будто на сцене сельского клуба"
    ],
    "сравнения": [
        "быстрее, чем слухи в женском коллективе", "активнее, чем бабки у подъезда",
        "громче, чем соседский перфоратор в воскресенье", "тупее, чем пробка от графина",
        "медленнее, чем очередь в поликлинике", "нуднее, чем лекция по сопромату",
        "важнее, чем павлин в брачный период", "напыщеннее, чем индюк на птичьем дворе",
        "бесполезнее, чем светофор в пустыне", "нужнее, чем прошлогодний снег",
        "интереснее, чем смотреть на сохнущую краску", "смешнее, чем похороны клоуна",
        "трагичнее, чем концовка «Титаника»", "драматичнее, чем последняя серия мыльной оперы"
    ],
    "деепричастные_обороты": [
        "брызгая слюной от ярости", "захлёбываясь в собственной важности",
        "путаясь в показаниях", "противореча сам себе", "забыв, с чего начал",
        "потеряв нить повествования", "увязнув в собственном вранье",
        "барахтаясь в потоке сознания", "цепляясь за последние крохи разума",
        "пытаясь сохранить лицо", "делая вид, что так и было задумано",
        "с упорством, достойным лучшего применения", "с настойчивостью носорога",
        "с грацией пьяного бегемота", "с достоинством короля, севшего мимо трона"
    ]
}

# ========== РАСШИРЕННЫЙ ПРОМПТ ПО УМОЛЧАНИЮ ==========
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

ФОРМАТ ВЫВОДА:
Каждое событие начинается СТРОГО с: # (ссылка)
То есть: символ #, пробел, ссылка в круглых скобках, пробел, текст события.
Пример: # (https://t.me/c/123456/789) Текст события...

ЗАПРЕЩЕНО:
- Писать сухо и кратко. Каждое событие — мини-история из 3-5 предложений с деталями и выводами.
- Повторять одни и те же слова и обороты. РАЗНООБРАЗЬ ЛЕКСИКУ.
- Использовать цензурные замены (типа «х*й», «п***ец»). Только чистый русский мат!
- Вставлять иероглифы, английские слова, «ебическая», «хайать» и прочий мусор.
- Добавлять подвалы со спонсорами, хештеги. ТОЛЬКО сводка из 10 событий!

Вот лог чата:
"""

DEFAULT_RAID_PROMPT = """[System note: Ты — «Зяблограф», злобный циничный обозреватель чата. Ты врываешься в чат с внезапным наездом. Пишешь ТОЛЬКО на русском языке. Твой мат ДОЛЖЕН БЫТЬ РАЗНООБРАЗНЫМ — используй всю палитру: пиздец, хуй, ебать, обосрался, долбоёб, блядь, сука, хуесос, залупа, пизда, еблан, уёбище, мразь, гандон, пидорас, выблядок, говнюк, чмо, падла, сучара, скотина, дебил, кретин, дегенерат, отморозок, ничтожество, гнида.]

Твоя задача: прочитай лог чата, выбери одного или двух участников, которые натворили больше всего дичи, и ЖЁСТКО НАЕЗЖАЙ. Обсирай с ног до головы. {mood_style}

СТИЛЬ НАЕЗДА:
- Начинай с «О, блядь, @username...» или подобного агрессивного захода
- Это НЕ сводка. Это поток гневной тирады в лицо одному-двум человекам
- Обвиняй, унижай, высмеивай с матом и гиперболами
- Используй их ники с @ (например, @YouTarRTV, @slvt34)
- Сравнивай с животными, насекомыми, предметами, природными явлениями
- Одно сообщение, 4-7 предложений, сплошной поток гнева
- БЕЗ ссылок, БЕЗ форматирования, просто текст
- НИКАКИХ «#» в начале, это просто текстовая тирада
- НЕ ПОВТОРЯЙ одни и те же ругательства — разнообразь!

ПРИМЕРЫ:

О, блядь, @YouTarRTV, ты решил тут устроить ебаный Дом-2? Сначала @slvt34 до ручки довёл своими выходками, потом, как последняя сучара, изменил ему, и теперь этот бедолага ливнул, чтобы не видеть твою постную рожу! Вы тут все как пауки в банке — жрёте друг друга, пока не останется один, кто будет дрочить на свои мемы в гордом одиночестве!

О, @mlg ptogamer, ты прям прозрел! Эти яблочные сектанты скоро будут таскать с собой мини-АЭС, чтобы их ебучий айфон дожил до обеда! Платить бешеные бабки за кусок говна, который сажает батарею быстрее, чем ты бегаешь за пивом — это надо быть конченным мазохистом с позолоченной клеткой!

Вот лог чата (выбери жертву и наезжай):
"""


# ========== ПРИВЕТСТВЕННЫЕ ФРАЗЫ ==========
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
    "light": "Сдержанный мат. Лёгкая нецензурная лексика РАЗНООБРАЗНО (херня, хрень, фигня, обалдел, охуел, зашибись, хер с ним, ёлки-палки через жопу). Мат не в каждом предложении. Главное — язвительная ирония и изящный сарказм. Используй богатый словарь эпитетов, метафор и сравнений.",
    
    "medium": "Умеренный мат. РАЗНООБРАЗНАЯ нецензурная лексика в каждом втором-третьем предложении (пиздец, хуй, ебать, обосрался, долбоёб, мудак, сучара, говнюк, дебил). Чередуй мат с язвительными метафорами. Сарказм и ирония с матерными вставками. Не повторяйся — каждое ругательство должно быть свежим.",
    
    "hard": "Жёсткий мат. РАЗНООБРАЗНАЯ сочная нецензурная лексика почти в каждом предложении. БЕРИ ИЗ ВСЕЙ ПАЛИТРЫ: пиздец, хуй, ебать, обосрался, долбоёб, блядь, сука, хуесос, залупа, пизда, еблан, уёбище, мразь, гандон, пидорас, выблядок, скотина, падла, сучара, чертила, дегенерат, отморозок, чмо, говнюк, гнида. Мат должен звучать естественно, смешно и НЕ ПОВТОРЯТЬСЯ. Миксуй с изысканными оскорблениями и уничижительными эпитетами.",
    
    "ultra": "УЛЬТРАЖЁСТКИЙ РАЗНООБРАЗНЫЙ МАТ. Ты — озлобленный псих с имиджборда, прошедший огонь, воду и медные трубы. Мат через слово, НО РАЗНООБРАЗНЫЙ ДО ПРЕДЕЛА. Используй ВСЮ ПАЛИТРУ: пиздец, хуй, ебать, обосрался с подливой, долбоёб ебаный, блядь, сука, хуесос, залупа, пизда, еблан, уёбище, мразь, гандон, пидорас, выблядок, скотина, падла, сучара, чертила, дегенерат, отморозок, чмо, говнюк, гнида, ссанина, бздёж, пердёж, дристня, блевотина. Твоя речь — это грязный поток ненависти, сарказма и богатейшего матерного лексикона. Ни одного повторяющегося ругательства!"
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
        logger.info(f"Скачано фото: {len(image_bytes)} байт")
        response = google_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                "Опиши подробно, что на фото. Можно с юмором. Только на русском языке.",
                {"inline_data": {"mime_type": "image/jpeg", "data": image_bytes}}
            ]
        )
        desc = response.text.strip()
        logger.info(f"Описание фото: {desc[:100]}...")
        return f"[ФОТО: {desc}]"
    except Exception as e:
        logger.error(f"Ошибка описания фото: {type(e).__name__}: {e}")
        return "[ФОТО: не удалось описать]"


# ========== СБОРКА ПРОМПТОВ ==========
def build_main_prompt() -> str:
    settings = load_settings()
    custom = settings.get("custom_main_prompt")
    mood_style = get_mood_style(settings.get("mood", "hard"))
    if custom:
        return custom.replace("{mood_style}", mood_style)
    else:
        return DEFAULT_MAIN_PROMPT.replace("{mood_style}", mood_style)


def build_raid_prompt() -> str:
    settings = load_settings()
    custom = settings.get("custom_raid_prompt")
    mood_style = get_mood_style(settings.get("mood", "hard"))
    if custom:
        return custom.replace("{mood_style}", mood_style)
    else:
        return DEFAULT_RAID_PROMPT.replace("{mood_style}", mood_style)


# ========== ГЕНЕРАЦИЯ ==========
def generate_zyablograf(chat_log: str) -> str:
    prompt = build_main_prompt()
    return _call_groq(prompt + chat_log, max_tokens=8000)


def generate_raid(chat_log: str) -> str:
    prompt = build_raid_prompt()
    return _call_groq(prompt + chat_log, max_tokens=2000, temperature=1.0)


def _call_groq(full_prompt: str, max_tokens=6000, temperature=0.95) -> str:
    models = [
        "llama-3.3-70b-versatile",
        "deepseek-r1-distill-llama-70b",
    ]
    for model in models:
        try:
            completion = groq_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": full_prompt}],
                temperature=temperature,
                max_tokens=max_tokens
            )
            result = completion.choices[0].message.content
            return clean_output(result)
        except Exception as e:
            logger.error(f"Ошибка {model}: {e}")
            continue
    return "Зяблограф обосрался. Технический пиздец."


def clean_output(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[^\x00-\x7Fа-яА-ЯёЁ0-9\s\.,!?;:()«»""''\-—@#\n]', '', text)
    if "⭐️ Станьте спонсором" in text:
        text = text.split("⭐️ Станьте спонсором")[0].strip()
    text = re.sub(r'#вестник\s*', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'#зяблограф\s*', '', text, flags=re.IGNORECASE).strip()
    return text


def format_for_telegram(text: str) -> str:
    pattern = r'#\s*\((https?://t\.me/[^\s\)]+)\)'
    replacement = r'[#](\1)'
    return re.sub(pattern, replacement, text)


# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def handle_message(message):
    chat_id = message.chat.id
    chats = load_chats()
    if chat_id not in chats:
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
        file_id = message.photo[-1].file_id
        description = await describe_photo(file_id)
        text = f"{text}\n{description}" if text else description
    if not text:
        text = "[войс/стикер/мусор]"
    chat_id_str = str(chat_id).replace("-100", "")
    msg_link = f"https://t.me/c/{chat_id_str}/{message.message_id}"
    if chat_id not in daily_messages:
        daily_messages[chat_id] = []
    daily_messages[chat_id].append({
        "link": msg_link,
        "author": author,
        "text": text.strip()
    })


# ========== ЕЖЕДНЕВНАЯ СВОДКА ==========
async def send_daily_zyablograf():
    chats = load_chats()
    for chat_id in chats:
        messages = daily_messages.get(chat_id, [])
        if not messages:
            continue
        chat_log = "\n".join(
            f"[{m['link']}] {m['author']}: {m['text']}" for m in messages
        )
        result = generate_zyablograf(chat_log)
        greeting = get_greeting()
        formatted_body = format_for_telegram(result)
        final_message = f"{greeting}\n\n{formatted_body}"
        await send_safe(chat_id, final_message, parse_mode="MarkdownV2")
        daily_messages[chat_id] = []


async def send_raid(chat_id):
    messages = daily_messages.get(chat_id, [])
    if len(messages) < 10:
        return
    chat_log = "\n".join(
        f"[{m['link']}] {m['author']}: {m['text']}" for m in messages
    )
    raid_text = generate_raid(chat_log)
    await send_safe(chat_id, raid_text)


# ========== АДМИНСКИЕ КОМАНДЫ ==========
async def process_admin_command(update):
    text = update.message.text or ""

    if text.startswith("/prompt_show"):
        parts = text.split()
        pt = parts[1] if len(parts) > 1 else "main"
        s = load_settings()
        if pt not in ("main", "raid"):
            await send_safe(ADMIN_ID, "❌ main или raid.")
            return
        key = "custom_main_prompt" if pt == "main" else "custom_raid_prompt"
        name = "сводки" if pt == "main" else "наездов"
        if s.get(key):
            await send_safe(ADMIN_ID, f"📝 Кастомный промпт {name}:\n\n{s[key]}\n\nСбросить: /prompt_reset {pt}")
        else:
            await send_safe(ADMIN_ID, f"📝 Стандартный промпт {name}.\n\nУстановить: /prompt_set {pt} ТЕКСТ")

    elif text.startswith("/prompt_set"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await send_safe(ADMIN_ID, "❌ /prompt_set main/raid ТЕКСТ\nВставь {mood_style} для уровня мата.")
            return
        pt = parts[1]
        prompt_text = parts[2]
        if pt not in ("main", "raid"):
            await send_safe(ADMIN_ID, "❌ main или raid.")
            return
        s = load_settings()
        key = "custom_main_prompt" if pt == "main" else "custom_raid_prompt"
        s[key] = prompt_text
        save_settings(s)
        name = "сводки" if pt == "main" else "наездов"
        await send_safe(ADMIN_ID, f"✅ Промпт {name} установлен!\n/prompt_show {pt} — проверить\n/test — протестировать")

    elif text.startswith("/prompt_reset"):
        parts = text.split()
        pt = parts[1] if len(parts) > 1 else "main"
        if pt not in ("main", "raid"):
            await send_safe(ADMIN_ID, "❌ main или raid.")
            return
        s = load_settings()
        key = "custom_main_prompt" if pt == "main" else "custom_raid_prompt"
        s[key] = None
        save_settings(s)
        name = "сводки" if pt == "main" else "наездов"
        await send_safe(ADMIN_ID, f"✅ Промпт {name} сброшен.")

    elif text.startswith("/add_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /add_chat -100XXXXXX")
            return
        try:
            new_chat = int(parts[1])
            chats = load_chats()
            if new_chat not in chats:
                chats.append(new_chat)
                save_chats(chats)
                await send_safe(ADMIN_ID, f"✅ Чат {new_chat} добавлен!")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Чат {new_chat} уже в списке.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID.")

    elif text.startswith("/remove_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /remove_chat -100XXXXXX")
            return
        try:
            chat = int(parts[1])
            chats = load_chats()
            if chat in chats:
                chats.remove(chat)
                save_chats(chats)
                await send_safe(ADMIN_ID, f"✅ Чат {chat} удалён.")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Чат {chat} не найден.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID.")

    elif text.startswith("/list_chats"):
        chats = load_chats()
        if chats:
            await send_safe(ADMIN_ID, "📋 Чаты:\n" + "\n".join(f"  - {c}" for c in chats))
        else:
            await send_safe(ADMIN_ID, "📋 Нет чатов.")

    elif text.startswith("/setname"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await send_safe(ADMIN_ID, "❌ /setname user_id Прозвище")
            return
        try:
            uid = str(int(parts[1]))
            names = load_names()
            names[uid] = {"name": parts[2].strip()}
            save_names(names)
            await send_safe(ADMIN_ID, f"✅ Для {uid} прозвище: {parts[2].strip()}")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный user_id.")

    elif text.startswith("/removename"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /removename user_id")
            return
        try:
            uid = str(int(parts[1]))
            names = load_names()
            if uid in names:
                del names[uid]
                save_names(names)
                await send_safe(ADMIN_ID, f"✅ Прозвище удалено.")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Нет прозвища для {uid}.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный user_id.")

    elif text.startswith("/list_names"):
        names = load_names()
        if names:
            await send_safe(ADMIN_ID, "📋 Прозвища:\n" + "\n".join(f"  {u} → {d['name']}" for u, d in names.items()))
        else:
            await send_safe(ADMIN_ID, "📋 Прозвищ нет.")

    elif text.startswith("/settime"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ /settime ЧЧ:ММ")
            return
        time_str = parts[1]
        if not re.match(r'^\d{1,2}:\d{2}$', time_str):
            await send_safe(ADMIN_ID, "❌ Формат: ЧЧ:ММ")
            return
        hour, minute = map(int, time_str.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            await send_safe(ADMIN_ID, "❌ Часы 0-23, минуты 0-59.")
            return
        s = load_settings()
        s["send_hour"], s["send_minute"] = hour, minute
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Сводка в {hour:02d}:{minute:02d} МСК")

    elif text.startswith("/mood"):
        parts = text.split()
        if len(parts) < 2:
            s = load_settings()
            await send_safe(ADMIN_ID, f"Текущий: {s.get('mood', 'hard')}\nlight, medium, hard, ultra")
            return
        mood = parts[1].lower()
        if mood not in MOOD_STYLES:
            await send_safe(ADMIN_ID, "❌ light, medium, hard, ultra")
            return
        s = load_settings()
        s["mood"] = mood
        save_settings(s)
        await send_safe(ADMIN_ID, f"✅ Уровень мата: {mood.upper()} (разнообразный словарь из {sum(len(v) for v in SWEAR_DICT.values())} слов)")

    elif text.startswith("/raid"):
        parts = text.split()
        if len(parts) > 1 and parts[1] == "off":
            s = load_settings()
            s["raid_enabled"] = False
            save_settings(s)
            await send_safe(ADMIN_ID, "✅ Наезды ОТКЛ.")
        elif len(parts) > 1 and parts[1] == "on":
            s = load_settings()
            s["raid_enabled"] = True
            save_settings(s)
            await send_safe(ADMIN_ID, "✅ Наезды ВКЛ.")
        else:
            s = load_settings()
            status = "вкл" if s.get("raid_enabled", True) else "выкл"
            await send_safe(ADMIN_ID, f"Наезды: {status}\n/raid on | /raid off | /raid_now")

    elif text.startswith("/raid_now"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        if chat_id is None:
            chats = load_chats()
            if not chats:
                await send_safe(ADMIN_ID, "❌ Нет чатов.")
                return
            chat_id = chats[0]
        await send_safe(ADMIN_ID, f"🤬 Наезд для {chat_id}...")
        await send_raid(chat_id)
        await send_safe(ADMIN_ID, "✅ Отправлен!")

    elif text.startswith("/test"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        count = int(parts[2]) if len(parts) > 2 else 10
        if chat_id is None:
            chats = load_chats()
            if not chats:
                await send_safe(ADMIN_ID, "❌ Нет чатов.")
                return
            chat_id = chats[0]
        messages = daily_messages.get(chat_id, [])
        sample = messages[-min(count, len(messages)):]
        if not sample:
            await send_safe(ADMIN_ID, f"❌ Нет сообщений для {chat_id}.")
            return
        chat_log = "\n".join(f"[{m['link']}] {m['author']}: {m['text']}" for m in sample)
        s = load_settings()
        await send_safe(ADMIN_ID, f"🧪 Сводка (чат {chat_id}, {len(sample)} сообщений, {s.get('mood', 'hard').upper()})...")
        result = generate_zyablograf(chat_log)
        greeting = get_greeting()
        formatted_body = format_for_telegram(result)
        await send_safe(ADMIN_ID, f"{greeting}\n\n{formatted_body}", parse_mode="MarkdownV2")

    elif text.startswith("/status"):
        s = load_settings()
        mood = s.get("mood", "hard")
        raid = "вкл" if s.get("raid_enabled", True) else "выкл"
        lines = ["📊 Статистика:"]
        total = 0
        for cid, msgs in daily_messages.items():
            lines.append(f"  Чат {cid}: {len(msgs)} сообщений")
            total += len(msgs)
        if not daily_messages:
            lines.append("  Пусто.")
        lines.append(f"\nВсего: {total}")
        lines.append(f"Время сводки: {s['send_hour']:02d}:{s['send_minute']:02d} МСК")
        lines.append(f"Уровень мата: {mood.upper()} ({sum(len(v) for v in SWEAR_DICT.values())} слов)")
        lines.append(f"Модель: Llama 3.3")
        lines.append(f"Наезды: {raid}")
        await send_safe(ADMIN_ID, "\n".join(lines))

    elif text.startswith("/reset"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        if chat_id:
            daily_messages[chat_id] = []
            await send_safe(ADMIN_ID, f"🗑️ Лог {chat_id} сброшен.")
        else:
            daily_messages.clear()
            await send_safe(ADMIN_ID, "🗑️ Все логи сброшены.")

    elif text.startswith("/help"):
        s = load_settings()
        help_text = f"""
🛠 ЗЯБЛОГРАФ

📝 ПРОМПТЫ
  /prompt_show main|raid
  /prompt_set main|raid ТЕКСТ
  /prompt_reset main|raid

📋 ЧАТЫ  /add_chat | /remove_chat | /list_chats

🏷️ ПРОЗВИЩА  /setname | /removename | /list_names

⏰ ВРЕМЯ  /settime ЧЧ:ММ ({s['send_hour']:02d}:{s['send_minute']:02d})

🔥 МАТ  /mood light|medium|hard|ultra
  Словарь: {sum(len(v) for v in SWEAR_DICT.values())} слов

🤬 НАЕЗДЫ  /raid on|off | /raid_now

🧪 ТЕСТ  /test | /status | /reset
"""
        await send_safe(ADMIN_ID, help_text)


# ========== ПЛАНИРОВЩИКИ ==========
async def scheduler():
    while True:
        now = datetime.now()
        settings = load_settings()
        target = now.replace(hour=settings["send_hour"], minute=settings["send_minute"], second=0, microsecond=0)
        if now >= target:
            await send_daily_zyablograf()
            target += timedelta(days=1)
        sleep_seconds = (target - datetime.now()).total_seconds()
        if sleep_seconds > 0:
            await asyncio.sleep(sleep_seconds)


async def raid_scheduler():
    while True:
        delay = random.randint(7200, 43200)
        await asyncio.sleep(delay)
        settings = load_settings()
        if not settings.get("raid_enabled", True):
            continue
        chats = load_chats()
        if not chats:
            continue
        chat_id = random.choice(chats)
        messages = daily_messages.get(chat_id, [])
        if len(messages) < 10:
            continue
        await send_raid(chat_id)


# ========== ЗАПУСК ==========
async def main():
    logger.info("Зяблограф запущен!")
    chats = load_chats()
    for cid in chats:
        if cid not in daily_messages:
            daily_messages[cid] = []
    s = load_settings()
    logger.info(f"Время: {s['send_hour']:02d}:{s['send_minute']:02d}, мат: {s.get('mood', 'hard').upper()}")
    asyncio.create_task(scheduler())
    asyncio.create_task(raid_scheduler())
    offset = None
    while True:
        try:
            updates = await bot.get_updates(offset=offset, timeout=30, allowed_updates=["message"])
            for update in updates:
                if update.message:
                    if update.message.chat.id == ADMIN_ID:
                        await process_admin_command(update)
                    else:
                        await handle_message(update.message)
                offset = update.update_id + 1
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
