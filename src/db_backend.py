"""
Async database backend abstraction.

If DATABASE_URL env var is set → Postgres via asyncpg (Railway).
Otherwise → SQLite via aiosqlite (local dev).

The wrapper handles:
- ? placeholders → $1, $2, ... for Postgres
- INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
- INSERT OR REPLACE → INSERT ... ON CONFLICT ... DO UPDATE
- datetime('now') → CURRENT_TIMESTAMP
- Schema DDL: INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL PRIMARY KEY,
  INTEGER → BIGINT, REAL → DOUBLE PRECISION, TIMESTAMP → TIMESTAMPTZ
- cursor.lastrowid / RETURNING id
- dict-like row access on both backends
"""
from __future__ import annotations

import os
import re
import logging
from typing import Any, Optional, Sequence

import aiosqlite

logger = logging.getLogger(__name__)

DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip()
IS_POSTGRES: bool = bool(DATABASE_URL)
DB_PATH: str = os.getenv("DB_PATH", "bot.db")

_asyncpg = None
if IS_POSTGRES:
    import asyncpg as _asyncpg  # type: ignore


# ── SQL translation ──────────────────────────────────────────────────────────

def _qmark_to_dollar(sql: str) -> str:
    """Replace ? placeholders with $1, $2, ... for Postgres (quote-aware)."""
    out: list[str] = []
    i = 0
    n = 0
    in_quote = False
    quote_ch: Optional[str] = None
    while i < len(sql):
        ch = sql[i]
        if in_quote:
            out.append(ch)
            if ch == quote_ch:
                if i + 1 < len(sql) and sql[i + 1] == quote_ch:
                    i += 1
                    out.append(sql[i])
                else:
                    in_quote = False
                    quote_ch = None
        elif ch in "'\"":
            in_quote = True
            quote_ch = ch
            out.append(ch)
        elif ch == "?":
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


_RE_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_RE_INSERT_OR_REPLACE = re.compile(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", re.IGNORECASE)
_RE_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)

# For INSERT OR REPLACE we need to know the PK column to build ON CONFLICT.
# Most of our tables use a single-column PK; this regex extracts the table
# name and we look up the PK from a hardcoded map (see _PK_MAP below).
_RE_INSERT_TABLE = re.compile(
    r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)", re.IGNORECASE,
)

_PK_MAP: dict[str, str] = {
    "settings": "key",
    "admins": "user_id",
    "chat_settings": "chat_id",
    "user_relationships": "user_id",
    "user_bans": "user_id",
    "bot_balance": "chat_id",
}


def translate_sql(sql: str) -> str:
    """Translate SQLite-flavored SQL to Postgres flavor (no-op on SQLite)."""
    if not IS_POSTGRES:
        return sql

    has_or_ignore = bool(_RE_INSERT_OR_IGNORE.search(sql))
    has_or_replace = bool(_RE_INSERT_OR_REPLACE.search(sql))

    if has_or_ignore:
        sql = _RE_INSERT_OR_IGNORE.sub("INSERT INTO", sql)

    if has_or_replace:
        m = _RE_INSERT_TABLE.search(sql)
        table = m.group(1) if m else ""
        pk = _PK_MAP.get(table, "id")
        sql = _RE_INSERT_OR_REPLACE.sub("INSERT INTO", sql)
        if "on conflict" not in sql.lower():
            # Find the column list to build the DO UPDATE SET clause
            col_match = re.search(
                r"INSERT\s+INTO\s+\w+\s*\(([^)]+)\)\s*VALUES", sql, re.IGNORECASE,
            )
            if col_match:
                cols = [c.strip() for c in col_match.group(1).split(",")]
                set_clause = ", ".join(
                    f"{c} = EXCLUDED.{c}" for c in cols if c != pk
                )
                sql = sql.rstrip(" \n\t;") + \
                    f" ON CONFLICT ({pk}) DO UPDATE SET {set_clause}"
            else:
                sql = sql.rstrip(" \n\t;") + f" ON CONFLICT ({pk}) DO NOTHING"

    sql = _RE_DATETIME_NOW.sub("CURRENT_TIMESTAMP", sql)

    if has_or_ignore and "on conflict" not in sql.lower():
        sql = sql.rstrip(" \n\t;") + " ON CONFLICT DO NOTHING"

    sql = _qmark_to_dollar(sql)
    return sql


