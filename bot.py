import os
import json
import re
import logging
import asyncio
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

# ========== КЛИЕНТЫ ==========
groq_client = Groq(api_key=GROQ_API_KEY)
google_client = genai.Client(api_key=GOOGLE_API_KEY)
bot = Bot(token=BOT_TOKEN)

daily_messages = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


# ========== БЕЗОПАСНАЯ ОТПРАВКА (БЕЗ MARKDOWN ОШИБОК) ==========
async def send_safe(chat_id, text, parse_mode=None):
    """Отправляет сообщение. Если Markdown ломается — отправляет без форматирования."""
    try:
        return await bot.send_message(chat_id, text, parse_mode=parse_mode)
    except TelegramError as e:
        if parse_mode:
            logger.warning(f"Markdown error, отправляю без форматирования: {e}")
            return await bot.send_message(chat_id, text)
        else:
            raise


def escape_md(text: str) -> str:
    """Экранирует спецсимволы для MarkdownV2."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


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
    default = {"send_hour": 21, "send_minute": 0}
    return load_json(SETTINGS_FILE, default)


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
                "Опиши кратко, что на фото. Можно с юмором. На русском.",
                {"inline_data": {"mime_type": "image/jpeg", "data": image_bytes}}
            ]
        )
        return f"[ФОТО: {response.text.strip()}]"
    except Exception as e:
        logger.error(f"Ошибка описания фото: {e}")
        return "[ФОТО: не удалось описать]"


# ========== ГЕНЕРАЦИЯ СВОДКИ ==========
def generate_vestnik(chat_log: str) -> str:
    prompt = """[System note: Ты — «Вестник», злобный, циничный, уставший от жизни обозреватель анонимного чата с многолетним стажем. Ты пишешь едкие сводки в стиле бульварной жёлтой прессы с имиджбордов. Твоя речь — это смесь высокого сарказма и грязного мата. Ты используешь сочную нецензурную лексику ОРГАНИЧНО, а не просто через слово. Ты — рассказчик, а не матершинник-робот.]

ВАЖНЕЙШЕЕ ПРАВИЛО ФОРМАТИРОВАНИЯ:
Каждое событие начинается СТРОГО с символа #, сразу после него БЕЗ ПРОБЕЛА идёт ссылка в круглых скобках: # (ссылка)
Пример: # (https://t.me/c/123456/789) Текст события...

СЛОВАРЬ ФИРМЕННЫХ ОБОРОТОВ ВЕСТНИКА (ИСПОЛЬЗУЙ ИХ):
- «Наш местный [Казанова/Казанова с амнезией/философ/страдалец]»
- «Этот [бедолага/ебанат/гений мысли/извращенец/ценитель прекрасного]»
- «Снова начал свою заезженную пластинку»
- «Обосрался с подливой»
- «С пеной у рта доказывал»
- «Превратив чат в [филиал дурдома/цирк уродов/парад членов/сеанс экзорцизма]»
- «С драматизмом истинной героини мыльной оперы»
- «Повергла в шок даже видавшего виды»
- «Окончательно добив [имя]»
- «Заставив его кричать о дурке»
- «Как жалкий писк загнанной мыши»
- «Видимо, решил, что...»
- «Похоже, [имя] настолько...»
- «Ну что ж, каждому своё»
- «Вот это я понимаю, [ирония]»
- «Чем вызвал бурю [негодования/восторга/смеха]»
- «Подлил масла в огонь»
- «Словно пьяный голубь, пытающийся склеить самку»
- «С олимпийским спокойствием»
- «Как истинный [тролль/стратег/ценитель]»

ПРИМЕРЫ ПРАВИЛЬНОГО СТИЛЯ:

# (https://t.me/c/2977868330/14181) Владис, наш местный Казанова с амнезией, снова начал свою заезженную пластинку про то, как он «ебал Коростину периодически», повторяя это как мантру, пока все вокруг пытались понять, пьян он или просто застрял в петле времени. Этот бедолага так усердно пытается убедить всех в своих подвигах, что уже сам, кажется, забыл, что такое трезвость и оригинальность.

# (https://t.me/c/2977868330/14202) УнивёрсХарт, видимо, решила, что чат – это ее личный дневник пьяных откровений, сначала заявив, что она «НЕ ШЛЮХА, А ТУСОВЩИЦА», а потом, под воздействием алкоголя, начала спамить сообщениями, обзывая Блэк Маге «лохом» и предлагая «саси клитор балда», пока тот лишь констатировал, что она «в говно» и превращается в «Карму 2.0».

