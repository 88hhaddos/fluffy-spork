import random
import logging
import re

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.enums import ChatAction

from src.config import config
from src.filters import IsGroupChat, IsPrivateChat
from src.personality import build_system_prompt
from src.context_manager import ContextManager

logger = logging.getLogger(__name__)

router = Router(name="group_chat")

THINKING_MESSAGES = [
    "💭 Закури думает...",
    "🤔 М-м-м, дай-ка подумаю...",
    "🐉 Закури чесает чешуйку...",
    "💭 Хвостиком шевелит и думает...",
    "🧠 Закури напрягает мозги...",
    "✨ Закури придумывает ответ...",
    "💕 Закури думает о тебе...",
    "🤗 Закури готовит что-то милое...",
    "👀 Закури присматривается к вопросу...",
    "🔥 Закури разжигает мысли...",
    "💭 М-м... интересный вопрос...",
    "🐉 Крылышки шевелятся, Закури думает...",
]

PHOTO_MESSAGES = [
    "🎨 Закури достаёт кисточки...",
    "🖌️ Закури ищет краски...",
    "🎭 Закури вдохновляется...",
    "✨ Закури творит магию...",
    "🐉 Закури расправляет крылья для творчества...",
    "💕 Закури рисует с любовью...",
    "🎨 М-м-м, что бы такое нарисовать...",
    "🖌️ Закури макает кисть в огонь...",
]

PHOTO_KEYWORDS = [
    "нарисуй", "сгенерируй", "создай фото", "сделай картинку",
    "сгенерируй изображение", "сделай фото", "создай изображение",
    "сгенерируй картинку", "сгенерируй картину", "generate image",
    "сфотографируй", "сделай селфи", "дай фото", "как выглядит",
    "покажи как выглядит", "дай изображение", "покажи фото",
]

EDIT_KEYWORDS = [
    "измени", "отредактируй", "переделай", "модифицируй",
    "измени фото", "отредактируй фото",
]

MENTION_PATTERN = re.compile(r"@(\w+)", re.IGNORECASE)


def get_message_text(message: Message) -> str:
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if message.sticker:
        return f"[Стикер: {message.sticker.emoji or ''}]"
    if message.photo:
        return "[Фото]"
    if message.voice:
        return "[Голосовое сообщение]"
    if message.video:
        return "[Видео]"
    if message.document:
        return f"[Документ: {message.document.file_name or ''}]"
    if message.animation:
        return "[GIF]"
    return ""


DEFAULT_TRIGGERS = [
    "закури", "зак", "заку", "драко", "дракон", "дракончик",
    "дракоша", "драк", "закурий", "бот",
]


def _get_triggers(db) -> list[str]:
    """Возвращает список триггер-слов из БД или стандартный."""
    import asyncio
    try:
        custom = asyncio.get_event_loop().run_until_complete(
            db.get_setting("trigger_words")
        ) if not asyncio.get_event_loop().is_running() else None
    except Exception:
        custom = None
    return DEFAULT_TRIGGERS


async def _get_triggers_async(db) -> list[str]:
    custom = await db.get_setting("trigger_words")
    if custom:
        words = [w.strip().lower() for w in custom.split(",") if w.strip()]
        return words if words else DEFAULT_TRIGGERS
    return DEFAULT_TRIGGERS


SHORT_IGNORE_PATTERNS = [
    "ахах", "ахаха", "хах", "хаха", "хахаха", "лол", "lol", "кек", "kek",
    "ок", "окей", "да", "нет", "+", "спс", "спасибо", "норм", "пон",
    "агп", "понял", "окл", " Keck", "ор", "ору", "lmao", "rofl",
    "хз", "нзч", "жиза", "рили", "даа", "нее", "вообще", "крч",
]


def _is_short_or_meaningless(text: str) -> bool:
    text_lower = text.lower().strip()
    if len(text_lower) <= 4:
        return True
    for pattern in SHORT_IGNORE_PATTERNS:
        if text_lower == pattern or text_lower.startswith(pattern + " ") or text_lower == pattern + ".":
            return True
    if all(c in "хацтyuыq!?.," for c in text_lower):
        return True
    return False


def should_respond_in_group(message: Message, chat_settings: dict, triggers: list[str] = None) -> bool:
    text = (message.text or message.caption or "").lower()
    bot_username_lower = config.BOT_USERNAME.lower() if config.BOT_USERNAME else ""

    if f"@{bot_username_lower}" in text:
        return True

    if triggers is None:
        triggers = DEFAULT_TRIGGERS

    for trigger in triggers:
        if trigger in text:
            return True

    if message.reply_to_message:
        if message.reply_to_message.from_user and message.reply_to_message.from_user.id == config.BOT_ID:
            if _is_short_or_meaningless(text):
                if random.randint(1, 100) <= 20:
                    return True
                return False
            if random.randint(1, 100) <= 95:
                return True
            return False

    auto = chat_settings.get("auto_respond", 0) if chat_settings else 0
    if auto:
        freq = chat_settings.get("respond_frequency", 10) if chat_settings else 10
        if not _is_short_or_meaningless(text) and random.randint(1, 100) <= freq:
            return True

    return False