_RE_DDL = re.compile(r"^\s*(CREATE\s+TABLE|ALTER\s+TABLE|CREATE\s+INDEX)\b", re.IGNORECASE)


def _looks_like_ddl(sql: str) -> bool:
    return bool(_RE_DDL.match(sql or ""))


def translate_schema(ddl: str) -> str:
    """Translate CREATE TABLE / ALTER TABLE DDL between SQLite and Postgres."""
    if not IS_POSTGRES:
        return ddl
    ddl = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "__PK_AUTO__", ddl, flags=re.IGNORECASE,
    )
    ddl = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\b",
        "__PK_AUTO__", ddl, flags=re.IGNORECASE,
    )
    ddl = re.sub(r"\bINTEGER\b", "BIGINT", ddl, flags=re.IGNORECASE)
    ddl = re.sub(r"\bREAL\b", "DOUBLE PRECISION", ddl, flags=re.IGNORECASE)
    ddl = ddl.replace("__PK_AUTO__", "BIGSERIAL PRIMARY KEY")
    ddl = _RE_DATETIME_NOW.sub("CURRENT_TIMESTAMP", ddl)
    return ddl


# ── Connection wrappers ──────────────────────────────────────────────────────

class AsyncCursor:
    """Async cursor wrapper — common surface for aiosqlite and asyncpg."""

    def __init__(self, raw, backend: str):
        self._raw = raw
        self._backend = backend
        self.lastrowid: Optional[int] = None

    async def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> "AsyncCursor":
        sql_t = translate_sql(sql)
        if _looks_like_ddl(sql_t):
            sql_t = translate_schema(sql_t)
        if self._backend == "postgres":
            if params:
                await self._raw.execute(sql_t, *params)
            else:
                await self._raw.execute(sql_t)
        else:
            if params is not None and params != ():
                await self._raw.execute(sql_t, params)
            else:
                await self._raw.execute(sql_t)
        return self

    async def fetchone(self):
        if self._backend == "postgres":
            return self._raw.fetchone() if hasattr(self._raw, "fetchone") else None
        return await self._raw.fetchone()

    async def fetchall(self):
        if self._backend == "postgres":
            return self._raw.fetchall() if hasattr(self._raw, "fetchall") else []
        return await self._raw.fetchall()

    @property
    def rowcount(self) -> int:
        return getattr(self._raw, "rowcount", -1) or 0

    async def close(self):
        if self._backend == "sqlite":
            await self._raw.close()


class AsyncConn:
    """Async DB-agnostic connection wrapper.

    For PostgreSQL: uses a pool internally — each execute acquires a fresh connection.
    For SQLite: uses the single connection (SQLite handles concurrent access via WAL).
    """

    def __init__(self, raw, backend: str):
        self._raw = raw
        self.backend = backend
        self._pool = None

    def set_pool(self, pool):
        self._pool = pool

    async def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> AsyncCursor:
        sql_t = translate_sql(sql)
        if _looks_like_ddl(sql_t):
            sql_t = translate_schema(sql_t)

        if self.backend == "postgres":
            # Use pool if available, otherwise raw connection
            if self._pool:
                async with self._pool.acquire() as conn:
                    if params:
                        rows = await conn.fetch(sql_t, *params)
                    else:
                        rows = await conn.fetch(sql_t)
            else:
                if params:
                    rows = await self._raw.fetch(sql_t, *params)
                else:
                    rows = await self._raw.fetch(sql_t)
            cur = AsyncCursor(_PgResultProxy(rows), "postgres")
            return cur
        else:
            cur = await self._raw.execute(sql_t, params if params else ())
            return AsyncCursor(cur, "sqlite")

    async def executescript(self, ddl: str) -> None:
        ddl_t = translate_schema(ddl)
        if self.backend == "postgres":
            if self._pool:
                async with self._pool.acquire() as conn:
                    for stmt in _split_sql(ddl_t):
                        stmt = stmt.strip()
                        if stmt:
                            await conn.execute(stmt)
            else:
                for stmt in _split_sql(ddl_t):
                    stmt = stmt.strip()
                    if stmt:
                        await self._raw.execute(stmt)
        else:
            await self._raw.executescript(ddl_t)

    async def commit(self) -> None:
        if self.backend == "sqlite":
            await self._raw.commit()

    async def close(self) -> None:
        if self.backend == "postgres":
            if self._pool:
                await self._pool.close()
                self._pool = None
            else:
                await self._raw.close()
        else:
            await self._raw.close()

    async def insert_returning_id(
        self, sql: str, params: Optional[Sequence[Any]] = None,
    ) -> Optional[int]:
        """Run an INSERT and return the new row's id."""
        sql_t = translate_sql(sql)
        if self.backend == "postgres":
            if "returning" not in sql_t.lower():
                sql_t = sql_t.rstrip(" \n\t;") + " RETURNING id"
            if params:
                row = await self._raw.fetchrow(sql_t, *params)
            else:
                row = await self._raw.fetchrow(sql_t)
            if row:
                return int(row["id"]) if "id" in row.keys() else int(row[0])
            return None
        else:
            cur = await self._raw.execute(sql_t, params if params else ())
            last_id = cur.lastrowid
            await self._raw.commit()
            return last_id


