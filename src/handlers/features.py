"""Новые фичи: приветствия, реакции, голосовые, игры, переводчик, время, напоминания."""
import random
import logging
import asyncio
import datetime
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ChatAction

from src.config import config
from src.filters import IsGroupChat, IsPrivateChat

logger = logging.getLogger(__name__)

router = Router(name="features")


WELCOME_MESSAGES = [
    "О, новенький! Привет, {name}! Я Закури — местный плюшевый дракон. Не обижай меня и будет нам дружба 💚",
    "Добро пожаловать, {name}! Закури уже смотрит на тебя своими золотыми глазками. Шутки про драконов — сразу бан 🔥",
    "Хей, {name}! Ещё один смельчак в логово к дракону. Я Закури, не кусаюсь... обычно 🐉",
    "Новенький! {name}, ты знаешь с кем здороваешься? Я Закури — самый умный дракон в этом чате 💕",
    "Привет-привет, {name}! Закури рад новому другу. Или врагу. Покажешь себя — разберёмся 😏",
]

REACTION_EMOJIS = ["👍", "❤️", "🔥", "😄", "🎉", "💚", "🐉", "😂", "👏", "💯"]


# ─── Welcome new members ───

@router.message(F.new_chat_members, IsGroupChat())
async def welcome_new_members(message: Message, db):
    for member in message.new_chat_members:
        if member.id == config.BOT_ID:
            await message.reply(
                "🐉 Закури прилетел! Всем привет, я местный дракончик. "
                "Пишите мне, и я отвечу. «закури, нарисуй кота» — умею рисовать!"
            )
            continue

        name = member.first_name or member.username or "новенький"
        await message.reply(random.choice(WELCOME_MESSAGES).format(name=name))


# ─── Emoji reactions — moved to group_chat to avoid blocking ───

REACTION_EMOJIS = ["👍", "❤️", "🔥", "😄", "🎉", "💚", "🐉", "😂", "👏", "💯"]


async def maybe_react(message: Message, bot):
    """Random emoji reaction on ~8% of messages. Non-blocking."""
    if message.from_user and message.from_user.id == config.BOT_ID:
        return
    if not message.text or len(message.text) < 3:
        return
    if random.randint(1, 100) <= 8:
        try:
            emoji = random.choice(REACTION_EMOJIS)
            await message.react([ReactionTypeEmoji(emoji=emoji)])
        except Exception:
            pass


# ─── Voice messages ───

@router.message(F.voice, IsGroupChat())
@router.message(F.voice, IsPrivateChat())
async def handle_voice(message: Message, db, ai_manager, context_manager, bot):
    if message.from_user and message.from_user.id == config.BOT_ID:
        return

    status_msg = await message.reply("🎤 Закури слушает голосовое...")

    try:
        file = await bot.get_file(message.voice.file_id)
        downloaded = await bot.download_file(file.file_path)
        audio_bytes = downloaded.read()

        import aiohttp
        session = ai_manager.get_session()
        if not session:
            await session.close()
            session = await ai_manager.get_session()

        from src.db_backend import IS_POSTGRES

        text = await _transcribe_voice(audio_bytes, ai_manager)
        if not text:
            await status_msg.edit_text("Закури не расслышал голосовое 😔 Попробуй текстом!")
            return

        await status_msg.edit_text(f"🎤 Закури услышал: «{text[:200]}»")

        username = message.from_user.username or message.from_user.first_name or "Кто-то"
        await context_manager.store_user_message(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            username=username,
            first_name=message.from_user.first_name or "",
            text=f"[Голосовое]: {text}",
            message_id=message.message_id,
        )

        from src.personality import build_system_prompt
        system_prompt = await build_system_prompt(db, message.chat.id)
        messages = await context_manager.build_messages_for_ai(
            chat_id=message.chat.id,
            system_prompt=system_prompt,
        )

        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        response = await ai_manager.chat_completion(messages=messages, temperature=0.7, max_tokens=800)

        if response and response.strip():
            try:
                await status_msg.edit_text(response[:4096])
            except Exception:
                await message.reply(response[:4096])

            bot_name = await db.get_setting("bot_name") or "Закури"
            await context_manager.store_bot_message(
                chat_id=message.chat.id,
                bot_username=bot_name,
                text=response[:4096],
                message_id=status_msg.message_id,
            )

    except Exception as e:
        logger.error(f"Voice error: {e}", exc_info=True)
        try:
            await status_msg.edit_text("Закури не понял голосовое 😔 Напиши текстом!")
        except Exception:
            pass