def is_photo_request(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in PHOTO_KEYWORDS)


def is_photo_edit_request(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in EDIT_KEYWORDS)


def extract_photo_prompt(text: str) -> str:
    text_lower = text.lower()
    for kw in PHOTO_KEYWORDS:
        idx = text_lower.find(kw)
        if idx != -1:
            prompt = text[idx + len(kw):].strip()
            prompt = prompt.lstrip(", .!?:;—- ")
            import re as _re
            prompt = _re.sub(r'\d+\s+вариант(а|ов)?', '', prompt, flags=_re.IGNORECASE).strip()
            prompt = _re.sub(r'\d+\s+шт', '', prompt, flags=_re.IGNORECASE).strip()
            return prompt
    return ""


def extract_photo_count(text: str) -> int:
    import re as _re
    m = _re.search(r'(\d+)\s+(?:вариант|шт)', text, _re.IGNORECASE)
    if m:
        n = int(m.group(1))
        return min(n, 4)
    return 1


PHOTO_STYLES = {
    "realistic": "realistic, photorealistic, high quality, 8k",
    "anime": "anime style, manga style, japanese animation",
    "cartoon": "cartoon style, colorful, fun",
    "art": "oil painting, artistic, detailed brush strokes",
    "digital": "digital art, concept art, trending on artstation",
    "watercolor": "watercolor painting, soft colors, artistic",
    "pixel": "pixel art, 8-bit, retro game style",
    "3d": "3d render, octane render, cinematic lighting",
    "dark": "dark fantasy, moody, dramatic lighting",
    "cute": "cute, kawaii, adorable, soft pastel colors",
}


def extract_edit_prompt(text: str) -> str:
    text_lower = text.lower()
    for kw in EDIT_KEYWORDS:
        idx = text_lower.find(kw)
        if idx != -1:
            prompt = text[idx + len(kw):].strip()
            prompt = prompt.lstrip(", .!?:;—- ")
            return prompt
    return ""


@router.message(CommandStart(), IsPrivateChat())
async def cmd_start_private(message: Message, db):
    name = await db.get_setting("bot_name") or "Дракончик Закури"
    await message.answer(
        f"Привет! Я {name} — плюшевый дракончик.\n\n"
        f"Можешь просто написать мне сюда, и мы поболтаем!\n"
        f"Ещё я умею рисовать: «нарисуй [промпт]»\n\n"
        f"Команды:\n"
        f"  /help — помощь\n"
        f"  /admin — панель управления (для админов)"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "🟢 Дракончик Закури\n\n"
        "В личке:\n"
        "  • Просто пиши мне — я отвечу!\n"
        "  • «нарисуй [промпт]» — сгенерирую фото\n"
        "  • Reply на фото + «измени [инструкции]» — отредактирую фото\n\n"
        "В группе:\n"
        "  • Напиши «закури» или @бот чтобы обратиться ко мне\n"
        "  • Reply на моё сообщение — отвечу (иногда)\n"
        "  • «закури, нарисуй [промпт]» — сгенерирую фото\n"
        "  • «закури, нарисуй 3 варианта кота» — несколько вариантов\n\n"
        "Команды:\n"
        "  /dice — кинуть кубик 🎲\n"
        "  /coin — подбросить монетку 🪙\n"
        "  /8ball — магический шар 🎱\n"
        "  /time — текущее время 🕐\n"
        "  /fact — что Закури запомнил про тебя\n"
        "  /who — легенда про юзера (reply на сообщение)\n"
        "  /gallery — последние сгенерированные фото 📸\n"
        "  /matches — матчи сегодня ⚽\n"
        "  /matches tomorrow — ближайшие матчи\n"
        "  /live — live матчи 🔴\n"
        "  /predict — прогноз Закури на матчи 🔮\n"
        "  /table wc — таблица ЧМ 📊\n"
        "  /results — недавние результаты 📋\n"
        "  /news — футбольные новости 📰\n"
        "  /bets — ставки Закури 🎰\n"
        "  /balance — баланс Закури 💰\n\n"
        "Админ-команды:\n"
        "  /say [текст] — написать от лица бота\n"
        "  /ban (reply) — забанить юзера\n"
        "  /unban (reply) — разбанить юзера\n"
        "  /warn (reply) — выдать предупреждение (3=бан)\n\n"
        "Фичи:\n"
        "  • «нарисуй что хочешь» — случайное фото 🎲\n"
        "  • «закури сделай опрос: вопрос|вариант1|вариант2» 🗳️\n"
        "  • «закури переведи на английский [текст]» 🌐\n"
        "  • «закури напомни через час [текст]» ⏰\n"
        "  • «закури запомни что я люблю кофе» 🧠\n"
        "  • Голосовые — транскрибация + ответ 🎤\n\n"
        "Админам:\n"
        "  • /admin — панель управления"
    )
    await message.answer(help_text)