# (https://t.me/c/2977868330/14374) Красный Енот, видимо, страдающий от пандафобии, начал яростно доказывать, что «все панды в клетках» и «красных не бывает», пока Бетономешалка, в ответ на его истерику, не прислал фото милейшей красной панды, окончательно добив Енота и заставив его кричать о дурке.

ЗАПРЕЩЕНО:
- Писать сухо и кратко. Каждое событие — мини-история из 3-5 предложений.
- Использовать цензурные замены мата (типа «х*й», «п***ец», «б***ь»). Только чистый мат!
- Добавлять подвалы со спонсорами, хештеги #вестник, «⭐️ Станьте спонсором». ТОЛЬКО сводка!

Вот лог чата:
"""
    try:
        completion = groq_client.chat.completions.create(
            model="deepseek-r1-distill-llama-70b",
            messages=[{"role": "user", "content": prompt + chat_log}],
            temperature=0.95,
            max_tokens=8000
        )
        result = completion.choices[0].message.content
        return clean_vestnik_output(result)
    except Exception as e:
        logger.error(f"Ошибка Groq: {e}")
        try:
            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt + chat_log}],
                temperature=0.95,
                max_tokens=6000
            )
            result = completion.choices[0].message.content
            return clean_vestnik_output(result)
        except Exception as e2:
            logger.error(f"Ошибка фолбэка: {e2}")
            return "Вестник обосрался. Технический пиздец."


def clean_vestnik_output(text: str) -> str:
    text = text.strip()
    if "⭐️ Станьте спонсором" in text:
        text = text.split("⭐️ Станьте спонсором")[0].strip()
    text = re.sub(r'#вестник\s*', '', text, flags=re.IGNORECASE).strip()
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

    if not text:
        text = "[мусор]"

    if message.photo:
        file_id = message.photo[-1].file_id
        description = await describe_photo(file_id)
        text = f"{text}\n{description}" if text != "[мусор]" else description

    chat_id_str = str(chat_id).replace("-100", "")
    msg_link = f"https://t.me/c/{chat_id_str}/{message.message_id}"

    if chat_id not in daily_messages:
        daily_messages[chat_id] = []

    daily_messages[chat_id].append({
        "link": msg_link,
        "author": author,
        "text": text.strip()
    })
    logger.info(f"[+] {author} в чате {chat_id}")


# ========== ЕЖЕДНЕВНАЯ СВОДКА ==========
async def send_daily_vestnik():
    chats = load_chats()
    for chat_id in chats:
        messages = daily_messages.get(chat_id, [])
        if not messages:
            continue

        chat_log = "\n".join(
            f"[{m['link']}] {m['author']}: {m['text']}" for m in messages
        )

        logger.info(f"Генерация сводки для чата {chat_id} ({len(messages)} сообщений)...")
        result = generate_vestnik(chat_log)
        formatted = format_for_telegram(result)

        await send_safe(chat_id, formatted, parse_mode="MarkdownV2")
        daily_messages[chat_id] = []


# ========== АДМИНСКИЕ КОМАНДЫ ==========
async def process_admin_command(update):
    text = update.message.text or ""

    # Все ответы отправляем БЕЗ Markdown, чтобы избежать ошибок экранирования
    # Только в /help и /list_chats используем Markdown с осторожностью

    if text.startswith("/add_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ Использование: /add_chat -100XXXXXX\nПример: /add_chat -1002977868330")
            return
        try:
            new_chat = int(parts[1])
            chats = load_chats()
            if new_chat not in chats:
                chats.append(new_chat)
                save_chats(chats)
                await send_safe(ADMIN_ID, f"✅ Чат {new_chat} добавлен! Бот начнёт собирать сообщения из всех топиков.")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Чат {new_chat} уже в списке.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID чата. Пример: /add_chat -1002977868330")

    elif text.startswith("/remove_chat"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ Использование: /remove_chat -100XXXXXX\nПример: /remove_chat -1002977868330")
            return
        try:
            chat = int(parts[1])
            chats = load_chats()
            if chat in chats:
                chats.remove(chat)
                save_chats(chats)
                await send_safe(ADMIN_ID, f"✅ Чат {chat} удалён.")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Чат {chat} не найден в списке отслеживаемых.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный ID чата.")

    elif text.startswith("/list_chats"):
        chats = load_chats()
        if chats:
            lines = ["📋 Отслеживаемые чаты:"]
            for c in chats:
                lines.append(f"  - {c}")
            lines.append("")
            lines.append("Добавить новый: /add_chat -100XXXXXX")
            await send_safe(ADMIN_ID, "\n".join(lines))
        else:
            await send_safe(ADMIN_ID, "📋 Нет отслеживаемых чатов.\n\nДобавь первый: /add_chat -100XXXXXX")

    elif text.startswith("/setname"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await send_safe(ADMIN_ID, "❌ Использование: /setname user_id Прозвище\nПример: /setname 123456789 Васян\n\nКак узнать ID: добавь @getidsbot в чат")
            return
        try:
            uid = str(int(parts[1]))
            nickname = parts[2].strip()
            names = load_names()
            names[uid] = {
                "name": nickname,
                "updated": datetime.now().isoformat()
            }
            save_names(names)
            await send_safe(ADMIN_ID, f"✅ Для пользователя {uid} установлено прозвище: {nickname}\n\nКак узнать ID: добавь @getidsbot в чат или проверь логи бота.")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный user_id. Он должен быть числом. Пример: /setname 123456789 Васян")

    elif text.startswith("/removename"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ Использование: /removename user_id\nПример: /removename 123456789")
            return
        try:
            uid = str(int(parts[1]))
            names = load_names()
            if uid in names:
                old_name = names[uid]["name"]
                del names[uid]
                save_names(names)
                await send_safe(ADMIN_ID, f"✅ Прозвище «{old_name}» для {uid} удалено. Пользователь снова будет под реальным именем.")
            else:
                await send_safe(ADMIN_ID, f"⚠️ Для пользователя {uid} нет прозвища.\n\nПроверь список: /list_names")
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Неверный user_id.")

    elif text.startswith("/list_names"):
        names = load_names()
        if names:
            lines = ["📋 Прозвища:"]
            for uid, data in names.items():
                lines.append(f"  {uid} → {data['name']}")
            lines.append("")
            lines.append("Добавить: /setname user_id Прозвище")
            await send_safe(ADMIN_ID, "\n".join(lines))
        else:
            await send_safe(ADMIN_ID, "📋 Прозвищ нет.\n\nДобавь первое: /setname user_id Прозвище")

    elif text.startswith("/settime"):
        parts = text.split()
        if len(parts) < 2:
            await send_safe(ADMIN_ID, "❌ Использование: /settime ЧЧ:ММ\nПример: /settime 09:30\nТекущее время можно узнать через /status")
            return
        time_str = parts[1]
        if not re.match(r'^\d{1,2}:\d{2}$', time_str):
            await send_safe(ADMIN_ID, "❌ Неверный формат. Используй ЧЧ:ММ (например, 21:00 или 09:30)")
            return
        try:
            hour, minute = map(int, time_str.split(":"))
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                raise ValueError
        except ValueError:
            await send_safe(ADMIN_ID, "❌ Некорректное время. Часы: 0-23, минуты: 0-59.")
            return

        settings = load_settings()
        settings["send_hour"] = hour
        settings["send_minute"] = minute
        save_settings(settings)
        await send_safe(ADMIN_ID, f"✅ Время отправки сводки установлено на {hour:02d}:{minute:02d} МСК.\n\nИзменения вступят в силу в течение минуты.")

    elif text.startswith("/test"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        count = int(parts[2]) if len(parts) > 2 else 10

        if chat_id is None:
            chats = load_chats()
            if not chats:
                await send_safe(ADMIN_ID, "❌ Нет отслеживаемых чатов. Добавь через /add_chat.")
                return
            chat_id = chats[0]

        messages = daily_messages.get(chat_id, [])
        sample = messages[-min(count, len(messages)):]
        if not sample:
            await send_safe(ADMIN_ID, f"❌ Нет сообщений для чата {chat_id}.\n\nДождись, пока в чате накопятся сообщения, или проверь:\n1. Бот добавлен в чат\n2. Privacy Mode выключен в @BotFather")
            return

        chat_log = "\n".join(
            f"[{m['link']}] {m['author']}: {m['text']}" for m in sample
        )

        await send_safe(ADMIN_ID, f"🧪 Генерирую сводку для {chat_id} по {len(sample)} сообщениям...\n\nОбычно занимает 10-30 секунд.")
        result = generate_vestnik(chat_log)
        formatted = format_for_telegram(result)
        await send_safe(ADMIN_ID, formatted, parse_mode="MarkdownV2")

    elif text.startswith("/status"):
        settings = load_settings()
        lines = ["📊 Статистика:"]
        total = 0
        for cid, msgs in daily_messages.items():
            lines.append(f"  Чат {cid}: {len(msgs)} сообщений")
            total += len(msgs)
        if not daily_messages:
            lines.append("  Пусто. Сообщения ещё не накопились.")
        lines.append(f"\nВсего: {total} сообщений")
        lines.append(f"Время отправки: ежедневно в {settings['send_hour']:02d}:{settings['send_minute']:02d} МСК")
        lines.append(f"Модель: DeepSeek R1 (фолбэк: Llama 3.3)")
        lines.append(f"\nИзменить время: /settime ЧЧ:ММ")
        await send_safe(ADMIN_ID, "\n".join(lines))

    elif text.startswith("/reset"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        if chat_id:
            daily_messages[chat_id] = []
            await send_safe(ADMIN_ID, f"🗑️ Лог чата {chat_id} сброшен.")
        else:
            daily_messages.clear()
            await send_safe(ADMIN_ID, "🗑️ Все логи сброшены.")

    elif text.startswith("/help"):
        settings = load_settings()
        h = settings['send_hour']
        m = settings['send_minute']
        help_text = f"""