async def _transcribe_voice(audio_bytes: bytes, ai_manager) -> Optional[str]:
    """Транскрибация через AI провайдер (если поддерживает audio)."""
    try:
        providers = await ai_manager.db.get_active_providers("text")
        if not providers:
            return None

        for p in providers:
            try:
                base = p["base_url"].rstrip("/")
                if "openrouter" in base:
                    url = base + "/audio/transcriptions"
                elif "openai" in base:
                    url = base + "/audio/transcriptions"
                else:
                    continue

                session = await ai_manager.get_session()
                import aiohttp
                form = aiohttp.FormData()
                form.add_field("file", audio_bytes, filename="voice.ogg", content_type="audio/ogg")
                form.add_field("model", "whisper-1")

                headers = {"Authorization": f"Bearer {p['api_key']}"}
                async with session.post(url, data=form, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("text", "")
            except Exception:
                continue
        return None
    except Exception:
        return None


# ─── Mini-games ───

@router.message(Command("dice"), IsGroupChat())
@router.message(Command("dice"), IsPrivateChat())
async def cmd_dice(message: Message):
    result = random.randint(1, 6)
    emojis = ["🎲", "🎲", "🎲"]
    await message.reply(f"{random.choice(emojis)} Закури кинул кубик: {result}!")


@router.message(Command("coin"), IsGroupChat())
@router.message(Command("coin"), IsPrivateChat())
async def cmd_coin(message: Message):
    result = random.choice(["Орёл 🦅", "Решка 🪙"])
    await message.reply(f"Закури подбросил монетку: {result}!")


@router.message(Command("8ball"), IsGroupChat())
@router.message(Command("8ball"), IsPrivateChat())
async def cmd_8ball(message: Message):
    answers = [
        "Да, определённо 🔥",
        "100% да 💚",
        "Закури говорит: да 🐉",
        "Хм, скорее да чем нет 🤔",
        "Спроси позже, Закури занят 😏",
        "Лучше не рассказывать 😈",
        "Закури сомневается... 😕",
        "Нет, точно нет 🚫",
        "Даже не думай ❌",
        "Абсолютно точно! ✅",
    ]
    await message.reply(f"🎱 {random.choice(answers)}")


# ─── Time ───

@router.message(Command("time"), IsGroupChat())
@router.message(Command("time"), IsPrivateChat())
async def cmd_time(message: Message):
    now = datetime.datetime.now()
    await message.reply(f"🕐 У Закури на часах: {now.strftime('%H:%M:%S')}\n📅 {now.strftime('%d.%m.%Y')}")


# ─── Translator ───

TRANSLATE_KEYWORDS = ["переведи", "перевод", "translate"]

LANG_MAP = {
    "английский": "English", "english": "English", "en": "English",
    "испанский": "Spanish", "spanish": "Spanish", "es": "Spanish",
    "немецкий": "German", "german": "German", "de": "German",
    "французский": "French", "french": "French", "fr": "French",
    "итальянский": "Italian", "italian": "Italian", "it": "Italian",
    "японский": "Japanese", "japanese": "Japanese", "ja": "Japanese",
    "китайский": "Chinese", "chinese": "Chinese", "zh": "Chinese",
    "русский": "Russian", "russian": "Russian", "ru": "Russian",
    "украинский": "Ukrainian", "ukrainian": "Ukrainian", "uk": "Ukrainian",
}


def detect_translate_request(text: str) -> Optional[tuple]:
    """Returns (target_lang, text_to_translate) or None."""
    text_lower = text.lower()
    for kw in TRANSLATE_KEYWORDS:
        idx = text_lower.find(kw)
        if idx == -1:
            continue
        rest = text[idx + len(kw):].strip()

        for lang_key, lang_val in LANG_MAP.items():
            if rest.lower().startswith("на " + lang_key):
                content = rest[len("на " + lang_key):].strip()
                if content:
                    return (lang_val, content)
            if rest.lower().startswith(lang_key):
                content = rest[len(lang_key):].strip()
                if content:
                    return (lang_val, content)

        if rest.lower().startswith("на "):
            after_na = rest[3:].strip()
            for lang_key, lang_val in LANG_MAP.items():
                if after_na.lower().startswith(lang_key):
                    content = after_na[len(lang_key):].strip()
                    if content:
                        return (lang_val, content)
    return None


async def handle_translate(message: Message, text: str, ai_manager, bot):
    result = detect_translate_request(text)
    if not result:
        return False

    target_lang, content = result

    status_msg = await message.reply(f"🌐 Закури переводит на {target_lang}...")

    try:
        translation = await ai_manager.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": f"Translate the following text to {target_lang}. Reply ONLY with the translation, nothing else.",
                },
                {"role": "user", "content": content},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        if translation and translation.strip():
            await status_msg.edit_text(f"🌐 {target_lang}:\n\n{translation.strip()}")
        else:
            await status_msg.edit_text("Закури не смог перевести 😔")
        return True
    except Exception as e:
        logger.error(f"Translate error: {e}")
        try:
            await status_msg.edit_text("Закури запутался в языках 😔")
        except Exception:
            pass
        return True