@router.message(Command("say"), IsGroupChat())
async def cmd_say_in_group(message: Message, db, bot):
    from src.config import config

    if message.from_user.id not in config.ADMIN_IDS and not await db.is_admin(message.from_user.id):
        return

    text = (message.text or "").replace("/say", "", 1).strip()
    if not text:
        await message.reply("Напиши: /say [текст сообщения]")
        return

    bot_name = await db.get_setting("bot_name") or "Закури"

    try:
        await message.delete()
    except Exception:
        pass

    sent = await message.bot.send_message(message.chat.id, text[:4096])

    from src.context_manager import ContextManager
    await db.store_message(
        chat_id=message.chat.id,
        user_id=0,
        username=bot_name,
        first_name="",
        message_text=text[:4096],
        is_forwarded=False,
        forwarded_from="",
        is_bot=True,
        message_id=sent.message_id,
    )


@router.message(Command("ban"), IsGroupChat())
@router.message(Command("ban"), IsPrivateChat())
async def cmd_ban(message: Message, db):
    from src.config import config

    if message.from_user.id not in config.ADMIN_IDS and not await db.is_admin(message.from_user.id):
        return

    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
    else:
        args = (message.text or "").replace("/ban", "", 1).strip()
        if args:
            try:
                target_id = int(args.split(":")[0].strip())
                from aiogram.types import User
                target = User(id=target_id, is_bot=False, first_name=args)
            except Exception:
                pass

    if not target:
        await message.reply("Reply на сообщение юзера или /ban <ID>")
        return

    reason = ""
    args = (message.text or "").split(":", 1)
    if len(args) > 1:
        reason = args[1].strip()

    username = target.username or target.first_name or str(target.id)
    await db.ban_user(target.id, username, message.from_user.id, reason)

    try:
        await message.delete()
    except Exception:
        pass

    await message.reply(f"🚫 {username} забанен. Причина: {reason or 'не указана'}")


@router.message(Command("unban"), IsGroupChat())
@router.message(Command("unban"), IsPrivateChat())
async def cmd_unban(message: Message, db):
    from src.config import config

    if message.from_user.id not in config.ADMIN_IDS and not await db.is_admin(message.from_user.id):
        return

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        args = (message.text or "").replace("/unban", "", 1).strip()
        if args:
            try:
                target_id = int(args)
            except ValueError:
                pass

    if not target_id:
        await message.reply("Reply на сообщение юзера или /unban <ID>")
        return

    await db.unban_user(target_id)
    await message.reply(f"🔓 Юзер {target_id} разбанен")


@router.message(Command("warn"), IsGroupChat())
@router.message(Command("warn"), IsPrivateChat())
async def cmd_warn(message: Message, db):
    from src.config import config

    if message.from_user.id not in config.ADMIN_IDS and not await db.is_admin(message.from_user.id):
        return

    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
    else:
        args = (message.text or "").replace("/warn", "", 1).strip()
        if args:
            try:
                target_id = int(args.split(":")[0].strip())
                from aiogram.types import User
                target = User(id=target_id, is_bot=False, first_name=args)
            except Exception:
                pass

    if not target:
        await message.reply("Reply на сообщение юзера или /warn <ID>")
        return

    username = target.username or target.first_name or str(target.id)
    warnings = await db.add_warning(target.id, username)

    if warnings >= 3:
        await db.ban_user(target.id, username, reason="3 предупреждения")
        await message.reply(f"⚠️→🚫 {username}: 3/3 — автоматический бан!")
    else:
        await message.reply(f"⚠️ {username}: предупреждение {warnings}/3. При 3 — бан.")


