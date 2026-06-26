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
                if random.randint(1, 100) <= 15:
                    return True
                return False
            if random.randint(1, 100) <= 70:
                return True
            return False

    auto = chat_settings.get("auto_respond", 0) if chat_settings else 0
    if auto:
        freq = chat_settings.get("respond_frequency", 10) if chat_settings else 10
        if random.randint(1, 100) <= freq:
            return True

    if _is_short_or_meaningless(text):
        return False

    if random.randint(1, 100) <= 5:
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


@router.message(Command("style"), IsGroupChat())
@router.message(Command("style"), IsPrivateChat())
async def cmd_style(message: Message, db):
    args = message.text or ""
    args = args.replace("/style", "", 1).strip().lower()

    if not args:
        current = await db.get_setting("photo_style") or "realistic"
        styles_list = "\n".join(f"  • {s}" for s in PHOTO_STYLES)
        await message.reply(
            f"🎨 <b>Стиль фото</b>\n\n"
            f"Текущий: <b>{current}</b>\n\n"
            f"Доступные:\n{styles_list}\n\n"
            f"Установить: /style anime\n"
            f"Сбросить: /style reset"
        )
        return

    if args == "reset":
        await db.set_setting("photo_style", "realistic")
        await message.reply("✅ Стиль сброшен на realistic")
        return

    if args in PHOTO_STYLES:
        old = await db.get_setting("photo_style") or "realistic"
        await db.set_setting("photo_style", args)
        await message.reply(
            f"✅ Стиль изменён!\n\n"
            f"Было: {old}\n"
            f"Стало: {args}\n\n"
            f"Теперь все фото будут в стиле «{args}»"
        )
    else:
        await message.reply(f"Стиль «{args}» не найден. Доступные: {', '.join(PHOTO_STYLES.keys())}")


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
        "  /style — стиль фото (anime, realistic, art...)\n"
        "  /dice — кинуть кубик 🎲\n"
        "  /coin — подбросить монетку 🪙\n"
        "  /8ball — магический шар 🎱\n"
        "  /time — текущее время 🕐\n\n"
        "Фичи:\n"
        "  • «закури переведи на английский [текст]» 🌐\n"
        "  • «закури напомни через час [текст]» ⏰\n"
        "  • «закури запомни что я люблю кофе» 🧠\n"
        "  • Голосовые — транскрибация + ответ 🎤\n\n"
        "Админам:\n"
        "  • /admin — панель управления"
    )
    await message.answer(help_text)


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

    if addressed and is_photo_request(text):
        await handle_photo_generation(message, text, ai_manager, bot, context_manager, db)
        return

    if addressed and is_photo_edit_request(text):
        if message.reply_to_message and message.reply_to_message.photo:
            await handle_photo_edit(message, text, ai_manager, bot, context_manager)
            return

    if addressed:
        from src.handlers.features import handle_translate, handle_reminder, handle_user_fact
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
        await handle_photo_generation(message, text, ai_manager, bot, context_manager, db)
        return

    if (
        is_photo_edit_request(text)
        and message.reply_to_message
        and message.reply_to_message.photo
    ):
        await handle_photo_edit(message, text, ai_manager, bot, context_manager)
        return

    from src.handlers.features import handle_translate, handle_reminder, handle_user_fact
    if await handle_translate(message, text, ai_manager, bot):
        return
    if await handle_reminder(message, text, bot):
        return
    if await handle_user_fact(message, text, db):
        return

    await _generate_and_send_response(message, db, ai_manager, context_manager, bot)


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

        status_msg = await message.reply(random.choice(THINKING_MESSAGES))

        system_prompt = await build_system_prompt(db, message.chat.id)

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

    try:
        enhanced = await ai_manager.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты ассистент который улучшает промпты для генерации изображений. "
                        "На вход получаешь короткий промпт на русском. "
                        "Улучши его: добавь детали, освещение, качество. "
                        f"Обязательно добавь стиль: {style_suffix}. "
                        "Переведи на английский. Не добавляй людей если не просят. "
                        "Отвечай ТОЛЬКО улучшенным промптом на английском, без объяснений."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
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
            await message.answer_photo(photo, caption=caption)
            generated += 1

        try:
            await status_msg.delete()
        except Exception:
            pass

        bot_name = "Закури"
        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username=bot_name,
            text=f"[Сгенерировал фото: {short_prompt}, стиль: {style}]",
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
            text=f"[Отредактировал фото: {prompt}]",
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