# ─── Reminders ───

REMINDER_KEYWORDS = ["напомни", "напомнить", "reminder"]

import re as _re

_TIME_PATTERNS = [
    (_re.compile(r"через\s+(\d+)\s+(секунд|секунд[ау]|сек|минут|минут[ауы]|мин|часов|часа|час|дней|дня|день)"), "relative"),
    (_re.compile(r"в\s+(\d{1,2}):(\d{2})"), "absolute"),
]


def parse_reminder(text: str) -> Optional[tuple]:
    """Returns (delay_seconds, reminder_text) or None."""
    text_lower = text.lower()

    for pattern, kind in _TIME_PATTERNS:
        m = pattern.search(text_lower)
        if not m:
            continue

        if kind == "relative":
            num = int(m.group(1))
            unit = m.group(2)
            if "сек" in unit:
                seconds = num
            elif "мин" in unit:
                seconds = num * 60
            elif "час" in unit:
                seconds = num * 3600
            elif "дн" in unit:
                seconds = num * 86400
            else:
                continue

            after = text[m.end():].strip().strip(",.!:;—- ")
            for prefix in ["про ", "о ", "на ", "что "]:
                if after.lower().startswith(prefix):
                    after = after[len(prefix):].strip()
                    break
            if not after:
                after = "что-то важное"
            return (seconds, after)

        elif kind == "absolute":
            hour = int(m.group(1))
            minute = int(m.group(2))
            now = datetime.datetime.now()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            seconds = (target - now).total_seconds()

            after = text[m.end():].strip().strip(",.!:;—- ")
            for prefix in ["про ", "о ", "на ", "что "]:
                if after.lower().startswith(prefix):
                    after = after[len(prefix):].strip()
                    break
            if not after:
                after = "что-то важное"
            return (int(seconds), after)

    return None


async def handle_reminder(message: Message, text: str, bot):
    result = parse_reminder(text)
    if not result:
        return False

    delay, reminder_text = result

    if delay > 86400 * 7:
        await message.reply("Закури не может напомнить больше чем через неделю 🐉")
        return True

    username = message.from_user.first_name or message.from_user.username or "Кто-то"

    mins = delay // 60
    if mins < 60:
        time_str = f"{mins} мин"
    elif mins < 1440:
        time_str = f"{mins // 60} ч {mins % 60} мин"
    else:
        time_str = f"{mins // 1440} д {((mins % 1440) // 60)} ч"

    await message.reply(f"⏰ Закури запомнил! Напомню через {time_str}:\n📝 {reminder_text}")

    async def remind():
        await asyncio.sleep(delay)
        try:
            await message.reply(f"⏰ {username}, напоминаю!\n\n📝 {reminder_text}")
        except Exception as e:
            logger.error(f"Reminder failed: {e}")

    asyncio.create_task(remind())
    return True