class _PgResultProxy:
    """Adapter so AsyncCursor.fetchone/fetchall work with asyncpg fetch() results."""

    def __init__(self, rows: list):
        self._rows = list(rows)
        self._idx = 0
        self.rowcount = len(self._rows)

    def fetchone(self):
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return _PgRow(row)
        return None

    def fetchall(self):
        rest = [_PgRow(r) for r in self._rows[self._idx:]]
        self._idx = len(self._rows)
        return rest


class _PgRow:
    """Wrap asyncpg.Record to support both dict-style and tuple-style access."""

    def __init__(self, record):
        self._record = record

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._record[key]
        return self._record[key]

    def __iter__(self):
        return iter(self._record)

    def __len__(self):
        return len(self._record)

    def keys(self):
        return self._record.keys()

    def values(self):
        return self._record.values()

    def items(self):
        return list(zip(self._record.keys(), self._record))


def _split_sql(sql: str) -> list[str]:
    """Split multi-statement SQL by semicolons (quote-aware)."""
    statements: list[str] = []
    current: list[str] = []
    in_quote = False
    quote_ch: Optional[str] = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_quote:
            current.append(ch)
            if ch == quote_ch:
                if i + 1 < len(sql) and sql[i + 1] == quote_ch:
                    i += 1
                    current.append(sql[i])
                else:
                    in_quote = False
                    quote_ch = None
        elif ch in "'\"":
            in_quote = True
            quote_ch = ch
            current.append(ch)
        elif ch == ";":
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1
    last = "".join(current).strip()
    if last:
        statements.append(last)
    return statements


_pg_pool = None


async def _get_pg_conn():
    """Acquire a connection from pool (PostgreSQL)."""
    global _pg_pool
    if _pg_pool is None or _pg_pool._closed:
        url = os.getenv("DATABASE_URL", DATABASE_URL).strip()
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        _pg_pool = await _asyncpg.create_pool(
            url,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("PostgreSQL pool создан (2-10 connections)")
    return await _pg_pool.acquire()


async def _release_pg_conn(conn):
    """Release connection back to pool."""
    global _pg_pool
    if _pg_pool and conn:
        await _pg_pool.release(conn)


# ── Public connect() ─────────────────────────────────────────────────────────

async def connect() -> AsyncConn:
    """Open an async connection to the configured database."""
    if IS_POSTGRES:
        url = os.getenv("DATABASE_URL", DATABASE_URL).strip()
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]

        global _pg_pool
        _pg_pool = await _asyncpg.create_pool(
            url,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.info("PostgreSQL pool создан (2-10 connections)")

        # Create a dummy connection for compatibility (init queries)
        raw = await _asyncpg.connect(url)
        conn = AsyncConn(raw, "postgres")
        conn.set_pool(_pg_pool)
        return conn

    db_path = os.getenv("DB_PATH", DB_PATH)
    raw = await aiosqlite.connect(db_path)
    raw.row_factory = aiosqlite.Row
    await raw.execute("PRAGMA journal_mode = WAL")
    await raw.execute("PRAGMA foreign_keys = ON")
    logger.info(f"Подключение к SQLite: {db_path}")
    return AsyncConn(raw, "sqlite")


# ── Helpers ──────────────────────────────────────────────────────────────────

async def column_exists(conn: AsyncConn, table: str, column: str) -> bool:
    if conn.backend == "postgres":
        cur = await conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = $2 LIMIT 1",
            (table, column),
        )
        return await cur.fetchone() is not None
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    return any(r[1] == column for r in rows)
