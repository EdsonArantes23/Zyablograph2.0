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

SEND_HOUR = 21
SEND_MINUTE = 0

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
Никаких квадратных скобок вокруг ссылки, никаких пробелов между # и (.

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


# ========== ОЧИСТКА ВЫВОДА ==========
def clean_vestnik_output(text: str) -> str:
    """Убирает мусор, исправляет формат ссылок."""
    text = text.strip()

    # Удаляем подвал со спонсором
    if "⭐️ Станьте спонсором" in text:
        text = text.split("⭐️ Станьте спонсором")[0].strip()

    # Удаляем #вестник
    text = re.sub(r'#вестник\s*', '', text, flags=re.IGNORECASE).strip()

    return text


# ========== ПРЕОБРАЗОВАНИЕ ССЫЛОК ДЛЯ TELEGRAM MARKDOWN ==========
def format_for_telegram(text: str) -> str:
    """
    Преобразует # (https://t.me/...) в Markdown-ссылку [#](https://t.me/...)
    для отображения решётки со скрытой ссылкой в Telegram.
    """
    # Паттерн: # (https://t.me/c/ЧИСЛО/ЧИСЛО)
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

        # Преобразуем ссылки в Markdown-формат для красивого отображения в Telegram
        formatted = format_for_telegram(result)

        try:
            await bot.send_message(chat_id, formatted, parse_mode="MarkdownV2")
            logger.info(f"Сводка отправлена в {chat_id}!")
        except TelegramError as e:
            # Если Markdown ломается из-за спецсимволов — отправляем без форматирования
            logger.warning(f"Ошибка Markdown, отправляю без форматирования: {e}")
            try:
                await bot.send_message(chat_id, result)
                logger.info(f"Сводка отправлена (без Markdown) в {chat_id}!")
            except TelegramError as e2:
                logger.error(f"Ошибка отправки в {chat_id}: {e2}")

        daily_messages[chat_id] = []