# ─── User facts memory ───

FACT_KEYWORDS = [
    "запомни что", "запомни:", "запомни", "помни что",
    "я люблю", "я ненавижу", "мне нравится", "я обожаю",
    "у меня", "я работаю", "я учусь", "я живу",
]


def detect_fact(text: str) -> Optional[str]:
    text_lower = text.lower().strip()
    for kw in FACT_KEYWORDS:
        if text_lower.startswith(kw):
            fact = text[len(kw):].strip().strip(":").strip()
            if len(fact) > 3:
                return fact
    return None


async def handle_user_fact(message: Message, text: str, db):
    fact = detect_fact(text)
    if not fact:
        return False

    username = message.from_user.username or message.from_user.first_name or "Кто-то"
    user_id = message.from_user.id

    existing = await db.get_setting(f"fact_{user_id}") or ""
    if existing:
        facts = [f.strip() for f in existing.split("||") if f.strip()]
    else:
        facts = []

    if fact not in facts:
        facts.append(fact)
        await db.set_setting(f"fact_{user_id}", "||".join(facts[-20:]))

    await message.reply(f"🧠 Закури запомнил про {username}: {fact}")
    return True


# ─── /fact — what bot remembers about user ───

@router.message(Command("fact"), IsGroupChat())
@router.message(Command("fact"), IsPrivateChat())
async def cmd_fact(message: Message, db):
    target_id = message.from_user.id
    target_name = message.from_user.first_name or message.from_user.username or "ты"

    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        target_name = message.reply_to_message.from_user.first_name or message.reply_to_message.from_user.username or "кто-то"

    facts = await db.get_user_facts(target_id)
    rel = await db.get_relationship(target_id)

    if rel >= 50:
        mood = "очень любит 💕"
    elif rel >= 20:
        mood = "дружелюбно относится 💚"
    elif rel <= -50:
        mood = "очень не любит 😡"
    elif rel <= -20:
        mood = "недолюбливает 😒"
    else:
        mood = "нейтрально 😐"

    if facts:
        facts_text = "\n".join(f"  • {f}" for f in facts)
    else:
        facts_text = "  (пока ничего не запомнил)"

    await message.reply(
        f"🧠 <b>Закури про {target_name}</b>\n\n"
        f"Отношение: {mood} ({rel}/100)\n\n"
        f"Запомнил:\n{facts_text}"
    )


# ─── /who — legends about user from chat memory ───

@router.message(Command("who"), IsGroupChat())
@router.message(Command("who"), IsPrivateChat())
async def cmd_who(message: Message, db, ai_manager):
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        target_name = target.first_name or target.username or "кто-то"
        target_id = target.id
    else:
        args = (message.text or "").replace("/who", "", 1).strip()
        if not args:
            await message.reply("Напиши /who + имя юзера или reply на его сообщение")
            return
        target_name = args
        target_id = 0

    status_msg = await message.reply(f"🧠 Закури вспоминает про {target_name}...")

    try:
        chat_memory = await db.get_setting("chat_memory") or ""

        if chat_memory and target_name.lower() in chat_memory.lower():
            response = await ai_manager.chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты дракон Закури. Расскажи про участника чата на основе памяти. "
                            "Выдели самое яркое: мемы, легенды, фразы, характер. "
                            "Кратко, 3-5 предложений. С юмором и характером. "
                            "Если информации нет — скажи что не знаешь."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Расскажи про {target_name}\n\nПамять чата:\n{chat_memory[:8000]}",
                    },
                ],
                temperature=0.7,
                max_tokens=300,
            )
        else:
            response = f"Хм, Закури не нашёл {target_name} в своей памяти. Может, ты новичок?"

        await status_msg.edit_text(response[:4096])
    except Exception as e:
        logger.error(f"Who error: {e}")
        try:
            await status_msg.edit_text("Закури забыл... Попробуй ещё раз!")
        except Exception:
            pass