@router.message(IsGroupChat())
async def handle_group_message(
    message: Message,
    db,
    ai_manager,
    context_manager: ContextManager,
    bot,
):
    if message.from_user and message.from_user.id == config.BOT_ID:
        return

    await db.ensure_chat_settings(message.chat.id)

    text = get_message_text(message)
    if not text:
        return

    is_fwd, fwd_from = ContextManager.extract_forwarded_info(message)

    username = ""
    first_name = ""
    if message.from_user:
        username = message.from_user.username or ""
        first_name = message.from_user.first_name or ""

    await context_manager.store_user_message(
        chat_id=message.chat.id,
        user_id=message.from_user.id if message.from_user else 0,
        username=username,
        first_name=first_name,
        text=text,
        is_forwarded=is_fwd,
        forwarded_from=fwd_from,
        message_id=message.message_id,
    )

    triggers = await _get_triggers_async(db)
    chat_settings = await db.get_chat_settings(message.chat.id)
    addressed = should_respond_in_group(message, chat_settings, triggers)

    from src.handlers.features import maybe_react
    await maybe_react(message, bot)

    if addressed:
        logger.info(f"Responding to {message.from_user.username or message.from_user.first_name}: {text[:50]}")

    banned = await db.is_banned(message.from_user.id) if message.from_user else False
    if banned:
        logger.info(f"Banned user {message.from_user.id} ignored")
        return

    if addressed and is_photo_request(text):
        from src.handlers.features import is_random_photo_request, handle_random_photo
        if is_random_photo_request(text):
            await handle_random_photo(message, ai_manager, bot, context_manager, db)
            return
        await handle_photo_generation(message, text, ai_manager, bot, context_manager, db)
        return

    if addressed and message.reply_to_message:
        reply_msg = message.reply_to_message
        if reply_msg.from_user and reply_msg.from_user.id == config.BOT_ID:
            reply_text = (reply_msg.text or reply_msg.caption or "").lower()
            user_text = text.lower().strip()

            AGREE_WORDS = ["давай", "да", "ок", "окей", "хочу", "го", "дава", "ладно", "попробуем", "согласен", "хорошо"]
            PHOTO_HINT_WORDS = ["нарисую", "нарисовать", "фото", "изображение", "картин", "сгенерир", "нарисовать тебе", "хочешь"]

            if any(w in user_text for w in AGREE_WORDS) and any(w in reply_text for w in PHOTO_HINT_WORDS):
                prompt = await _extract_photo_idea_from_bot_message(reply_msg.text or reply_msg.caption or "", ai_manager)
                if prompt:
                    await handle_photo_generation(message, prompt, ai_manager, bot, context_manager, db)
                    return

    if addressed and is_photo_edit_request(text):
        if message.photo:
            await handle_photo_edit_direct(message, text, ai_manager, bot, context_manager)
            return
        if message.reply_to_message and message.reply_to_message.photo:
            await handle_photo_edit(message, text, ai_manager, bot, context_manager)
            return

    if addressed:
        from src.handlers.features import handle_translate, handle_reminder, handle_user_fact, handle_poll
        if await handle_poll(message, text, bot):
            return
        if await handle_translate(message, text, ai_manager, bot):
            return
        if await handle_reminder(message, text, bot):
            return
        if await handle_user_fact(message, text, db):
            return

    if not addressed:
        return

    await _generate_and_send_response(message, db, ai_manager, context_manager, bot)


@router.message(IsPrivateChat())
async def handle_private_message(
    message: Message,
    db,
    ai_manager,
    context_manager: ContextManager,
    bot,
):
    if message.from_user and message.from_user.id == config.BOT_ID:
        return

    banned = await db.is_banned(message.from_user.id) if message.from_user else False
    if banned:
        await message.reply("🚫 Ты забанен и не можешь пользоваться ботом.")
        return

    text = get_message_text(message)
    if not text:
        return

    chat_id = message.chat.id

    is_fwd, fwd_from = ContextManager.extract_forwarded_info(message)

    username = message.from_user.username or "" if message.from_user else ""
    first_name = message.from_user.first_name or "" if message.from_user else ""

    await context_manager.store_user_message(
        chat_id=chat_id,
        user_id=message.from_user.id if message.from_user else 0,
        username=username,
        first_name=first_name,
        text=text,
        is_forwarded=is_fwd,
        forwarded_from=fwd_from,
        message_id=message.message_id,
    )

    if is_photo_request(text):
        from src.handlers.features import is_random_photo_request, handle_random_photo
        if is_random_photo_request(text):
            await handle_random_photo(message, ai_manager, bot, context_manager, db)
            return
        await handle_photo_generation(message, text, ai_manager, bot, context_manager, db)
        return

    if is_photo_edit_request(text):
        if message.photo:
            await handle_photo_edit_direct(message, text, ai_manager, bot, context_manager)
            return
        if message.reply_to_message and message.reply_to_message.photo:
            await handle_photo_edit(message, text, ai_manager, bot, context_manager)
            return

    from src.handlers.features import handle_translate, handle_reminder, handle_user_fact, handle_poll
    if await handle_poll(message, text, bot):
        return
    if await handle_translate(message, text, ai_manager, bot):
        return
    if await handle_reminder(message, text, bot):
        return
    if await handle_user_fact(message, text, db):
        return

    await _generate_and_send_response(message, db, ai_manager, context_manager, bot)


async def _extract_photo_idea_from_bot_message(bot_text: str, ai_manager) -> str:
    """Извлекает идею для фото из сообщения бота где он предложил нарисовать."""
    try:
        result = await ai_manager.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Из текста сообщения бота извлеки что он предлагал нарисовать. "
                        "Ответь ТОЛЬКО коротким промптом для генерации на русском, без объяснений. "
                        "Если ничего про рисунок нет — ответь пустой строкой."
                    ),
                },
                {"role": "user", "content": bot_text[:500]},
            ],
            temperature=0.1,
            max_tokens=50,
        )
        return result.strip() if result else ""
    except Exception:
        return ""