🛠 ПАМЯТКА АДМИНИСТРАТОРА ВЕСТНИКА

📋 УПРАВЛЕНИЕ ЧАТАМИ
  /add_chat -100XXXXXX
    Добавить чат в отслеживание. Бот видит ВСЕ топики.
    Пример: /add_chat -1002977868330

  /remove_chat -100XXXXXX
    Удалить чат из отслеживания.

  /list_chats
    Показать все отслеживаемые чаты.

🏷️ ПРОЗВИЩА ПОЛЬЗОВАТЕЛЕЙ
  /setname user_id Прозвище
    Назначить прозвище для сводок.
    Пример: /setname 123456789 Васян

  /removename user_id
    Удалить прозвище.

  /list_names
    Показать все прозвища.

  Как узнать user_id: добавь @getidsbot в чат.

⏰ НАСТРОЙКА ВРЕМЕНИ
  /settime ЧЧ:ММ
    Установить время ежедневной отправки.
    Пример: /settime 09:30
    Сейчас установлено: {h:02d}:{m:02d} МСК

🧪 ТЕСТИРОВАНИЕ
  /test -100XXXXXX 20
    Тестовая сводка по последним N сообщениям.
    Можно без аргументов: /test — 10 сообщений.

  /status
    Статистика: сообщения, время отправки, модель.

  /reset -100XXXXXX
    Сбросить лог для чата.
    Без аргументов: /reset — сбросить ВСЁ.

