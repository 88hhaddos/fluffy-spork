from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery


class IsAdmin(BaseFilter):
    """Фильтр: пользователь является админом (из env или БД)."""

    async def __call__(self, event, db=None) -> bool:
        if not event.from_user:
            return False
        user_id = event.from_user.id

        from src.config import config

        if user_id in config.ADMIN_IDS:
            return True

        if db:
            return await db.is_admin(user_id)
        return False


class IsGroupChat(BaseFilter):
    """Фильтр: сообщение из группового чата."""

    async def __call__(self, event) -> bool:
        if isinstance(event, Message):
            return event.chat.type in ("group", "supergroup")
        return False


class IsPrivateChat(BaseFilter):
    """Фильтр: сообщение из личного чата."""

    async def __call__(self, event) -> bool:
        if isinstance(event, Message):
            return event.chat.type == "private"
        if isinstance(event, CallbackQuery):
            return event.message and event.message.chat.type == "private"
        return False