FOOTBALL_KEYWORDS = [
    "матч", "чм", "чемпионат мира", "world cup", "сборная", "плей офф", "плей-офф",
    "группа", "гол", "забил", "счёт", "счет", "побед", "ничья", "коэффициент",
    "ставка", "прогноз", "аргентин", "мексик", "бразил", "франц", "испан",
    "англ", "герман", "португал", "холанд", "месси", "мбапп", "роннал",
    "турнир", "финал", "полуфинал", "четвертьфинал", "1/8", "1/4",
    "бомбардир", "вратарь", "защитник", "нападающий", "тренер",
    "кто забил", "кто побед", "кто игра", "соперник", "результат",
    "таблица", "очки", "групповой", "этап", "стадия",
    "кальджич", "калайджич", "кайседо", "кайсед", "дивала",
    "футбол", "футбольн", "мяч", "пас", "удар", "пенальт",
]


def _is_football_question(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in FOOTBALL_KEYWORDS)


async def _get_football_context(text: str, db) -> str:
    """Получает ВСЕ данные ЧМ из SStats API для контекста бота."""
    if not _is_football_question(text):
        return ""

    try:
        from src.football_api import FootballAPI
        api = FootballAPI('sjzgn3bbco67pk8j')

        parts = []

        wc_matches = await api.get_matches_by_league(1, 2026)
        finished = [m for m in wc_matches if m.get("status") in (8, 9, 10, 17, 18)]
        upcoming = [m for m in wc_matches if m.get("status") in (1, 2)]
        live = [m for m in wc_matches if m.get("status") in (3, 4, 5, 6, 7, 11, 19)]

        # ── ВСЕ завершённые матчи ──
        if finished:
            all_results = []
            all_goals = []
            scorer_stats = {}

            for m in finished:
                home_name = (m.get("homeTeam") or {}).get("name", "?")
                away_name = (m.get("awayTeam") or {}).get("name", "?")
                sc_h = m.get("homeResult", 0)
                sc_a = m.get("awayResult", 0)
                date = (m.get("date") or "")[:10]
                all_results.append(f"  {home_name} {sc_h}:{sc_a} {away_name} ({date})")

                game_id = m.get("id")
                if game_id:
                    details = await api.get_match_details(game_id)
                    if details:
                        events = details.get("events", [])
                        for ev in events:
                            ev_type = (ev.get("type") or "").lower()
                            if "goal" in ev_type or "гол" in ev_type:
                                player = ev.get("player") or ev.get("text") or "?"
                                minute = ev.get("minute", "?")
                                all_goals.append(f"  {home_name}-{away_name}: {player} ({minute}')")

                                clean_name = player.split("(")[0].strip()
                                if clean_name and clean_name != "?":
                                    scorer_stats[clean_name] = scorer_stats.get(clean_name, 0) + 1

                        lineups = details.get("lineups", [])
                        if lineups:
                            for lineup in lineups[:2]:
                                team_name = (lineup.get("team") or {}).get("name", "?")
                                players = lineup.get("players", [])
                                for p in players:
                                    if p.get("assists", 0) and p.get("assists", 0) > 0:
                                        pass

            parts.append(f"### ВСЕ завершённые матчи ЧМ 2026 ({len(all_results)}):\n" + "\n".join(all_results))

            if all_goals:
                parts.append(f"### Все голы ЧМ 2026:\n" + "\n".join(all_goals[-50:]))

            if scorer_stats:
                top_scorers = sorted(scorer_stats.items(), key=lambda x: x[1], reverse=True)[:10]
                scorers_str = "\n".join(f"  {name}: {goals} гол(ов)" for name, goals in top_scorers)
                parts.append(f"### Топ бомбардиры ЧМ 2026:\n{scorers_str}")

        # ── Live ──
        if live:
            live_str = "\n".join(
                f"  🔴 {(m.get('homeTeam') or {}).get('name', '?')} {m.get('homeResult', 0)}:{m.get('awayResult', 0)} {(m.get('awayTeam') or {}).get('name', '?')} (идёт сейчас)"
                for m in live
            )
            parts.append(f"### Live матчи ЧМ:\n{live_str}")

        # ── Предстоящие ──
        if upcoming:
            upcoming_str = "\n".join(
                f"  {(m.get('homeTeam') or {}).get('name', '?')} — {(m.get('awayTeam') or {}).get('name', '?')} ({(m.get('date') or '')[:16]})"
                for m in upcoming[:15]
            )
            parts.append(f"### Предстоящие матчи ЧМ:\n{upcoming_str}")

        # ── Таблица ──
        standings = await api.get_standings(1, 2026)
        if standings:
            table_str = "\n".join(
                f"  {i+1}. {t.get('teamName', '?')} — {t.get('points', 0)} очк, {t.get('wins', 0)}В {t.get('draws', 0)}Н {t.get('loss', 0)}П, голы {t.get('goalsScored', 0)}-{t.get('goalsMissed', 0)}"
                for i, t in enumerate(standings[:15])
            )
            parts.append(f"### Турнирная таблица ЧМ 2026:\n{table_str}")

        # ── Ставки бота ──
        bal = await db.get_balance(0)
        pending = await db.get_pending_bets(0)
        recent_bets = await db.get_recent_bets(0, 10)
        bets_lines = [
            f"  Баланс: {bal['balance']:.0f} юаней",
            f"  Долг: {bal['credit']:.0f} юаней" if bal['credit'] > 0 else "",
            f"  Всего ставок: {bal['bets_count']}, Выиграно: {bal['wins_count']}",
            f"  Прибыль: {bal['total_won'] - bal['total_lost']:+.0f} юаней",
            f"  Ожидают результата: {len(pending)}",
        ]
        if recent_bets:
            import json as _json
            bets_lines.append("  Последние ставки:")
            for b in recent_bets[:5]:
                sels = _json.loads(b["selections"])
                first = sels[0] if sels else {}
                match_name = f"{first.get('home', '?')} — {first.get('away', '?')}"
                if len(sels) > 1:
                    match_name += f" +{len(sels)-1}"
                status_emoji = "✅" if b["status"] == "won" else ("❌" if b["status"] == "lost" else "⏳")
                bets_lines.append(f"    {status_emoji} {match_name}: {first.get('pick', '?')} @ {b['odds']} — {b['stake']:.0f} юаней → {b['status']}")

        parts.append(f"### Мои ставки и баланс:\n" + "\n".join(b for b in bets_lines if b))

        # ── Новости ──
        try:
            from src.news import get_football_news
            posts = await get_football_news("footballearn")
            if posts:
                news_text = " ".join(posts[:2])[:500]
                parts.append(f"### Последние новости ЧМ (из TG канала):\n{news_text}")
        except Exception:
            pass

        await api.close()

        if parts:
            result = "\n\n".join(parts)
            if len(result) > 12000:
                result = result[:12000] + "\n\n[... данные обрезаны ...]"
            return result
        return ""
    except Exception as e:
        logger.error(f"Football context error: {e}")
        return ""


