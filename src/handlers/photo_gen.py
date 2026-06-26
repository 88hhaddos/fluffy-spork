import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from aiogram.enums import ChatAction

from src.config import config
from src.filters import IsGroupChat, IsPrivateChat

logger = logging.getLogger(__name__)

router = Router(name="photo_gen")


@router.message(Command("gen"))
async def cmd_gen(message: Message, ai_manager, bot, context_manager):
    """Явная команда генерации фото: /gen <промпт>"""
    prompt = message.text or ""
    prompt = prompt.replace("/gen", "", 1).strip()

    if not prompt:
        await message.reply(
            "Нужен промпт! Например: /gen кот сидит на дереве"
        )
        return

    await _do_generate(message, prompt, ai_manager, bot, context_manager)


@router.message(Command("edit"))
async def cmd_edit(message: Message, ai_manager, bot, context_manager):
    """Явная команда редактирования фото: /edit <инструкции> (reply на фото)"""
    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.reply(
            "Отправьте /edit reply-ом на фото с инструкциями.\n"
            "Например: reply на фото + /edit сделай в стиле аниме"
        )
        return

    prompt = message.text or ""
    prompt = prompt.replace("/edit", "", 1).strip()

    if not prompt:
        await message.reply("Нужны инструкции! Например: /edit сделай чёрно-белым")
        return

    await _do_edit(message, prompt, ai_manager, bot, context_manager)


async def _do_generate(message: Message, prompt: str, ai_manager, bot, context_manager):
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)
        image_bytes = await ai_manager.generate_image(prompt)
        photo = BufferedInputFile(image_bytes, filename="zakuri_art.png")
        await message.answer_photo(photo, caption=f"Закури нарисовал: {prompt}")
        await context_manager.store_bot_message(
            chat_id=message.chat.id,
            bot_username="Закури",
            text=f"[Сгенерировал фото: {prompt}]",
            message_id=message.message_id,
        )
    except RuntimeError as e:
        logger.error(f"Image generation error: {e}")
        await message.reply(f"Закури не может рисовать сейчас: {e}")
    except Exception as e:
        logger.error(f"Image generation error: {e}", exc_info=True)
        await message.reply("Закури уронил кисточку. Попробуй ещё раз.")


async def _do_edit(message: Message, prompt: str, ai_manager, bot, context_manager):
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.UPLOAD_PHOTO)
        photo_obj = message.reply_to_message.photo[-1]
        file = await bot.get_file(photo_obj.file_id)
        downloaded = await bot.download_file(file.file_path)
        image_bytes = downloaded.read()
        edited_bytes = await ai_manager.edit_image(image_bytes, prompt)
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
        await message.reply(f"Закури не может редактировать сейчас: {e}")
    except Exception as e:
        logger.error(f"Image edit error: {e}", exc_info=True)
        await message.reply("Закури уронил кисточку при редактировании. Попробуй ещё раз.")
