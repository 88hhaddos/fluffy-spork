import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_SIZE = 50
SUMMARIZE_THRESHOLD = 50
MAX_SUMMARY_CHUNK = 200


class ContextManager:
    """Управление контекстом чата и памятью бота."""

    def __init__(self, db, ai_manager):
        self.db = db
        self.ai = ai_manager

    async def store_user_message(
        self,
        chat_id: int,
        user_id: int,
        username: str,
        first_name: str,
        text: str,
        is_forwarded: bool = False,
        forwarded_from: str = "",
        message_id: int = 0,
    ):
        await self.db.store_message(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            first_name=first_name,
            message_text=text,
            is_forwarded=is_forwarded,
            forwarded_from=forwarded_from,
            is_bot=False,
            message_id=message_id,
        )

    async def store_bot_message(
        self,
        chat_id: int,
        bot_username: str,
        text: str,
        message_id: int = 0,
    ):
        await self.db.store_message(
            chat_id=chat_id,
            user_id=0,
            username=bot_username,
            first_name="",
            message_text=text,
            is_forwarded=False,
            forwarded_from="",
            is_bot=True,
            message_id=message_id,
        )

    async def get_context_size(self, chat_id: int) -> int:
        settings = await self.db.get_chat_settings(chat_id)
        if settings and settings.get("context_size"):
            return settings["context_size"]
        global_size = await self.db.get_setting("global_context_size")
        return int(global_size) if global_size else DEFAULT_CONTEXT_SIZE

    async def build_messages_for_ai(
        self,
        chat_id: int,
        system_prompt: str,
        current_message: Optional[str] = None,
        current_username: Optional[str] = None,
    ) -> list[dict]:
        context_size = await self.get_context_size(chat_id)

        recent = await self.db.get_recent_messages(chat_id, context_size)
        events = await self.db.get_key_events(chat_id, limit=20)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]

        if events:
            events_text = "\n".join(
                f"• {e['event_text']}" for e in reversed(events)
            )
            messages.append({
                "role": "system",
                "content": f"Ключевые события из прошлых разговоров:\n{events_text}",
            })

        for msg in recent:
            if msg["is_bot_message"]:
                messages.append({
                    "role": "assistant",
                    "content": msg["message_text"],
                })
            else:
                if msg["is_forwarded"]:
                    fwd = msg["forwarded_from"] or "неизвестно"
                    content = f"[Переслано от {fwd}]: {msg['message_text']}"
                else:
                    name = msg["username"] or msg["first_name"] or "Кто-то"
                    content = f"{name}: {msg['message_text']}"
                messages.append({"role": "user", "content": content})

        if current_message and current_username:
            messages.append({
                "role": "user",
                "content": f"{current_username}: {current_message}",
            })

        return messages

    async def maybe_summarize(self, chat_id: int):
        """Суммаризует старые сообщения если их слишком много.
        
        Порог: context_size + SUMMARIZE_THRESHOLD.
        Если старых сообщений больше MAX_SUMMARY_CHUNK — суммаризует по частям.
        """
        context_size = await self.get_context_size(chat_id)
        msg_count = await self.db.get_message_count(chat_id)

        if msg_count <= context_size + SUMMARIZE_THRESHOLD:
            return

        old_messages = await self.db.get_old_messages(chat_id, context_size)
        if len(old_messages) < 10:
            return

        chunks = [old_messages[i:i + MAX_SUMMARY_CHUNK] for i in range(0, len(old_messages), MAX_SUMMARY_CHUNK)]

        for chunk in chunks:
            try:
                text_parts = []
                for m in chunk:
                    if m["is_forwarded"]:
                        fwd = m["forwarded_from"] or "неизвестно"
                        text_parts.append(f"[Переслано от {fwd}]: {m['message_text']}")
                    else:
                        name = m["username"] or m["first_name"] or "Кто-то"
                        role = "Закури" if m["is_bot_message"] else name
                        text_parts.append(f"{role}: {m['message_text']}")

                combined = "\n".join(text_parts)
                summary = await self.ai.summarize(combined)

                if summary:
                    await self.db.add_key_event(chat_id, summary)
                    ids = [m["id"] for m in chunk]
                    await self.db.delete_messages_by_ids(ids)
                    logger.info(f"Суммаризовано {len(chunk)} сообщений для chat {chat_id}")
            except Exception as e:
                logger.error(f"Ошибка суммаризации для chat {chat_id}: {e}")
                break

    async def load_large_context(self, chat_id: int, text: str) -> int:
        """Загрузка большого контекста (до 100k символов).
        Разбивает на части, суммаризует каждую, сохраняет как ключевые события.
        Возвращает количество созданных ключевых событий."""
        chunk_size = 8000
        chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
        count = 0

        for chunk in chunks:
            try:
                summary = await self.ai.summarize(chunk, max_tokens=300)
                if summary:
                    await self.db.add_key_event(chat_id, f"[Загружено админом] {summary}")
                    count += 1
            except Exception as e:
                logger.error(f"Ошибка при загрузке контекста: {e}")

        return count

    async def clear_context(self, chat_id: int):
        """Полная очистка контекста чата."""
        await self.db.clear_messages(chat_id)
        await self.db.clear_key_events(chat_id)

    async def clear_key_events(self, chat_id: int):
        await self.db.clear_key_events(chat_id)

    async def get_key_events_text(self, chat_id: int) -> str:
        events = await self.db.get_key_events(chat_id, limit=50)
        if not events:
            return "Ключевых событий нет."
        lines = []
        for e in reversed(events):
            lines.append(f"• {e['event_text']}")
        return "\n".join(lines)

    @staticmethod
    def extract_forwarded_info(message) -> tuple[bool, str]:
        """Проверяет, переслано ли сообщение, и возвращает (is_forwarded, forwarded_from)."""
        if message.forward_origin:
            origin = message.forward_origin
            forwarded_from = ""
            if hasattr(origin, "sender_user") and origin.sender_user:
                forwarded_from = (
                    origin.sender_user.username
                    or origin.sender_user.full_name
                    or "пользователь"
                )
            elif hasattr(origin, "sender_chat_name") and origin.sender_chat_name:
                forwarded_from = origin.sender_chat_name
            elif hasattr(origin, "chat") and origin.chat:
                forwarded_from = origin.chat.title or origin.chat.username or "чат"
            else:
                forwarded_from = "неизвестный источник"
            return True, forwarded_from
        return False, ""