# ========== АДМИНСКИЕ КОМАНДЫ ==========
async def process_admin_command(update):
    text = update.message.text or ""

    if text.startswith("/add_chat"):
        parts = text.split()
        if len(parts) < 2:
            await bot.send_message(ADMIN_ID, "❌ Использование: /add_chat -100XXXXXX")
            return
        try:
            new_chat = int(parts[1])
            chats = load_chats()
            if new_chat not in chats:
                chats.append(new_chat)
                save_chats(chats)
                await bot.send_message(ADMIN_ID, f"✅ Чат {new_chat} добавлен!")
            else:
                await bot.send_message(ADMIN_ID, f"⚠️ Чат {new_chat} уже в списке.")
        except ValueError:
            await bot.send_message(ADMIN_ID, "❌ Неверный ID чата.")

    elif text.startswith("/remove_chat"):
        parts = text.split()
        if len(parts) < 2:
            await bot.send_message(ADMIN_ID, "❌ Использование: /remove_chat -100XXXXXX")
            return
        try:
            chat = int(parts[1])
            chats = load_chats()
            if chat in chats:
                chats.remove(chat)
                save_chats(chats)
                await bot.send_message(ADMIN_ID, f"✅ Чат {chat} удалён.")
            else:
                await bot.send_message(ADMIN_ID, f"⚠️ Чат {chat} не найден.")
        except ValueError:
            await bot.send_message(ADMIN_ID, "❌ Неверный ID чата.")

    elif text.startswith("/list_chats"):
        chats = load_chats()
        if chats:
            msg = "📋 Отслеживаемые чаты:\n" + "\n".join(str(c) for c in chats)
        else:
            msg = "📋 Нет отслеживаемых чатов."
        await bot.send_message(ADMIN_ID, msg)

    elif text.startswith("/setname"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            await bot.send_message(ADMIN_ID, "❌ Использование: /setname user_id Прозвище")
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
            await bot.send_message(ADMIN_ID, f"✅ Для пользователя {uid} установлено прозвище: {nickname}")
        except ValueError:
            await bot.send_message(ADMIN_ID, "❌ Неверный user_id.")

    elif text.startswith("/removename"):
        parts = text.split()
        if len(parts) < 2:
            await bot.send_message(ADMIN_ID, "❌ Использование: /removename user_id")
            return
        try:
            uid = str(int(parts[1]))
            names = load_names()
            if uid in names:
                old_name = names[uid]["name"]
                del names[uid]
                save_names(names)
                await bot.send_message(ADMIN_ID, f"✅ Прозвище «{old_name}» для {uid} удалено.")
            else:
                await bot.send_message(ADMIN_ID, f"⚠️ Для {uid} нет прозвища.")
        except ValueError:
            await bot.send_message(ADMIN_ID, "❌ Неверный user_id.")

    elif text.startswith("/list_names"):
        names = load_names()
        if names:
            msg = "📋 Прозвища:\n"
            for uid, data in names.items():
                msg += f"  {uid} → {data['name']}\n"
        else:
            msg = "📋 Прозвищ нет."
        await bot.send_message(ADMIN_ID, msg)

    elif text.startswith("/test"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        count = int(parts[2]) if len(parts) > 2 else 10

        if chat_id is None:
            chats = load_chats()
            if not chats:
                await bot.send_message(ADMIN_ID, "❌ Нет отслеживаемых чатов.")
                return
            chat_id = chats[0]

        messages = daily_messages.get(chat_id, [])
        sample = messages[-min(count, len(messages)):]
        if not sample:
            await bot.send_message(ADMIN_ID, f"❌ Нет сообщений для чата {chat_id}.")
            return

        chat_log = "\n".join(
            f"[{m['link']}] {m['author']}: {m['text']}" for m in sample
        )

        await bot.send_message(ADMIN_ID, f"🧪 Тест для {chat_id} ({len(sample)} сообщений)...")
        result = generate_vestnik(chat_log)
        formatted = format_for_telegram(result)
        try:
            await bot.send_message(ADMIN_ID, formatted, parse_mode="MarkdownV2")
        except TelegramError:
            await bot.send_message(ADMIN_ID, result)

    elif text.startswith("/status"):
        msg_parts = ["📊 Статистика:"]
        for cid, msgs in daily_messages.items():
            msg_parts.append(f"  Чат {cid}: {len(msgs)} сообщений")
        if not daily_messages:
            msg_parts.append("  Пусто.")
        await bot.send_message(ADMIN_ID, "\n".join(msg_parts))

    elif text.startswith("/reset"):
        parts = text.split()
        chat_id = int(parts[1]) if len(parts) > 1 else None
        if chat_id:
            daily_messages[chat_id] = []
            await bot.send_message(ADMIN_ID, f"🗑️ Лог чата {chat_id} сброшен.")
        else:
            daily_messages.clear()
            await bot.send_message(ADMIN_ID, "🗑️ Все логи сброшены.")

    elif text.startswith("/help"):
        help_text = """
🛠 Админ-команды:

/add_chat -100XXXXXX — добавить чат
/remove_chat -100XXXXXX — удалить чат
/list_chats — список чатов
/setname user_id Прозвище — задать прозвище
/removename user_id — удалить прозвище
/list_names — список прозвищ
/test [chat_id] [кол-во] — тестовая сводка
/status — статистика
/reset [chat_id] — сбросить лог
/help — это сообщение
"""
        await bot.send_message(ADMIN_ID, help_text)


# ========== ПЛАНИРОВЩИК ==========
async def scheduler():
    while True:
        now = datetime.now()
        target = now.replace(hour=SEND_HOUR, minute=SEND_MINUTE, second=0, microsecond=0)
        if now >= target:
            await send_daily_vestnik()
            target += timedelta(days=1)
        sleep_seconds = (target - datetime.now()).total_seconds()
        if sleep_seconds > 0:
            logger.info(f"Сон до {target.strftime('%H:%M')} ({sleep_seconds:.0f} сек)")
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
