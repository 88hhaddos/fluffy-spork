"""
Data layer for the Zakuri dragon bot.

Backend selection is delegated to ``db_backend.py``:
- If ``DATABASE_URL`` env var is set (Postgres URL) → Postgres (Railway).
- Otherwise → SQLite at ``$DB_PATH`` (default ``./bot.db``).
"""
import logging
from typing import Optional

from src.db_backend import (
    DB_PATH,
    IS_POSTGRES,
    AsyncConn,
    connect as _connect,
)

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    username TEXT
);

CREATE TABLE IF NOT EXISTS ai_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    model TEXT NOT NULL,
    provider_type TEXT NOT NULL DEFAULT 'text',
    priority INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    message_text TEXT,
    is_forwarded INTEGER DEFAULT 0,
    forwarded_from TEXT,
    is_bot_message INTEGER DEFAULT 0,
    message_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS key_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    event_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER PRIMARY KEY,
    auto_respond INTEGER DEFAULT 0,
    respond_frequency INTEGER DEFAULT 10,
    context_size INTEGER DEFAULT 50,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_bans (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    banned_by INTEGER,
    reason TEXT,
    warnings INTEGER DEFAULT 0,
    banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_relationships (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    relationship INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_key_events_chat_id ON key_events(chat_id);
"""


class Database:
    def __init__(self, db_path: str = "bot.db"):
        self.db_path = db_path
        self.conn: Optional[AsyncConn] = None

    async def init(self):
        self.conn = await _connect()
        await self.conn.executescript(SCHEMA)

        defaults = {
            "base_personality": "",
            "bot_name": "Дракончик Закури",
            "topic": "",
            "custom_instructions": "",
            "chat_memory": "",
            "trigger_words": "",
            "anger_level": "30",
            "photo_style": "realistic",
            "photo_custom_prompt": "",
            "global_context_size": "50",
            "auto_respond_frequency": "10",
            "max_context_tokens": "8000",
        }
        for key, value in defaults.items():
            await self.conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        await self.conn.commit()
        backend = "PostgreSQL" if IS_POSTGRES else "SQLite"
        logger.info(f"База данных инициализирована ({backend})")

    async def close(self):
        if self.conn:
            await self.conn.close()

    # ─── Settings ───

    async def get_setting(self, key: str) -> Optional[str]:
        cur = await self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None

    async def set_setting(self, key: str, value: str):
        await self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self.conn.commit()

    async def get_all_settings(self) -> dict[str, str]:
        cur = await self.conn.execute("SELECT key, value FROM settings")
        rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    # ─── Admins ───

    async def add_admin(self, user_id: int, username: str = ""):
        await self.conn.execute(
            "INSERT OR REPLACE INTO admins (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        await self.conn.commit()

    async def remove_admin(self, user_id: int):
        await self.conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def is_admin(self, user_id: int) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return await cur.fetchone() is not None

    async def get_admins(self) -> list[dict]:
        cur = await self.conn.execute("SELECT user_id, username FROM admins")
        rows = await cur.fetchall()
        return [{"user_id": row[0], "username": row[1] or ""} for row in rows]

    # ─── AI Providers ───

    async def add_provider(
        self, name: str, base_url: str, api_key: str, model: str,
        provider_type: str = "text", priority: int = 0,
    ) -> int:
        return await self.conn.insert_returning_id(
            "INSERT INTO ai_providers (name, base_url, api_key, model, provider_type, priority) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, base_url, api_key, model, provider_type, priority),
        )

    async def update_provider(self, provider_id: int, **kwargs):
        sets = []
        values = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            values.append(v)
        values.append(provider_id)
        await self.conn.execute(
            f"UPDATE ai_providers SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        await self.conn.commit()

    async def delete_provider(self, provider_id: int):
        await self.conn.execute("DELETE FROM ai_providers WHERE id = ?", (provider_id,))
        await self.conn.commit()

    async def get_providers(self, provider_type: Optional[str] = None) -> list[dict]:
        if provider_type:
            q = "SELECT * FROM ai_providers WHERE provider_type = ? ORDER BY priority ASC"
            params = (provider_type,)
        else:
            q = "SELECT * FROM ai_providers ORDER BY provider_type, priority ASC"
            params = ()
        cur = await self.conn.execute(q, params)
        rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def get_active_providers(self, provider_type: str) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM ai_providers WHERE provider_type = ? AND is_active = 1 ORDER BY priority ASC",
            (provider_type,),
        )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def toggle_provider(self, provider_id: int):
        await self.conn.execute(
            "UPDATE ai_providers SET is_active = 1 - is_active WHERE id = ?",
            (provider_id,),
        )
        await self.conn.commit()

    # ─── Messages ───

    async def store_message(
        self, chat_id: int, user_id: int, username: str, first_name: str,
        message_text: str, is_forwarded: bool = False, forwarded_from: str = "",
        is_bot: bool = False, message_id: int = 0,
    ):
        await self.conn.execute(
            "INSERT INTO messages (chat_id, user_id, username, first_name, message_text, "
            "is_forwarded, forwarded_from, is_bot_message, message_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, username, first_name, message_text,
             int(is_forwarded), forwarded_from, int(is_bot), message_id),
        )
        await self.conn.commit()

    async def get_recent_messages(self, chat_id: int, limit: int = 50) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(row) for row in reversed(rows)]

    async def get_message_count(self, chat_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_id = ?", (chat_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    async def clear_messages(self, chat_id: int):
        await self.conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    async def get_old_messages(self, chat_id: int, keep_count: int) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM messages WHERE chat_id = ? AND id NOT IN "
            "(SELECT id FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?) "
            "ORDER BY id ASC",
            (chat_id, chat_id, keep_count),
        )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def delete_messages_by_ids(self, ids: list[int]):
        if not ids:
            return
        if IS_POSTGRES:
            placeholders = ",".join(f"${i+1}" for i in range(len(ids)))
        else:
            placeholders = ",".join("?" * len(ids))
        await self.conn.execute(
            f"DELETE FROM messages WHERE id IN ({placeholders})",
            ids,
        )
        await self.conn.commit()

    # ─── Key Events ───

    async def add_key_event(self, chat_id: int, event_text: str):
        await self.conn.execute(
            "INSERT INTO key_events (chat_id, event_text) VALUES (?, ?)",
            (chat_id, event_text),
        )
        await self.conn.commit()

    async def get_key_events(self, chat_id: int, limit: int = 20) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM key_events WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def clear_key_events(self, chat_id: int):
        await self.conn.execute("DELETE FROM key_events WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    # ─── Chat Settings ───

    async def ensure_chat_settings(self, chat_id: int):
        await self.conn.execute(
            "INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)",
            (chat_id,),
        )
        await self.conn.commit()

    async def get_chat_settings(self, chat_id: int) -> dict:
        await self.ensure_chat_settings(chat_id)
        cur = await self.conn.execute(
            "SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else {}

    async def update_chat_settings(self, chat_id: int, **kwargs):
        await self.ensure_chat_settings(chat_id)
        sets = []
        values = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            values.append(v)
        values.append(chat_id)
        await self.conn.execute(
            f"UPDATE chat_settings SET {', '.join(sets)} WHERE chat_id = ?",
            values,
        )
        await self.conn.commit()

    # ─── User Bans ───

    async def ban_user(self, user_id: int, username: str = "", banned_by: int = 0, reason: str = ""):
        await self.conn.execute(
            "INSERT OR REPLACE INTO user_bans (user_id, username, banned_by, reason, banned_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (user_id, username, banned_by, reason),
        )
        await self.conn.commit()

    async def unban_user(self, user_id: int):
        await self.conn.execute("DELETE FROM user_bans WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def is_banned(self, user_id: int) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM user_bans WHERE user_id = ?", (user_id,))
        return await cur.fetchone() is not None

    async def get_banned_users(self) -> list[dict]:
        cur = await self.conn.execute("SELECT * FROM user_bans ORDER BY banned_at DESC")
        rows = await cur.fetchall()
        return [dict(row) for row in rows]

    async def add_warning(self, user_id: int, username: str = "") -> int:
        cur = await self.conn.execute(
            "SELECT warnings FROM user_bans WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        warnings = (row[0] if row else 0) + 1
        if row:
            await self.conn.execute(
                "UPDATE user_bans SET warnings = ?, username = ? WHERE user_id = ?",
                (warnings, username, user_id),
            )
        else:
            await self.conn.execute(
                "INSERT INTO user_bans (user_id, username, warnings) VALUES (?, ?, ?)",
                (user_id, username, warnings),
            )
        await self.conn.commit()
        return warnings

    async def get_warnings(self, user_id: int) -> int:
        cur = await self.conn.execute("SELECT warnings FROM user_bans WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0

    async def clear_warnings(self, user_id: int):
        await self.conn.execute(
            "UPDATE user_bans SET warnings = 0 WHERE user_id = ?", (user_id,)
        )
        await self.conn.commit()

    # ─── User Relationships ───

    async def get_relationship(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT relationship FROM user_relationships WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else 0

    async def set_relationship(self, user_id: int, username: str, relationship: int):
        relationship = max(-100, min(100, relationship))
        await self.conn.execute(
            "INSERT OR REPLACE INTO user_relationships (user_id, username, relationship, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (user_id, username, relationship),
        )
        await self.conn.commit()

    async def adjust_relationship(self, user_id: int, username: str, delta: int) -> int:
        current = await self.get_relationship(user_id)
        new_val = max(-100, min(100, current + delta))
        await self.set_relationship(user_id, username, new_val)
        return new_val

    async def get_all_relationships(self) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM user_relationships ORDER BY relationship DESC"
        )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]