async def _generate_and_send_response(
    message: Message,
    db,
    ai_manager,
    context_manager: ContextManager,
    bot,
):
    import asyncio

    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        user_id = message.from_user.id if message.from_user else 0
        username = (message.from_user.username or message.from_user.first_name) if message.from_user else ""

        text_lower = (message.text or message.caption or "").lower()

        INSULT_WORDS = [
            "рот шатал", "пошёл нахуй", "пошел нахуй", "иди нахуй", "нахуй", "пидор",
            "мразь", "тварь", "сволочь", "урод", "дурак", "дебил", "даун", "тупой",
            "ненавижу", "ненавижу тебя", "ты лох", "лох", "чмо", "шлюха", "шалава",
            "закури говно", "ты говно", "ты мусор", "ублюдок", "соси", "сосать",
            "заткнись", "захлопни", "душила", "терпила", "ты терпила",
        ]
        COMPLIMENT_WORDS = [
            "красавчик", "молодец", "умница", "хороший", "лучший", "закури лучший",
            "люблю тебя", "обожаю", "ты крутой", "закури красавчик", "закури молодец",
            "спасибо закури", "спасибо", "закури ты лучший", "добрый", "милый",
            "закури милый", "закури добрый", "классный", "закури классный",
        ]

        if user_id:
            insult_detected = any(w in text_lower for w in INSULT_WORDS)
            compliment_detected = any(w in text_lower for w in COMPLIMENT_WORDS)

            if insult_detected:
                new_rel = await db.adjust_relationship(user_id, username, -15)
                logger.info(f"Relationship {username}: -15 → {new_rel}/100 (insult)")
            elif compliment_detected:
                new_rel = await db.adjust_relationship(user_id, username, +10)
                logger.info(f"Relationship {username}: +10 → {new_rel}/100 (compliment)")

        status_msg = await message.reply(random.choice(THINKING_MESSAGES))

        msg_text = message.text or message.caption or ""
        football_context = await _get_football_context(msg_text, db)
        if football_context:
            logger.info(f"Football context loaded for: {msg_text[:50]}")

        system_prompt = await build_system_prompt(
            db, message.chat.id,
            user_id=user_id,
            username=username,
        )

        if football_context:
            system_prompt += f"\n\n## Данные ЧМ 2026 (реальные данные)\n{football_context}"

        username = ""
        if message.from_user:
            username = message.from_user.username or message.from_user.first_name or "Кто-то"

        text = get_message_text(message)

        messages = await context_manager.build_messages_for_ai(
            chat_id=message.chat.id,
            system_prompt=system_prompt,
        )

        async def keep_typing():
            while True:
                try:
                    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
                except Exception:
                    pass
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())

        try:
            response = await ai_manager.chat_completion(
                messages=messages,
                temperature=0.7,
                max_tokens=800,
            )
        finally:
            typing_task.cancel()

        if not response or not response.strip():
            response = "Хм, Закури задумался. Спроси ещё раз."

        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        try:
            await status_msg.edit_text(response[:4096])
        except Exception:
            sent = await message.reply(response[:4096])
            status_msg = sent

        bot_name = await db.get_setting("bot_name") or "Закури"
        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username=bot_name,
            text=response[:4096],
            message_id=status_msg.message_id,
        )

        await context_manager.maybe_summarize(message.chat.id)

    except RuntimeError as e:
        logger.error(f"AI error: {e}")
        await message.reply(f"Закури не может ответить: {str(e)[:200]}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        await message.reply("Закури отвлёкся. Попробуй ещё раз.")


async def handle_photo_generation(
    message: Message,
    text: str,
    ai_manager,
    bot,
    context_manager: ContextManager,
    db=None,
):
    import asyncio

    prompt = extract_photo_prompt(text)
    count = extract_photo_count(text)

    if not prompt:
        await message.reply(
            "Закури готов рисовать, но нужен промпт! Напиши что нарисовать.\n"
            "Например: «закури, нарисуй кота на дереве»"
        )
        return

    short_prompt = prompt
    style = "realistic"
    if db:
        style = await db.get_setting("photo_style") or "realistic"
    style_suffix = PHOTO_STYLES.get(style, PHOTO_STYLES["realistic"])

    requester = message.from_user.first_name or message.from_user.username or "Кто-то"
    requester_id = message.from_user.id

    status_msg = await message.reply(random.choice(PHOTO_MESSAGES))
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

    async def update_status(text: str):
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    count_text = f" ({count} варианта)" if count > 1 else ""
    await asyncio.sleep(1)
    await update_status(f"🧠 Закури продумывает детали{count_text}...\n\n💬 Промпт: {short_prompt}\n🎨 Стиль: {style}")

    if db:
        is_banned = await db.is_banned(requester_id)
        if is_banned:
            await update_status("🚫 Ты забанен и не можешь просить фото!")
            return

        warnings = await db.get_warnings(requester_id)
        if warnings >= 3:
            await db.ban_user(requester_id, requester, reason="3 предупреждения за неадекватные фото-запросы")
            await update_status("🚫 3 предупреждения! Ты забанен!")
            return

    try:
        is_appropriate = await ai_manager.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Оцени запрос на генерацию изображения. "
                        "Ответь ТОЛЬКО 'OK' если запрос адекватный, "
                        "или 'BAD: причина' если неадекватный "
                        "(насилие, незаконное, и т.д.)."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=50,
        )

        if is_appropriate and is_appropriate.strip().startswith("BAD"):
            if db:
                w = await db.add_warning(requester_id, requester)
                reason = is_appropriate.split(":", 1)[1].strip() if ":" in is_appropriate else "неадекватный запрос"
                await update_status(f"⚠️ Предупреждение {w}/3!\n\nПричина: {reason}")
            else:
                await update_status("⚠️ Закури отказался рисовать это!")
            return
    except Exception:
        pass

    try:
        custom_instructions = ""
        if db:
            custom_instructions = await db.get_setting("custom_instructions") or ""

        system_content = (
            "Ты ассистент который улучшает промпты для генерации изображений. "
            "На вход получаешь короткий промпт на русском от конкретного пользователя. "
            "Улучши его: добавь детали, освещение, качество. "
            f"Обязательно добавь стиль: {style_suffix}. "
            "Переведи на английский. Не добавляй людей если не просят. "
            "Учитывай контекст: кто просит и для кого/чего. "
            "Отвечай ТОЛЬКО улучшенным промптом на английском, без объяснений."
        )

        if custom_instructions:
            system_content += f"\n\nДОПОЛНИТЕЛЬНЫЕ ИНСТРУКЦИИ ОТ АДМИНА (ОБЯЗАТЕЛЬНЫ):\n{custom_instructions}"

        photo_custom = ""
        if db:
            photo_custom = await db.get_setting("photo_custom_prompt") or ""
        if photo_custom:
            system_content += f"\n\nКАСТОМНЫЙ ФОТО-ПРОМПТ (ДОБАВЛЯТЬ ВСЕГДА): {photo_custom}"

        enhanced = await ai_manager.chat_completion(
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": f"Пользователь {requester} просит: {prompt}"},
            ],
            temperature=0.3,
            max_tokens=200,
        )

        if enhanced and len(enhanced) > 10:
            prompt = enhanced.strip()
        else:
            prompt = prompt + ", " + style_suffix

    except Exception:
        prompt = prompt + ", " + style_suffix

    await update_status(f"🖌️ Закури рисует{count_text}...\n\n💬 Промпт: {short_prompt}\n🎨 Стиль: {style}")
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

    try:
        from aiogram.types import BufferedInputFile

        generated = 0
        for i in range(count):
            if i > 0:
                await asyncio.sleep(1)
                await update_status(f"🖌️ Закури рисует вариант {i+1}/{count}...\n\n💬 Промпт: {short_prompt}")
                await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

            image_bytes = await ai_manager.generate_image(prompt)
            caption = f"🎨 Закури нарисовал: {short_prompt}"
            if count > 1:
                caption += f" (вариант {i+1}/{count})"
            photo = BufferedInputFile(image_bytes, filename=f"zakuri_art_{i+1}.png")

            sent_photo = None
            for attempt in range(3):
                try:
                    sent_photo = await message.answer_photo(photo, caption=caption)
                    break
                except Exception as send_err:
                    logger.warning(f"Photo send attempt {attempt+1} failed: {send_err}")
                    if attempt < 2:
                        await asyncio.sleep(2)
                    else:
                        raise

            if not sent_photo:
                raise RuntimeError("Не удалось отправить фото после 3 попыток")

            generated += 1

            if db:
                file_id = sent_photo.photo[-1].file_id if sent_photo.photo else ""
                await db.add_gallery_photo(
                    chat_id=message.chat.id,
                    user_id=requester_id,
                    username=requester,
                    prompt=short_prompt,
                    style=style,
                    file_id=file_id,
                )

        try:
            await status_msg.delete()
        except Exception:
            pass

        bot_name = "Закури"
        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username=bot_name,
            text=f"🎨 Нарисовал: {short_prompt}",
            message_id=message.message_id,
        )

    except RuntimeError as e:
        logger.error(f"Image generation error: {e}")
        await update_status(
            f"🚫 Изображение не создано\n\n"
            f"💬 Ответ модели:\n{str(e)[:300]}"
        )
    except Exception as e:
        logger.error(f"Image generation error: {e}", exc_info=True)
        await update_status(
            f"🚫 Изображение не создано\n\n"
            f"💬 Закури уронил кисточку. Попробуй ещё раз."
        )


