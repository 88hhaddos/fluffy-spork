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
            return True

    auto = chat_settings.get("auto_respond", 0) if chat_settings else 0
    if auto:
        freq = chat_settings.get("respond_frequency", 10) if chat_settings else 10
        if random.randint(1, 100) <= freq:
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
            return prompt
    return ""


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
        "  • Reply на моё сообщение — отвечу\n"
        "  • «закури, нарисуй [промпт]» — сгенерирую фото\n\n"
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
        await handle_photo_generation(message, text, ai_manager, bot, context_manager)
        return

    if addressed and is_photo_edit_request(text):
        if message.reply_to_message and message.reply_to_message.photo:
            await handle_photo_edit(message, text, ai_manager, bot, context_manager)
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
        await handle_photo_generation(message, text, ai_manager, bot, context_manager)
        return

    if (
        is_photo_edit_request(text)
        and message.reply_to_message
        and message.reply_to_message.photo
    ):
        await handle_photo_edit(message, text, ai_manager, bot, context_manager)
        return

    await _generate_and_send_response(message, db, ai_manager, context_manager, bot)


async def _generate_and_send_response(
    message: Message,
    db,
    ai_manager,
    context_manager: ContextManager,
    bot,
):
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

        system_prompt = await build_system_prompt(db, message.chat.id)

        username = ""
        if message.from_user:
            username = message.from_user.username or message.from_user.first_name or "Кто-то"

        text = get_message_text(message)

        messages = await context_manager.build_messages_for_ai(
            chat_id=message.chat.id,
            system_prompt=system_prompt,
            current_message=text,
            current_username=username,
        )

        response = await ai_manager.chat_completion(
            messages=messages,
            temperature=0.7,
            max_tokens=800,
        )

        if not response or not response.strip():
            response = "Хм, Закури задумался. Спроси ещё раз."

        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        sent = await message.reply(response[:4096])

        bot_name = await db.get_setting("bot_name") or "Закури"
        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username=bot_name,
            text=response[:4096],
            message_id=sent.message_id,
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
):
    prompt = extract_photo_prompt(text)

    if not prompt:
        await message.reply(
            "Закури готов рисовать, но нужен промпт! Напиши что нарисовать.\n"
            "Например: «закури, нарисуй кота на дереве»"
        )
        return

    if not any(w in prompt.lower() for w in ["робот", "robot", "мех", "киборг", "android", "droid"]):
        prompt = prompt + ", realistic person or animal, natural style, no robots"

    status_msg = await message.reply("🎨 Закури достаёт кисточки...")
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

    try:
        image_bytes = await ai_manager.generate_image(prompt)

        await status_msg.delete()

        from aiogram.types import BufferedInputFile
        photo = BufferedInputFile(image_bytes, filename="zakuri_art.png")
        await message.answer_photo(photo, caption=f"Закури нарисовал: {prompt.split(',')[0]}")

        bot_name = "Закури"
        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username=bot_name,
            text=f"[Сгенерировал фото: {prompt.split(',')[0]}]",
            message_id=message.message_id,
        )

    except RuntimeError as e:
        logger.error(f"Image generation error: {e}")
        try:
            await status_msg.edit_text(f"Закури не может рисовать сейчас: {e}")
        except Exception:
            await message.reply(f"Закури не может рисовать сейчас: {e}")
    except Exception as e:
        logger.error(f"Image generation error: {e}", exc_info=True)
        try:
            await status_msg.edit_text("Закури уронил кисточку. Попробуй ещё раз.")
        except Exception:
            await message.reply("Закури уронил кисточку. Попробуй ещё раз.")


async def handle_photo_edit(
    message: Message,
    text: str,
    ai_manager,
    bot,
    context_manager: ContextManager,
):
    prompt = extract_edit_prompt(text)

    if not prompt:
        await message.reply(
            "Закури готов редактировать, но нужны инструкции! Напиши что изменить.\n"
            "Например: reply на фото + «закури, измени сделай в стиле аниме»"
        )
        return

    status_msg = await message.reply("🎨 Закури редактирует фото...")
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)

    try:
        photo = message.reply_to_message.photo[-1]
        file = await bot.get_file(photo.file_id)
        downloaded = await bot.download_file(file.file_path)
        image_bytes = downloaded.read()

        edited_bytes = await ai_manager.edit_image(image_bytes, prompt)

        await status_msg.delete()

        from aiogram.types import BufferedInputFile
        result_photo = BufferedInputFile(edited_bytes, filename="zakuri_edit.png")
        await message.answer_photo(result_photo, caption=f"Закури отредактировал: {prompt}")

        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username="Закури",
            text=f"[Отредактировал фото: {prompt}]",
            message_id=message.message_id,
        )

    except RuntimeError as e:
        logger.error(f"Image edit error: {e}")
        try:
            await status_msg.edit_text(f"Закури не может редактировать сейчас: {e}")
        except Exception:
            await message.reply(f"Закури не может редактировать сейчас: {e}")
    except Exception as e:
        logger.error(f"Image edit error: {e}", exc_info=True)
        try:
            await status_msg.edit_text("Закури уронил кисточку. Попробуй ещё раз.")
        except Exception:
            await message.reply("Закури уронил кисточку при редактировании. Попробуй ещё раз.")