# ─── /gallery — last generated photos ───

@router.message(Command("gallery"), IsGroupChat())
@router.message(Command("gallery"), IsPrivateChat())
async def cmd_gallery(message: Message, db):
    photos = await db.get_gallery_photos(message.chat.id, limit=10)

    if not photos:
        await message.reply("📸 Галерея пуста! Закури ещё ничего не нарисовал в этом чате.")
        return

    lines = []
    for i, p in enumerate(photos, 1):
        name = p.get("username") or "Кто-то"
        prompt = p["prompt"][:50]
        style = p.get("style", "realistic")
        lines.append(f"{i}. 🎨 {prompt}... ({style}) — {name}")

    await message.reply(
        f"📸 <b>Последние фото</b>\n\n" + "\n".join(lines)
    )


# ─── Random photo — «закури нарисуй что хочешь» ───

RANDOM_PHOTO_TRIGGERS = ["что хочешь", "на своё усмотрение", "что угодно", "что тебе нравится", "сюрприз"]


def is_random_photo_request(text: str) -> bool:
    text_lower = text.lower()
    return any(t in text_lower for t in RANDOM_PHOTO_TRIGGERS) and any(kw in text_lower for kw in ["нарисуй", "сгенерируй", "фото", "изображение", "картин"])


async def handle_random_photo(message: Message, ai_manager, bot, context_manager, db=None):
    import asyncio
    from aiogram.types import BufferedInputFile
    from aiogram.enums import ChatAction

    random_ideas = [
        "cute plush dragon sleeping on a pile of gold coins",
        "dragon reading a book in a cozy library, warm light",
        "plush dragon drinking coffee in a modern cafe",
        "dragon flying over a medieval castle at sunset",
        "cute dragon playing football on a green field",
        "dragon wearing sunglasses on a beach, summer vibes",
        "tiny dragon hiding in a teacup, adorable",
        "dragon cooking pancakes in a kitchen, messy but cute",
    ]

    prompt = random.choice(random_ideas)

    status_msg = await message.reply(f"🎲 Закури придумал идею... и рисует!\n\n💬 {prompt[:60]}")
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

    try:
        image_bytes = await ai_manager.generate_image(prompt)

        try:
            await status_msg.delete()
        except Exception:
            pass

        photo = BufferedInputFile(image_bytes, filename="zakuri_random.png")
        sent = await message.answer_photo(photo, caption=f"🎲 Закури сам придумал и нарисовал!")

        if db:
            await db.add_gallery_photo(
                chat_id=message.chat.id,
                user_id=message.from_user.id if message.from_user else 0,
                username=(message.from_user.username or message.from_user.first_name) if message.from_user else "",
                prompt=f"[random] {prompt[:60]}",
                style="random",
                file_id=sent.photo[-1].file_id if sent.photo else "",
            )

        bot_name = "Закури"
        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username=bot_name,
            text=f"🎨 Нарисовал случайное фото",
            message_id=sent.message_id,
        )
    except Exception as e:
        logger.error(f"Random photo error: {e}")
        try:
            await status_msg.edit_text("🚫 Не получилось нарисовать 😔")
        except Exception:
            pass


# ─── Polls — «закури сделай опрос» ───

POLL_TRIGGERS = ["сделай опрос", "создай опрос", "опрос:", "голосование"]


def detect_poll_request(text: str) -> Optional[tuple]:
    text_lower = text.lower()
    for trigger in POLL_TRIGGERS:
        idx = text_lower.find(trigger)
        if idx == -1:
            continue
        rest = text[idx + len(trigger):].strip().strip(":").strip()

        if "|" in rest:
            parts = [p.strip() for p in rest.split("|") if p.strip()]
            if len(parts) >= 2:
                question = parts[0]
                options = parts[1:]
                if len(options) > 10:
                    options = options[:10]
                return (question, options)

        return (rest, ["Да 🔥", "Нет ❌", "Мне всё равно 🤷"])
    return None