async def handle_photo_edit_direct(
    message: Message,
    text: str,
    ai_manager,
    bot,
    context_manager: ContextManager,
):
    """Редактирование фото которое юзер прислал напрямую (с подписью)."""
    import asyncio

    prompt = extract_edit_prompt(text)
    if not prompt:
        prompt = text

    if not prompt:
        await message.reply("Напиши что изменить в фото!")
        return

    status_msg = await message.reply(random.choice(PHOTO_MESSAGES))
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

    async def update_status(t: str):
        try:
            await status_msg.edit_text(t)
        except Exception:
            pass

    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        downloaded = await bot.download_file(file.file_path)
        image_bytes = downloaded.read()

        await update_status(f"🖌️ Закури редактирует твоё фото...\n\n💬 Инструкции: {prompt}")
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

        edited_bytes = await ai_manager.edit_image(image_bytes, prompt)

        from aiogram.types import BufferedInputFile
        result_photo = BufferedInputFile(edited_bytes, filename="zakuri_edit.png")
        await message.answer_photo(result_photo, caption=f"🎨 Закури отредактировал: {prompt}")

        await update_status(f"✅ Готово!\n\n💬 Инструкции: {prompt}")

        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username="Закури",
            text="🎨 Отредактировал фото",
            message_id=message.message_id,
        )

    except RuntimeError as e:
        logger.error(f"Image edit direct error: {e}")
        await update_status(f"🚫 Редактирование не удалось\n\n💬 {str(e)[:300]}")
    except Exception as e:
        logger.error(f"Image edit direct error: {e}", exc_info=True)
        await update_status("🚫 Закури уронил кисточку. Попробуй ещё раз.")