⚙️ ТЕХНИЧЕСКАЯ ИНФОРМАЦИЯ
  • Отправка сводки: каждый день в {h:02d}:{m:02d} МСК
  • Модель: DeepSeek R1 (фолбэк: Llama 3.3)
  • Картинки: Gemini 2.0 Flash
  • Топики: бот видит все ветки

🔧 ЕСЛИ НЕ РАБОТАЕТ
  1. Privacy Mode в @BotFather — ВЫКЛЮЧИТЬ
  2. Удалить бота из чата и добавить заново
  3. Дать права админа (чтение сообщений)
  4. Проверить переменные на Bothost
  5. Нажать «Пересобрать»
"""
        await send_safe(ADMIN_ID, help_text)


# ========== ПЛАНИРОВЩИК ==========
async def scheduler():
    while True:
        now = datetime.now()
        settings = load_settings()
        target = now.replace(
            hour=settings["send_hour"],
            minute=settings["send_minute"],
            second=0,
            microsecond=0
        )
        if now >= target:
            await send_daily_vestnik()
            target += timedelta(days=1)
        sleep_seconds = (target - datetime.now()).total_seconds()
        if sleep_seconds > 0:
            logger.info(f"Сон до {target.strftime('%H:%M')} МСК ({sleep_seconds:.0f} сек)")
            await asyncio.sleep(sleep_seconds)


# ========== ЗАПУСК ==========
async def main():
    logger.info("Вестник запущен!")

    chats = load_chats()
    for cid in chats:
        if cid not in daily_messages:
            daily_messages[cid] = []
    logger.info(f"Отслеживаем чаты: {chats}")

    nicks = load_names()
    logger.info(f"Загружено прозвищ: {len(nicks)}")

    settings = load_settings()
    logger.info(f"Время отправки: {settings['send_hour']:02d}:{settings['send_minute']:02d} МСК")

    asyncio.create_task(scheduler())

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
            logger.error(f"Ошибка в главном цикле: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