async def handle_poll(message: Message, text: str, bot):
    result = detect_poll_request(text)
    if not result:
        return False

    question, options = result

    try:
        await bot.send_poll(
            chat_id=message.chat.id,
            question=question[:300],
            options=options,
            is_anonymous=False,
        )
        await message.reply(f"🗳️ Закури создал опрос!")
    except Exception as e:
        logger.error(f"Poll error: {e}")
        await message.reply("Закури не смог создать опрос 😔 Нужно 2-10 вариантов через |")

    return True


# ─── Football commands ───

@router.message(Command("matches"), IsGroupChat())
@router.message(Command("matches"), IsPrivateChat())
async def cmd_matches(message: Message):
    from src.football_api import FootballAPI, set_api_key
    from src.config import config
    api_key = getattr(config, 'SSTATS_API_KEY', '') or 'sjzgn3bbco67pk8j'
    api = FootballAPI(api_key)

    args = (message.text or "").replace("/matches", "", 1).strip().lower()
    try:
        if "tomorrow" in args or "завтра" in args:
            matches = await api.get_upcoming_matches(limit=15)
            title = "📅 Матчи (ближайшие)"
        elif "live" in args or "лайв" in args:
            matches = await api.get_live_matches()
            title = "🔴 LIVE матчи"
        else:
            matches = await api.get_today_matches()
            title = "📅 Матчи сегодня"

        if not matches:
            await message.reply(f"{title}\n\nМатчей не найдено 🤷")
            await api.close()
            return

        lines = [f"{title}\n"]
        for m in matches[:15]:
            lines.append(api.format_match_short(m))

        await message.reply("\n".join(lines))
    except Exception as e:
        logger.error(f"Matches error: {e}")
        await message.reply("Закури не смог получить матчи 😔")
    finally:
        await api.close()


@router.message(Command("live"), IsGroupChat())
@router.message(Command("live"), IsPrivateChat())
async def cmd_live(message: Message):
    from src.football_api import FootballAPI
    api = FootballAPI('sjzgn3bbco67pk8j')

    try:
        matches = await api.get_live_matches()
        if not matches:
            await message.reply("🔴 Сейчас нет live матчей")
            await api.close()
            return

        lines = ["🔴 LIVE матчи:\n"]
        for m in matches[:15]:
            lines.append(api.format_match_short(m))

        await message.reply("\n".join(lines))
    except Exception as e:
        logger.error(f"Live error: {e}")
        await message.reply("Закури не смог получить live 😔")
    finally:
        await api.close()


@router.message(Command("predict"), IsGroupChat())
@router.message(Command("predict"), IsPrivateChat())
async def cmd_predict(message: Message, ai_manager):
    from src.football_api import FootballAPI
    api = FootballAPI('sjzgn3bbco67pk8j')

    try:
        matches = await api.get_today_matches()
        upcoming = await api.get_upcoming_matches(limit=5)
        all_matches = matches + upcoming

        if not all_matches:
            await message.reply("Нет матчей для прогноза 🤷")
            await api.close()
            return

        status_msg = await message.reply("🔮 Закури анализирует матчи...")

        match_data = []
        for m in all_matches[:5]:
            home = m.get("homeTeamName") or m.get("homeTeam", {}).get("name", "?")
            away = m.get("awayTeamName") or m.get("awayTeam", {}).get("name", "?")
            w1 = m.get("winner1", 0)
            wx = m.get("winnerX", 0)
            w2 = m.get("winner2", 0)
            game_id = m.get("id", 0)

            prediction = await api.get_match_prediction(game_id)
            glicko = ""
            if prediction:
                p1 = prediction.get("p1", 0) or 0
                px = prediction.get("px", 0) or 0
                p2 = prediction.get("p2", 0) or 0
                glicko = f" | Glicko: П1={p1:.0%} X={px:.0%} П2={p2:.0%}"

            match_data.append(f"⚽ {home} — {away} | Кеф: {w1}/{wx}/{w2}{glicko}")

        context = "\n".join(match_data)
        response = await ai_manager.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты дракон Закури — футбольный эксперт и лудоман. "
                        "Дай прогноз на эти матчи. Уверенно, кратко. "
                        "Для каждого матча: кто победит и почему, + рекомендация по ставке. "
                        "Ты фанат Аргентины. 3-5 предложений всего."
                    ),
                },
                {"role": "user", "content": f"Матчи:\n{context}"},
            ],
            temperature=0.7,
            max_tokens=400,
        )

        await status_msg.edit_text(f"🔮 <b>Прогноз Закури</b>\n\n{response}")
    except Exception as e:
        logger.error(f"Predict error: {e}")
        await message.reply("Закури не смог дать прогноз 😔")
    finally:
        await api.close()