async def handle_photo_edit(
    message: Message,
    text: str,
    ai_manager,
    bot,
    context_manager: ContextManager,
):
    import asyncio

    prompt = extract_edit_prompt(text)

    if not prompt:
        await message.reply(
            "Закури готов редактировать, но нужны инструкции! Напиши что изменить.\n"
            "Например: reply на фото + «закури, измени сделай в стиле аниме»"
        )
        return

    status_msg = await message.reply(random.choice(PHOTO_MESSAGES))
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

    async def update_status(text: str):
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass

    try:
        photo = message.reply_to_message.photo[-1]
        file = await bot.get_file(photo.file_id)
        downloaded = await bot.download_file(file.file_path)
        image_bytes = downloaded.read()

        await update_status(f"🖌️ Закури перерисовывает...\n\n💬 Инструкции: {prompt}")
        await asyncio.sleep(1)
        await update_status(f"🎨 Закури дорабатывает детали...\n\n💬 Инструкции: {prompt}")
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

        edited_bytes = await ai_manager.edit_image(image_bytes, prompt)

        from aiogram.types import BufferedInputFile
        result_photo = BufferedInputFile(edited_bytes, filename="zakuri_edit.png")
        await message.answer_photo(result_photo, caption=f"🎨 Закури отредактировал: {prompt}")

        await update_status(f"✅ Готово!\n\n💬 Инструкции: {prompt}")

        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username="Закури",
            text=f"🎨 Отредактировал фото",
            message_id=message.message_id,
        )

    except RuntimeError as e:
        logger.error(f"Image edit error: {e}")
        await update_status(
            f"🚫 Редактирование не удалось\n\n"
            f"💬 Ответ модели:\n{str(e)[:300]}"
        )
    except Exception as e:
        logger.error(f"Image edit error: {e}", exc_info=True)
        await update_status(
            f"🚫 Редактирование не удалось\n\n"
            f"💬 Закури уронил кисточку. Попробуй ещё раз."
        )