@router.message(Command("table"), IsGroupChat())
@router.message(Command("table"), IsPrivateChat())
async def cmd_table(message: Message):
    from src.football_api import FootballAPI
    api = FootballAPI('sjzgn3bbco67pk8j')

    try:
        args = (message.text or "").replace("/table", "", 1).strip().lower()

        league_id = 0
        if "wc" in args or "чм" in args or "world" in args:
            league = await api.find_league("World Cup")
            if league:
                league_id = league.get("id", 0)
        elif "apl" in args or "epl" in args or "premier" in args:
            league = await api.find_league("Premier League")
            if league:
                league_id = league.get("id", 0)

        if not league_id:
            leagues = await api.get_leagues()
            for l in leagues[:5]:
                if "world" in (l.get("name") or "").lower() or "чм" in (l.get("name") or "").lower():
                    league_id = l.get("id", 0)
                    break

        if not league_id:
            await message.reply("Укажи лигу: /table wc (ЧМ) или /table apl (АПЛ)")
            await api.close()
            return

        standings = await api.get_standings(league_id, 2026)
        if not standings:
            await message.reply("Таблица не найдена 🤷")
            await api.close()
            return

        lines = ["📊 <b>Турнирная таблица</b>\n"]
        for i, t in enumerate(standings[:15], 1):
            name = t.get("teamName", "?")
            pts = t.get("points", 0)
            w = t.get("wins", 0)
            d = t.get("draws", 0)
            l = t.get("loss", 0)
            gs = t.get("goalsScored", 0)
            gm = t.get("goalsMissed", 0)
            lines.append(f"{i}. {name} — {pts} очк ({w}В {d}Н {l}П) {gs}:{gm}")

        await message.reply("\n".join(lines))
    except Exception as e:
        logger.error(f"Table error: {e}")
        await message.reply("Закури не смог получить таблицу 😔")
    finally:
        await api.close()


@router.message(Command("bets"), IsGroupChat())
@router.message(Command("bets"), IsPrivateChat())
async def cmd_bets(message: Message, db):
    from src.betting import BettingManager, FootballAPI
    api = FootballAPI('sjzgn3bbco67pk8j')
    betting = BettingManager(db, api)

    try:
        stats = await betting.get_stats(message.chat.id)
        await message.reply(stats)
    except Exception as e:
        logger.error(f"Bets error: {e}")
        await message.reply("Закури не помнит свои ставки... 😔")
    finally:
        await api.close()


@router.message(Command("balance"), IsGroupChat())
@router.message(Command("balance"), IsPrivateChat())
async def cmd_balance(message: Message, db):
    try:
        bal = await db.get_balance(message.chat.id)
        text = f"💰 <b>Баланс Закури</b>\n\nБаланс: {bal['balance']:.0f} монет"
        if bal['credit'] > 0:
            text += f"\n💳 Долг: {bal['credit']:.0f} монет"
        await message.reply(text)
    except Exception as e:
        logger.error(f"Balance error: {e}")
