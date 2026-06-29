"""
КАЗИБЕТ — модуль ставок на футбол.
Интегрирован в главное меню Казимира через кнопку «🎯 КАЗИБЕТ».
Использует общую БД с основным ботом (таблица players/баланс).
"""
import os
import json
import asyncio
import hashlib
import logging
import asyncpg
import aiohttp
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  КОНФИГ — читает env, fallback — значения основного бота
# ════════════════════════════════════════════════════════════

ADMIN_ID     = int(os.getenv("ADMIN_TG_ID", os.getenv("ADMIN_ID", "6163072393")))
DATABASE_URL = os.getenv("DATABASE_URL", "")

DAILY_BONUS    = 500        # дневной бонус в антван коинах (бонус-кнопка в казибете)
BONUS_COOLDOWN = 86400      # 24ч

MIN_BET = 10
MAX_BET = 100_000

# ── SStats.net API — бесплатно, без ключа ────────────────────────────────────
SSTATS_BASE = "https://api.sstats.net"
SSTATS_KEY  = os.getenv("SSTATS_KEY", "tof5dv59vu3tzog3")

# Лиги SStats (leagueid совпадают с api-football)
LEAGUES = {
    # Топ-5 европейских лиг
    39:  "🏴󠁧󠁢󠁥󠁮󠁧󠁿 АПЛ",
    140: "🇪🇸 Ла Лига",
    78:  "🇩🇪 Бундеслига",
    135: "🇮🇹 Серия А",
    61:  "🇫🇷 Лига 1",
    # Топ 6–10 европейских лиг
    94:  "🇵🇹 Примейра (Португалия)",
    88:  "🇳🇱 Эредивизи (Нидерланды)",
    203: "🇹🇷 Суперлига (Турция)",
    144: "🇧🇪 Жюпиле Про Лига (Бельгия)",
    40:  "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Чемпионшип (Англия)",
    # Кубки топ-5 лиг
    45:  "🏴󠁧󠁢󠁥󠁮󠁧󠁿 FA Cup",
    143: "🇪🇸 Кубок Испании",
    81:  "🇩🇪 DFB-Pokal",
    137: "🇮🇹 Кубок Италии",
    66:  "🇫🇷 Кубок Франции",
    # Еврокубки
    2:   "🏆 Лига чемпионов",
    3:   "🥈 Лига Европы",
    848: "🥉 Лига конференций",
    # СНГ
    235: "🇷🇺 РПЛ",
    333: "🇺🇦 УПЛ",
    # Америка
    253: "🇺🇸 MLS",
    71:  "🇧🇷 Бразилия Серия А",
    72:  "🇧🇷 Бразилия Серия B",
}

# Структура меню: регион → подразделы → список league_id
LEAGUE_REGIONS = {
    "EU": "🇪🇺 Европа",
    "AM": "🌎 Америка",
}

LEAGUE_GROUPS = {
    "EU": [
        ("TOP5",  "⚽ Топ-5 лиг",        [39, 140, 78, 135, 61]),
        ("TOP10", "🌟 Топ 6–10 лиг",     [94, 88, 203, 144, 40]),
        ("CUPS",  "🏆 Кубки топ-5 лиг",  [45, 143, 81, 137, 66]),
        ("EURO",  "🌍 Еврокубки",        [2, 3, 848]),
        ("CIS",   "🇷🇺🇺🇦 СНГ",          [235, 333]),
    ],
    "AM": [
        ("MLS", "🇺🇸 MLS",              [253]),
        ("BRA", "🇧🇷 Бразильские лиги", [71, 72]),
    ],
}

# ── Коды рынков и подписи ────────────────────────────────────────────────────
# Коды bet_on НЕ должны содержать "_" — он используется как разделитель
# в callback_data (cb:bt_{fixture}_{bet_on}, cb:ba_{fixture}_{bet_on}_{amount}).

BET_LABELS = {
    # 1X2
    "home":  "П1",
    "draw":  "X",
    "away":  "П2",
    # Тоталы голов
    "o15":   "ТБ 1.5",
    "u15":   "ТМ 1.5",
    "o25":   "ТБ 2.5",
    "u25":   "ТМ 2.5",
    "o35":   "ТБ 3.5",
    "u35":   "ТМ 3.5",
    # Обе забьют
    "bttsy": "Обе забьют: Да",
    "bttsn": "Обе забьют: Нет",
    # Угловые ТБ/ТМ 9.5
    "c95o":  "Угловые ТБ 9.5",
    "c95u":  "Угловые ТМ 9.5",
    # Жёлтые карточки ТБ/ТМ 4.5
    "yc45o": "ЖК ТБ 4.5",
    "yc45u": "ЖК ТМ 4.5",
    # Двойной шанс
    "dc1x":  "1X (П1 или ничья)",
    "dc12":  "12 (без ничьей)",
    "dcx2":  "X2 (ничья или П2)",
    # Точный счёт (топ-6)
    "cs10":  "Точный счёт 1:0",
    "cs21":  "Точный счёт 2:1",
    "cs20":  "Точный счёт 2:0",
    "cs00":  "Точный счёт 0:0",
    "cs11":  "Точный счёт 1:1",
    "cs22":  "Точный счёт 2:2",
}

# Маппинг код → колонка в cached_fixtures
ODDS_COL = {
    "home":  "odds_home",
    "draw":  "odds_draw",
    "away":  "odds_away",
    "o15":   "odds_o15",
    "u15":   "odds_u15",
    "o25":   "odds_o25",
    "u25":   "odds_u25",
    "o35":   "odds_o35",
    "u35":   "odds_u35",
    "bttsy": "odds_bttsy",
    "bttsn": "odds_bttsn",
    "c95o":  "odds_c95o",
    "c95u":  "odds_c95u",
    "yc45o": "odds_yc45o",
    "yc45u": "odds_yc45u",
    "dc1x":  "odds_dc1x",
    "dc12":  "odds_dc12",
    "dcx2":  "odds_dcx2",
    "cs10":  "odds_cs10",
    "cs21":  "odds_cs21",
    "cs20":  "odds_cs20",
    "cs00":  "odds_cs00",
    "cs11":  "odds_cs11",
    "cs22":  "odds_cs22",
}

OU_LINES    = (1.5, 2.5, 3.5)
OU_OVER_KEY = {1.5: "o15", 2.5: "o25", 3.5: "o35"}
OU_UNDER_KEY = {1.5: "u15", 2.5: "u25", 3.5: "u35"}

# Точный счёт: код → (home_goals, away_goals)
CORRECT_SCORES = {
    "cs10": (1, 0),
    "cs21": (2, 1),
    "cs20": (2, 0),
    "cs00": (0, 0),
    "cs11": (1, 1),
    "cs22": (2, 2),
}

# Префикс кодов для голлеров (Anytime Goal Scorer).
# Полный код ставки: "gsa<player_id>", напр. "gsa48389".
GOAL_SCORER_PREFIX = "gsa"

# Максимум вариантов голлеров на матч (для UI и БД)
MAX_SCORERS_PER_FIXTURE = 40

# Текущий сезон
CURRENT_YEAR = 2024

# FSM
WAIT_BET_AMOUNT         = 1
WAIT_GIVE_ID            = 2
WAIT_GIVE_AMT           = 3
WAIT_BROADCAST          = 4
WAIT_CBET_TITLE         = 5
WAIT_CBET_OPTIONS       = 6
WAIT_CBET_AMOUNT        = 7
WAIT_CBET_SETTLE_ID     = 8
WAIT_CBET_SETTLE_OPTION = 9
WAIT_PROMO_CODE         = 10
WAIT_EXPRESS_AMOUNT     = 11
WAIT_CAPPER_FOLLOW_AMT  = 12
WAIT_NEW_LEAGUE_REQUEST = 13
WAIT_RB_FIXTURE_RESULT  = 14   # ввод результата матча (счёт + опции) для ручного резолва ставок

# ── валюта — общая с казино (антван коины) ───────────────────────────────────
COIN_EMOJI = "🪙"
COIN_NAME  = "антван коинов"

def fmt_coins(n: int) -> str:
    """Форматирует сумму антван коинов (пример: 1 250 🪙)."""
    return f"{int(n):,}".replace(",", " ") + f" {COIN_EMOJI}"

# ════════════════════════════════════════════════════════════
#  DATABASE
# ════════════════════════════════════════════════════════════

_pool = None
_update_lock = asyncio.Lock()  # предотвращает параллельные обновления

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool

async def init_db():
    """Создаёт/мигрирует таблицы казибета. Таблица players создаётся
    основным ботом; мы только добавляем к ней недостающие колонки."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # ── Расширяем основную таблицу players колонками казибета ──
        for stmt in (
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS last_bonus TIMESTAMP",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS is_capper BOOLEAN DEFAULT FALSE",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS capper_test_cooldown_until TIMESTAMP",
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS capper_test_taken BOOLEAN DEFAULT FALSE",
        ):
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning("casibet migration skipped (%s): %s", e.__class__.__name__, stmt)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS cached_fixtures (
                fixture_id  BIGINT PRIMARY KEY,
                league_id   INTEGER NOT NULL,
                league_name TEXT NOT NULL,
                home_team   TEXT NOT NULL,
                away_team   TEXT NOT NULL,
                match_date  TEXT NOT NULL,
                start_ts    BIGINT NOT NULL,
                status      TEXT DEFAULT 'ns',
                home_score  INTEGER DEFAULT NULL,
                away_score  INTEGER DEFAULT NULL,
                odds_home   FLOAT DEFAULT NULL,
                odds_draw   FLOAT DEFAULT NULL,
                odds_away   FLOAT DEFAULT NULL,
                odds_o15    FLOAT DEFAULT NULL,
                odds_u15    FLOAT DEFAULT NULL,
                odds_o25    FLOAT DEFAULT NULL,
                odds_u25    FLOAT DEFAULT NULL,
                odds_o35    FLOAT DEFAULT NULL,
                odds_u35    FLOAT DEFAULT NULL,
                odds_bttsy  FLOAT DEFAULT NULL,
                odds_bttsn  FLOAT DEFAULT NULL,
                odds_c95o   FLOAT DEFAULT NULL,
                odds_c95u   FLOAT DEFAULT NULL,
                odds_yc45o  FLOAT DEFAULT NULL,
                odds_yc45u  FLOAT DEFAULT NULL,
                odds_dc1x   FLOAT DEFAULT NULL,
                odds_dc12   FLOAT DEFAULT NULL,
                odds_dcx2   FLOAT DEFAULT NULL,
                odds_cs10   FLOAT DEFAULT NULL,
                odds_cs21   FLOAT DEFAULT NULL,
                odds_cs20   FLOAT DEFAULT NULL,
                odds_cs00   FLOAT DEFAULT NULL,
                odds_cs11   FLOAT DEFAULT NULL,
                odds_cs22   FLOAT DEFAULT NULL,
                total_corners INTEGER DEFAULT NULL,
                total_yellows INTEGER DEFAULT NULL,
                fetched_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        # Голлеры (Anytime Goal Scorer) — один матч может иметь много игроков
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS fixture_scorers (
                fixture_id  BIGINT NOT NULL,
                player_id   INTEGER NOT NULL,
                player_name TEXT NOT NULL,
                team_side   TEXT DEFAULT '?',
                odds_any    FLOAT NOT NULL,
                scored      BOOLEAN DEFAULT NULL,
                PRIMARY KEY (fixture_id, player_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS api_fetch_log (
                id         SERIAL PRIMARY KEY,
                fetch_type TEXT,
                fetched_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT,
                match_id   TEXT,
                match_info TEXT,
                bet_on     TEXT,
                amount     BIGINT,
                odds       FLOAT,
                result     TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_bets (
                id             SERIAL PRIMARY KEY,
                title          TEXT NOT NULL,
                options        JSONB NOT NULL,
                status         TEXT DEFAULT 'open',
                winning_option INTEGER DEFAULT NULL,
                created_by     BIGINT,
                created_at     TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_bet_entries (
                id            SERIAL PRIMARY KEY,
                custom_bet_id INTEGER REFERENCES custom_bets(id) ON DELETE CASCADE,
                user_id       BIGINT,
                option_index  INTEGER NOT NULL,
                amount        BIGINT NOT NULL,
                odds          FLOAT NOT NULL,
                result        TEXT DEFAULT 'pending',
                created_at    TIMESTAMP DEFAULT NOW()
            )
        """)
        # промокоды — используем общие таблицы основного бота (promocodes/promo_used)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS express_bets (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT,
                amount     BIGINT NOT NULL,
                total_odds FLOAT  NOT NULL,
                result     TEXT   DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS express_bet_legs (
                id          SERIAL PRIMARY KEY,
                express_id  INTEGER REFERENCES express_bets(id) ON DELETE CASCADE,
                match_id    TEXT   NOT NULL,
                match_info  TEXT   NOT NULL,
                bet_on      TEXT   NOT NULL,
                odds        FLOAT  NOT NULL,
                result      TEXT   DEFAULT 'pending'
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS capper_tips (
                id          SERIAL PRIMARY KEY,
                capper_id   BIGINT,
                bet_type    TEXT   NOT NULL,   -- 'single' | 'express'
                ref_id      BIGINT NOT NULL,   -- bets.id или express_bets.id
                description TEXT,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)

        # ── миграции старых БД казибета ──
        for stmt in (
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS via_tip_id INTEGER",
            "ALTER TABLE express_bets ADD COLUMN IF NOT EXISTS via_tip_id INTEGER",
            "ALTER TABLE bets DROP CONSTRAINT IF EXISTS bets_match_id_fkey",
            "ALTER TABLE bets ALTER COLUMN match_id TYPE TEXT USING match_id::TEXT",
            # Рынки тоталов и «обе забьют»
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_o15 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_u15 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_o25 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_u25 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_o35 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_u35 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_bttsy FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_bttsn FLOAT",
            # Угловые, карточки, двойной шанс, точный счёт
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_c95o FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_c95u FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_yc45o FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_yc45u FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_dc1x FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_dc12 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_dcx2 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_cs10 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_cs21 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_cs20 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_cs00 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_cs11 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS odds_cs22 FLOAT",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS total_corners INTEGER",
            "ALTER TABLE cached_fixtures ADD COLUMN IF NOT EXISTS total_yellows INTEGER",
        ):
            try:
                await conn.execute(stmt)
            except Exception as e:
                logger.warning("casibet migration skipped (%s): %s", e.__class__.__name__, stmt)
    logger.info("Casibet DB ready")

# ── players ───────────────────────────────────────────────────────────────────

async def get_or_create(user_id, username=None, first_name=None):
    """Игрок ДОЛЖЕН быть зарегистрирован в основном боте.
    Возвращает словарь с алиасом coins=balance для совместимости с казибет-кодом."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT *, balance AS coins FROM players WHERE tg_id=$1", user_id
        )
        if row:
            return dict(row)
    # Fallback: ping main bot's registration path. Игрок не должен сюда попасть,
    # но на случай прямого обращения — возвращаем минимальный stub.
    return {"user_id": user_id, "coins": 0, "balance": 0, "last_bonus": None,
            "is_capper": False, "capper_test_cooldown_until": None,
            "capper_test_taken": False,
            "username": username, "first_name": first_name}

async def get_player(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT *, balance AS coins FROM players WHERE tg_id=$1", user_id
        )
        return dict(row) if row else None

async def add_coins(user_id, delta):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE players SET balance=balance+$1, total_won=total_won+$1 WHERE tg_id=$2", delta, user_id)

async def set_last_bonus(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE players SET balance=balance+$1, total_won=total_won+$1, last_bonus=NOW() WHERE tg_id=$2",
            DAILY_BONUS, user_id
        )

async def all_user_ids():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT tg_id AS user_id FROM players")
        return [r["user_id"] for r in rows]

async def db_stats():
    pool = await get_pool()
    async with pool.acquire() as conn:
        users   = await conn.fetchval("SELECT COUNT(*) FROM players")
        total   = await conn.fetchval("SELECT COUNT(*) FROM bets")
        pending = await conn.fetchval("SELECT COUNT(*) FROM bets WHERE result='pending'")
        won     = await conn.fetchval("SELECT COUNT(*) FROM bets WHERE result='win'")
        lost    = await conn.fetchval("SELECT COUNT(*) FROM bets WHERE result='lose'")
        coins   = await conn.fetchval("SELECT COALESCE(SUM(balance),0) FROM players")
        return dict(users=users, total=total, pending=pending, won=won, lost=lost, coins=coins)

# ── fixtures ──────────────────────────────────────────────────────────────────

_SAVE_FIXTURE_COLS = list(ODDS_COL.values())  # фикс. порядок «колонка odds_*»

async def save_fixtures(fixtures: list):
    """Сохраняет матчи со всеми котировками. Колонки берутся из `ODDS_COL`,
    чтобы при добавлении нового рынка не приходилось править SQL.
    """
    pool = await get_pool()
    # Готовим SQL динамически: $1..$7 — базовые, далее по числу odds-колонок.
    base_cols   = ["fixture_id", "league_id", "league_name", "home_team",
                   "away_team", "match_date", "start_ts"]
    all_cols    = base_cols + _SAVE_FIXTURE_COLS
    placeholders = ", ".join(f"${i+1}" for i in range(len(all_cols)))
    update_set   = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in
        ["home_team", "away_team"] + _SAVE_FIXTURE_COLS
    ) + ", status = 'ns', fetched_at = NOW()"
    sql = (
        f"INSERT INTO cached_fixtures ({', '.join(all_cols)}, status) "
        f"VALUES ({placeholders}, 'ns') "
        f"ON CONFLICT (fixture_id) DO UPDATE SET {update_set}"
    )
    async with pool.acquire() as conn:
        for f in fixtures:
            params = [
                f["fixture_id"], f["league_id"], f["league_name"],
                f["home"], f["away"], f["date"], f["timestamp"],
            ]
            for col in _SAVE_FIXTURE_COLS:
                params.append(f.get(col))
            await conn.execute(sql, *params)
        await conn.execute("INSERT INTO api_fetch_log (fetch_type) VALUES ('fixtures')")
    logger.info(f"Saved {len(fixtures)} fixtures")


async def save_fixture_scorers(fixture_id: int, scorers: list):
    """scorers — список dict'ов {player_id, player_name, team_side, odds_any}.
    Перезаписывает все записи по fixture_id.
    """
    if not scorers:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM fixture_scorers WHERE fixture_id=$1", fixture_id
            )
            for s in scorers[:MAX_SCORERS_PER_FIXTURE]:
                await conn.execute(
                    "INSERT INTO fixture_scorers "
                    "(fixture_id, player_id, player_name, team_side, odds_any) "
                    "VALUES ($1,$2,$3,$4,$5) "
                    "ON CONFLICT (fixture_id, player_id) DO UPDATE SET "
                    "player_name=EXCLUDED.player_name, "
                    "team_side=EXCLUDED.team_side, "
                    "odds_any=EXCLUDED.odds_any",
                    fixture_id, int(s["player_id"]), str(s["player_name"])[:60],
                    s.get("team_side", "?"), float(s["odds_any"]),
                )


async def load_fixture_scorers(fixture_id: int) -> list:
    """Возвращает список голлеров матча, отсортированный по кэфу (популярные сверху)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT player_id, player_name, team_side, odds_any "
            "FROM fixture_scorers WHERE fixture_id=$1 "
            "ORDER BY odds_any ASC",
            fixture_id,
        )
    return [dict(r) for r in rows]


async def get_fixture_scorer(fixture_id: int, player_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT player_id, player_name, team_side, odds_any "
            "FROM fixture_scorers WHERE fixture_id=$1 AND player_id=$2",
            fixture_id, player_id,
        )
    return dict(row) if row else None


async def find_scorer_by_player_id(player_id: int) -> dict | None:
    """Ищет игрока в fixture_scorers по player_id ВО ВСЕХ матчах.

    Нужно для корректного отображения ставок «автор гола» в админке:
    если конкретный матч уже в `cached_fixtures` обновился и
    fixture_scorers переписаны без этого игрока, мы всё равно
    можем достать имя из любого другого матча с тем же player_id
    (id игрока — глобальный SStats).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT player_id, player_name, team_side, odds_any "
            "FROM fixture_scorers WHERE player_id=$1 "
            "ORDER BY fixture_id DESC LIMIT 1",
            player_id,
        )
    return dict(row) if row else None

async def load_fixtures(league_id: int = None) -> list:
    pool = await get_pool()
    now_ts = int(datetime.now(timezone.utc).timestamp())
    async with pool.acquire() as conn:
        if league_id:
            rows = await conn.fetch("""
                SELECT * FROM cached_fixtures
                WHERE league_id=$1 AND start_ts > $2 AND status='ns'
                ORDER BY start_ts ASC LIMIT 20
            """, league_id, now_ts)
        else:
            rows = await conn.fetch("""
                SELECT * FROM cached_fixtures
                WHERE start_ts > $1 AND status='ns'
                ORDER BY start_ts ASC LIMIT 50
            """, now_ts)
    return [dict(r) for r in rows]

async def get_last_fetch_time():
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT fetched_at FROM api_fetch_log WHERE fetch_type='fixtures' "
            "ORDER BY fetched_at DESC LIMIT 1"
        )
        return row["fetched_at"] if row else None

async def update_fixture_result(fixture_id: int, home_score: int, away_score: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE cached_fixtures SET status='ft', home_score=$1, away_score=$2 "
            "WHERE fixture_id=$3",
            home_score, away_score, fixture_id
        )

# ── bets ──────────────────────────────────────────────────────────────────────

async def save_bet(user_id, fixture_id, match_info, bet_on, amount, odds,
                   via_tip_id: int | None = None) -> int | None:
    """Атомарно списывает коины и создаёт ставку. Если что-то идёт не так
    (недостаточно коинов / ошибка INSERT) — транзакция откатывается, баланс не
    меняется. Возвращает id ставки при успехе, None — недостаточно средств."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT balance AS coins FROM players WHERE tg_id=$1 FOR UPDATE", user_id
            )
            if not row or row["coins"] < amount:
                return None
            await conn.execute(
                "UPDATE players SET balance=balance-$1, total_lost=total_lost+$1 WHERE tg_id=$2", amount, user_id
            )
            r = await conn.fetchrow(
                "INSERT INTO bets (user_id,match_id,match_info,bet_on,amount,odds,via_tip_id) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
                user_id, str(fixture_id), match_info, bet_on, amount, odds, via_tip_id
            )
            return int(r["id"])

async def get_user_bets(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM bets WHERE user_id=$1 ORDER BY created_at DESC LIMIT 15",
            user_id
        )
        return [dict(r) for r in rows]

async def get_user_cbet_entries(user_id):
    """Возвращает записи игрока по кастомным ставкам вместе с названием/вариантом."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT e.id, e.custom_bet_id, e.option_index, e.amount, e.odds, "
            "       e.result, e.created_at, "
            "       c.title AS cbet_title, c.options AS cbet_options, "
            "       c.status AS cbet_status "
            "FROM custom_bet_entries e "
            "JOIN custom_bets c ON c.id = e.custom_bet_id "
            "WHERE e.user_id=$1 "
            "ORDER BY e.created_at DESC LIMIT 15",
            user_id,
        )
    result = []
    for r in rows:
        d = dict(r)
        opts = d.get("cbet_options")
        if isinstance(opts, str):
            try:
                opts = json.loads(opts)
            except Exception:
                opts = []
        d["cbet_options"] = opts or []
        result.append(d)
    return result

async def get_pending_by_fixture(fixture_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM bets WHERE match_id=$1 AND result='pending'", str(fixture_id)
        )
        return [dict(r) for r in rows]

async def settle_bet(bet_id, result, winnings, user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE bets SET result=$1 WHERE id=$2", result, bet_id)
        if result == "win" and winnings > 0:
            await conn.execute(
                "UPDATE players SET balance=balance+$1, total_won=total_won+$1 WHERE tg_id=$2", winnings, user_id
            )
        elif result == "void" and winnings > 0:
            # Возврат ставки: только кредитуем баланс, без total_won.
            await conn.execute(
                "UPDATE players SET balance=balance+$1 WHERE tg_id=$2",
                winnings, user_id,
            )

# ── custom bets ───────────────────────────────────────────────────────────────

MAX_CUSTOM_OPTIONS = 10

async def create_custom_bet(title: str, options: list, created_by: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO custom_bets (title, options, created_by) "
            "VALUES ($1, $2::jsonb, $3) RETURNING id",
            title, json.dumps(options), created_by,
        )
        return row["id"]

async def list_open_custom_bets() -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, options FROM custom_bets "
            "WHERE status='open' ORDER BY created_at DESC LIMIT 30"
        )
    result = []
    for r in rows:
        d = dict(r)
        opts = d["options"]
        if isinstance(opts, str):
            opts = json.loads(opts)
        d["options"] = opts
        result.append(d)
    return result

async def get_custom_bet(cbet_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM custom_bets WHERE id=$1", cbet_id)
    if not row:
        return None
    d = dict(row)
    opts = d["options"]
    if isinstance(opts, str):
        opts = json.loads(opts)
    d["options"] = opts
    return d

async def _user_cbet_recent(user_id: int, cbet_id: int) -> bool:
    """Внутренняя проверка: была ли у пользователя ставка по ИМЕННО этой
    кастомной ставке (cbet_id) за последние 24 часа. Разные cbets
    независимы. Администратор (ADMIN_ID) не ограничивается.

    Нигде в UI не упоминается и не документируется намеренно.
    """
    if user_id == ADMIN_ID:
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM custom_bet_entries "
            "WHERE user_id=$1 AND custom_bet_id=$2 "
            "AND created_at > NOW() - INTERVAL '24 hours' "
            "LIMIT 1",
            user_id, cbet_id,
        )
    return row is not None


async def save_cbet_entry(user_id: int, cbet_id: int, option_index: int,
                           amount: int, odds: float) -> bool:
    """Атомарно списывает коины и создаёт запись по кастомной ставке."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cb = await conn.fetchrow(
                "SELECT status FROM custom_bets WHERE id=$1 FOR UPDATE", cbet_id
            )
            if not cb or cb["status"] != "open":
                return False
            row = await conn.fetchrow(
                "SELECT balance AS coins FROM players WHERE tg_id=$1 FOR UPDATE", user_id
            )
            if not row or row["coins"] < amount:
                return False
            await conn.execute(
                "UPDATE players SET balance=balance-$1, total_lost=total_lost+$1 WHERE tg_id=$2", amount, user_id
            )
            await conn.execute(
                "INSERT INTO custom_bet_entries "
                "(custom_bet_id, user_id, option_index, amount, odds) "
                "VALUES ($1,$2,$3,$4,$5)",
                cbet_id, user_id, option_index, amount, odds,
            )
    return True

async def settle_custom_bet(cbet_id: int, winning_index: int | None) -> list[dict]:
    """Рассчитывает кастомную ставку. winning_index=None → отмена (возврат денег).
    Возвращает список записей для уведомления пользователей."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cb = await conn.fetchrow(
                "SELECT * FROM custom_bets WHERE id=$1 FOR UPDATE", cbet_id
            )
            if not cb or cb["status"] != "open":
                return []
            entries = await conn.fetch(
                "SELECT * FROM custom_bet_entries WHERE custom_bet_id=$1", cbet_id
            )
            notifications = []
            for e in entries:
                if winning_index is None:
                    # возврат
                    await conn.execute(
                        "UPDATE players SET balance=balance+$1, total_won=total_won+$1 WHERE tg_id=$2",
                        e["amount"], e["user_id"],
                    )
                    await conn.execute(
                        "UPDATE custom_bet_entries SET result='refund' WHERE id=$1",
                        e["id"],
                    )
                    notifications.append({
                        "user_id": e["user_id"], "result": "refund",
                        "amount": e["amount"], "winnings": e["amount"], "odds": e["odds"],
                    })
                elif e["option_index"] == winning_index:
                    win = int(e["amount"] * e["odds"])
                    await conn.execute(
                        "UPDATE players SET balance=balance+$1, total_won=total_won+$1 WHERE tg_id=$2",
                        win, e["user_id"],
                    )
                    await conn.execute(
                        "UPDATE custom_bet_entries SET result='win' WHERE id=$1",
                        e["id"],
                    )
                    notifications.append({
                        "user_id": e["user_id"], "result": "win",
                        "amount": e["amount"], "winnings": win, "odds": e["odds"],
                    })
                else:
                    await conn.execute(
                        "UPDATE custom_bet_entries SET result='lose' WHERE id=$1",
                        e["id"],
                    )
                    notifications.append({
                        "user_id": e["user_id"], "result": "lose",
                        "amount": e["amount"], "winnings": 0, "odds": e["odds"],
                    })
            new_status = "cancelled" if winning_index is None else "settled"
            await conn.execute(
                "UPDATE custom_bets SET status=$1, winning_option=$2 WHERE id=$3",
                new_status, winning_index, cbet_id,
            )
    return notifications

# ── promo codes ───────────────────────────────────────────────────────────────

async def create_promo(code: str, coins: int, max_activations: int,
                        created_by: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO promo_codes (code, coins, max_activations, created_by) "
                "VALUES ($1,$2,$3,$4)",
                code, coins, max_activations, created_by,
            )
            return True
        except asyncpg.UniqueViolationError:
            return False

async def activate_promo(user_id: int, code: str) -> tuple[str, int]:
    """Возвращает (статус, количество_коинов).
    Статусы: 'ok', 'not_found', 'exhausted', 'already'."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM promo_codes WHERE code=$1 FOR UPDATE", code
            )
            if not row:
                return "not_found", 0
            if row["used_activations"] >= row["max_activations"]:
                return "exhausted", 0
            already = await conn.fetchrow(
                "SELECT 1 FROM promo_activations WHERE code=$1 AND user_id=$2",
                code, user_id,
            )
            if already:
                return "already", 0
            await conn.execute(
                "INSERT INTO promo_activations (code, user_id) VALUES ($1,$2)",
                code, user_id,
            )
            await conn.execute(
                "UPDATE promo_codes SET used_activations=used_activations+1 WHERE code=$1",
                code,
            )
            await conn.execute(
                "UPDATE players SET balance=balance+$1, total_won=total_won+$1 WHERE tg_id=$2",
                row["coins"], user_id,
            )
            return "ok", row["coins"]

# ── express bets (аккумуляторы) ───────────────────────────────────────────────

MIN_EXPRESS_LEGS = 2
MAX_EXPRESS_LEGS = 10

async def create_express_bet(user_id: int, amount: int, legs: list[dict]) -> int | None:
    """Создаёт экспресс атомарно. legs: list of dict(match_id, match_info, bet_on, odds).
    Возвращает express_id при успехе, None — если недостаточно коинов."""
    total_odds = 1.0
    for leg in legs:
        total_odds *= float(leg["odds"])
    total_odds = round(total_odds, 2)

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT balance AS coins FROM players WHERE tg_id=$1 FOR UPDATE", user_id
            )
            if not row or row["coins"] < amount:
                return None
            await conn.execute(
                "UPDATE players SET balance=balance-$1, total_lost=total_lost+$1 WHERE tg_id=$2", amount, user_id
            )
            exp = await conn.fetchrow(
                "INSERT INTO express_bets (user_id, amount, total_odds) "
                "VALUES ($1,$2,$3) RETURNING id",
                user_id, amount, total_odds,
            )
            exp_id = exp["id"]
            for leg in legs:
                await conn.execute(
                    "INSERT INTO express_bet_legs "
                    "(express_id, match_id, match_info, bet_on, odds) "
                    "VALUES ($1,$2,$3,$4,$5)",
                    exp_id, str(leg["match_id"]), leg["match_info"],
                    leg["bet_on"], float(leg["odds"]),
                )
    return exp_id

async def get_user_express_bets(user_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, amount, total_odds, result, created_at "
            "FROM express_bets WHERE user_id=$1 "
            "ORDER BY created_at DESC LIMIT 10",
            user_id,
        )
        result = []
        for r in rows:
            d = dict(r)
            legs = await conn.fetch(
                "SELECT match_id, match_info, bet_on, odds, result "
                "FROM express_bet_legs WHERE express_id=$1 ORDER BY id",
                d["id"],
            )
            d["legs"] = [dict(l) for l in legs]
            result.append(d)
        return result

async def get_pending_express_legs_by_fixture(fixture_id: int) -> list[dict]:
    """Все pending leg'и экспрессов по данному матчу."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT l.id, l.express_id, l.bet_on, l.odds "
            "FROM express_bet_legs l "
            "JOIN express_bets e ON e.id = l.express_id "
            "WHERE l.match_id=$1 AND l.result='pending' AND e.result='pending'",
            str(fixture_id),
        )
        return [dict(r) for r in rows]

async def update_express_leg_result(leg_id: int, result: str) -> None:
    """Проставляет результат ноги экспресса. Если ногу отменили (void) —
    пересчитывает total_odds экспресса, ДЕЛЯ на коэффициент ноги, чтобы
    «пустая» нога не давала бесплатный win ×1 (что было эксплойтом)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if result == "void":
                leg = await conn.fetchrow(
                    "SELECT express_id, odds, result FROM express_bet_legs "
                    "WHERE id=$1 FOR UPDATE",
                    leg_id,
                )
                if not leg:
                    return
                # Идемпотентность: если уже void, не пересчитываем повторно.
                if leg["result"] != "void":
                    await conn.execute(
                        "UPDATE express_bet_legs SET result='void' WHERE id=$1",
                        leg_id,
                    )
                    leg_odds = float(leg["odds"]) or 1.0
                    if leg_odds > 0:
                        await conn.execute(
                            "UPDATE express_bets "
                            "SET total_odds = ROUND((total_odds / $1)::numeric, 2) "
                            "WHERE id = $2",
                            leg_odds, leg["express_id"],
                        )
                return
            await conn.execute(
                "UPDATE express_bet_legs SET result=$1 WHERE id=$2", result, leg_id
            )

async def try_finalize_express(express_id: int) -> tuple[str, dict] | None:
    """Если все leg'и решены — закрывает экспресс и выплачивает выигрыш.
    Возвращает (result, express_row) при финализации, иначе None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            exp = await conn.fetchrow(
                "SELECT * FROM express_bets WHERE id=$1 FOR UPDATE", express_id
            )
            if not exp or exp["result"] != "pending":
                return None
            legs = await conn.fetch(
                "SELECT result FROM express_bet_legs WHERE express_id=$1", express_id
            )
            if not legs:
                return None
            # Void-ноги — нейтральные (были отменены, total_odds уже снижен).
            has_pending = any(l["result"] == "pending" for l in legs)
            has_lose    = any(l["result"] == "lose"    for l in legs)
            non_void    = [l for l in legs if l["result"] != "void"]
            all_win     = bool(non_void) and all(l["result"] == "win" for l in non_void)
            if has_lose:
                final = "lose"
            elif has_pending:
                return None
            elif not non_void:
                # Все ноги void — возвращаем ставку (refund).
                final = "refund"
            elif all_win:
                final = "win"
            else:
                final = "lose"
            await conn.execute(
                "UPDATE express_bets SET result=$1 WHERE id=$2", final, express_id
            )
            if final == "win":
                winnings = int(exp["amount"] * float(exp["total_odds"]))
                await conn.execute(
                    "UPDATE players SET balance=balance+$1, total_won=total_won+$1 WHERE tg_id=$2",
                    winnings, exp["user_id"],
                )
            elif final == "refund":
                # Возврат: только кредитуем баланс ставкой, без total_won.
                await conn.execute(
                    "UPDATE players SET balance=balance+$1 WHERE tg_id=$2",
                    int(exp["amount"]), exp["user_id"],
                )
            return final, dict(exp)

# ── cappers / tips ────────────────────────────────────────────────────────────

CAPPER_COOLDOWN_HOURS     = 24
CAPPER_QUIZ_PASS          = 8   # правильных ответов из 10 для прохождения
CAPPER_WIN_SHARE_PCT      = 10  # % от выигрыша фолловера → капер
CAPPER_LOSE_SHARE_PCT     = 50  # % от суммы проигрыша фолловера → капер

# quiz: (вопрос, [варианты], индекс правильного)
CAPPER_QUIZ = [
    ("Что такое коэффициент в ставке?",
     ["Множитель возможного выигрыша", "Количество забитых голов", "Время матча"], 0),
    ("Что такое экспресс?",
     ["Связка из нескольких событий в одной ставке",
      "Быстрая ставка за 10 секунд",
      "Ставка только на чемпионов"], 0),
    ("Ставка 100 🪙 при коэф 2.5. Чистый выигрыш, если зашла?",
     ["250 🪙", "100 🪙", "125 🪙"], 0),
    ("Что означает «П1» в ставке на матч?",
     ["Победа хозяев", "Победа гостей", "Ничья"], 0),
    ("Что означает «X» в ставке 1X2?",
     ["Ничья", "Победа хозяев", "Отмена матча"], 0),
    ("Можно ли в один экспресс добавить один и тот же матч дважды?",
     ["Нельзя", "Можно", "Можно только на платном аккаунте"], 0),
    ("В экспрессе из 3 событий одно не зашло. Что будет?",
     ["Экспресс проигран полностью",
      "Выигрыш делится на 3",
      "Будет возврат за 1 ногу"], 0),
    ("Что произойдёт, если поставить сумму больше баланса?",
     ["Ставка не будет принята",
      "Ставка пройдёт, уйдёт в минус",
      "Сумма округлится до баланса"], 0),
    ("Что такое «коэф 1.50»?",
     ["Выигрыш в 1.5 раза больше ставки",
      "На матч осталось 1.5 часа",
      "Размер комиссии"], 0),
    ("Общий коэф экспресса считается как?",
     ["Произведение коэф всех событий",
      "Среднее арифметическое",
      "Сумма коэф всех событий"], 0),
]

async def set_capper(user_id: int, value: bool) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE players SET is_capper=$1 WHERE tg_id=$2", value, user_id
        )

async def set_capper_cooldown(user_id: int, hours: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE players SET capper_test_cooldown_until = NOW() + ($1 || ' hours')::INTERVAL "
            "WHERE tg_id=$2",
            str(hours), user_id,
        )

async def mark_capper_test_taken(user_id: int) -> None:
    """Помечает, что игрок прошёл тест хотя бы один раз — больше предлагать не будем."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE players SET capper_test_taken = TRUE WHERE tg_id=$1",
            user_id,
        )

async def create_tip(capper_id: int, bet_type: str, ref_id: int,
                     description: str | None = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "INSERT INTO capper_tips (capper_id, bet_type, ref_id, description) "
            "VALUES ($1,$2,$3,$4) RETURNING id",
            capper_id, bet_type, ref_id, description,
        )
        return int(r["id"])

async def get_tip(tip_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT t.*, p.username AS capper_username "
            "FROM capper_tips t LEFT JOIN players p ON p.tg_id=t.capper_id "
            "WHERE t.id=$1",
            tip_id,
        )
        return dict(r) if r else None

async def list_recent_tips(limit: int = 15) -> list[dict]:
    """Возвращает свежие типы, у которых базовая ставка/экспресс ещё pending."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                """
                SELECT t.*, p.username AS capper_username
                FROM capper_tips t
                LEFT JOIN players p ON p.tg_id = t.capper_id
                WHERE
                    (t.bet_type = 'single' AND EXISTS (
                        SELECT 1 FROM bets b
                        WHERE b.id = t.ref_id AND b.result = 'pending'
                    ))
                    OR
                    (t.bet_type = 'express' AND EXISTS (
                        SELECT 1 FROM express_bets e
                        WHERE e.id = t.ref_id AND e.result = 'pending'
                    ))
                ORDER BY t.created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.exception("list_recent_tips failed: %s", e)
            return []

async def get_tip_summary(tip: dict) -> str:
    """Формирует текст тип-картинки: ставка/экспресс + детали."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if tip["bet_type"] == "single":
            row = await conn.fetchrow(
                "SELECT match_id, match_info, bet_on, odds, amount "
                "FROM bets WHERE id=$1",
                tip["ref_id"],
            )
            if not row:
                return "⚠️ Ставка удалена."
            label = await _bet_display_label(row["bet_on"], row["match_id"])
            return (
                f"⚽ <b>{row['match_info']}</b>\n"
                f"📌 {label}  ×{row['odds']}"
            )
        elif tip["bet_type"] == "express":
            exp = await conn.fetchrow(
                "SELECT amount, total_odds FROM express_bets WHERE id=$1",
                tip["ref_id"],
            )
            if not exp:
                return "⚠️ Экспресс удалён."
            legs = await conn.fetch(
                "SELECT match_id, match_info, bet_on, odds FROM express_bet_legs "
                "WHERE express_id=$1 ORDER BY id",
                tip["ref_id"],
            )
            lines = [f"🔗 <b>Экспресс</b> · общий коэф ×{exp['total_odds']}"]
            for i, l in enumerate(legs, 1):
                label = await _bet_display_label(l["bet_on"], l["match_id"])
                lines.append(
                    f"{i}. {l['match_info']}  ·  {label} ×{l['odds']}"
                )
            return "\n".join(lines)
        return "?"

async def get_tip_for_bet(bet_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT * FROM capper_tips WHERE bet_type='single' AND ref_id=$1",
            bet_id,
        )
        return dict(r) if r else None

async def get_tip_for_express(express_id: int) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow(
            "SELECT * FROM capper_tips WHERE bet_type='express' AND ref_id=$1",
            express_id,
        )
        return dict(r) if r else None

async def reward_capper_for_bet(bet_row: dict, winnings: int) -> tuple[int, int] | None:
    """Начисляет каперу долю за ставку фолловера. Возвращает (capper_id, sum) или None."""
    if not bet_row.get("via_tip_id"):
        return None
    tip = await get_tip(bet_row["via_tip_id"])
    if not tip:
        return None
    capper_id = tip["capper_id"]
    # фолловеру самому себе ничего не платим
    if capper_id == bet_row.get("user_id"):
        return None
    if bet_row.get("result") == "win":
        share = int(winnings * CAPPER_WIN_SHARE_PCT / 100)
    elif bet_row.get("result") == "lose":
        share = int(bet_row["amount"] * CAPPER_LOSE_SHARE_PCT / 100)
    else:
        return None
    if share <= 0:
        return None
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE players SET balance=balance+$1, total_won=total_won+$1 WHERE tg_id=$2",
            share, capper_id,
        )
    return capper_id, share

# ════════════════════════════════════════════════════════════
#  SSTATS.NET API
# ════════════════════════════════════════════════════════════

async def sstats_get(path: str, params: dict = None) -> dict:
    """GET запрос к SStats.net API."""
    url     = f"{SSTATS_BASE}/{path.lstrip('/')}"
    headers = {"Content-Type": "application/json"}
    if SSTATS_KEY:
        headers["X-API-Key"] = SSTATS_KEY
        if params is None:
            params = {}
        params["api_key"] = SSTATS_KEY  # некоторые API принимают ключ и как параметр

    p = params or {}
    timeout = aiohttp.ClientTimeout(total=15)
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, params=p) as resp:
                    if resp.status == 429:
                        wait = 3 * (attempt + 1)
                        logger.warning(f"SStats 429: {path}, retry in {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    if resp.status != 200:
                        logger.error(f"SStats {resp.status}: {path}")
                        return {}
                    data = await resp.json()
                    if str(data.get("status", "")).upper() != "OK":
                        logger.error(f"SStats bad status: {data.get('status')}")
                        return {}
                    return data
        except Exception as e:
            logger.error(f"SStats request {path}: {e}")
            return {}
    return {}


def _fallback_odds(home: str, away: str) -> tuple:
    """Псевдослучайные коэффициенты 1X2 — запасной вариант."""
    seed   = int(hashlib.md5(f"{home}{away}".encode()).hexdigest(), 16) % 100
    spread = (seed - 50) / 100
    h = round(max(1.30, 2.0 - spread * 0.6), 2)
    d = round(max(2.50, 3.2 + spread * 0.3), 2)
    a = round(max(1.30, 2.5 + spread * 0.5), 2)
    return h, d, a


def _fallback_ou(home: str, away: str, line: float) -> tuple:
    """Псевдослучайные коэффициенты Over/Under X. Возвращает (over, under)."""
    seed = int(hashlib.md5(f"{home}{away}ou{line}".encode()).hexdigest(), 16) % 100
    bias = (seed - 50) / 100  # [-0.5 .. 0.5]
    if line == 1.5:
        o = round(max(1.20, 1.35 + bias * 0.15), 2)
        u = round(max(2.50, 3.00 + bias * 0.6), 2)
    elif line == 2.5:
        o = round(max(1.55, 2.00 + bias * 0.35), 2)
        u = round(max(1.55, 1.80 - bias * 0.35), 2)
    elif line == 3.5:
        o = round(max(2.70, 3.80 + bias * 0.9), 2)
        u = round(max(1.15, 1.28 + bias * 0.07), 2)
    else:
        o, u = 2.0, 1.8
    return o, u


def _fallback_btts(home: str, away: str) -> tuple:
    """Псевдослучайные коэффициенты BTTS (Yes, No)."""
    seed = int(hashlib.md5(f"{home}{away}btts".encode()).hexdigest(), 16) % 100
    bias = (seed - 50) / 100
    y = round(max(1.50, 1.80 + bias * 0.25), 2)
    n = round(max(1.50, 1.90 - bias * 0.25), 2)
    return y, n


def _fallback_all(home: str, away: str) -> dict:
    """Полный набор fallback-коэффициентов по всем рынкам."""
    h, d, a = _fallback_odds(home, away)
    out = {"home": h, "draw": d, "away": a}
    for line in OU_LINES:
        o, u = _fallback_ou(home, away, line)
        out[OU_OVER_KEY[line]] = o
        out[OU_UNDER_KEY[line]] = u
    y, n = _fallback_btts(home, away)
    out["bttsy"] = y
    out["bttsn"] = n
    # Угловые (9.5) и ЖК (4.5) — усреднённые лиги
    out["c95o"], out["c95u"] = 1.85, 1.85
    out["yc45o"], out["yc45u"] = 1.90, 1.80
    # Двойной шанс — выводится из 1X2 (правило 1/x ≈ вероятность)
    try:
        p_h, p_d, p_a = 1 / h, 1 / d, 1 / a
        norm = p_h + p_d + p_a
        p_h, p_d, p_a = p_h / norm, p_d / norm, p_a / norm
        out["dc1x"] = round(max(1.10, 0.95 / (p_h + p_d)), 2)
        out["dc12"] = round(max(1.10, 0.95 / (p_h + p_a)), 2)
        out["dcx2"] = round(max(1.10, 0.95 / (p_d + p_a)), 2)
    except Exception:
        out["dc1x"] = out["dc12"] = out["dcx2"] = 1.40
    # Точный счёт — очень грубая оценка (редкие исходы — больше кэф)
    out["cs10"] = 8.5
    out["cs21"] = 9.0
    out["cs20"] = 10.0
    out["cs00"] = 10.0
    out["cs11"] = 6.5
    out["cs22"] = 15.0
    return out


def _safe_float(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 1.0 else None


# Точный счёт: "1:0" → "cs10" и т.п.
_CS_NAME_TO_KEY = {f"{h}:{a}": k for k, (h, a) in CORRECT_SCORES.items()}


def _extract_markets(bk: dict) -> tuple[dict, list]:
    """Вытаскивает из ответа букмекера коэффициенты всех поддерживаемых рынков
    и (если есть) список голлеров «Anytime Goal Scorer».

    Возвращает кортеж `(odds_dict, scorers_list)`:
      * odds_dict  — {bet_on_code: odds_value}
      * scorers_list — [{player_id, player_name, odds_any}, …]
        (id берём от SStats, если нет — хэш от имени).

    Поддерживаемые рынки SStats:
      * Match Winner (1X2)
      * Goals Over/Under (линии 1.5 / 2.5 / 3.5)
      * Both Teams Score (Да/Нет)
      * Corners Over Under (только линия 9.5)
      * Cards Over/Under (линия 4.5)
      * Double Chance
      * Correct Score (топ-6)
      * Anytime Goal Scorer
    """
    out: dict = {}
    scorers: list = []
    markets = bk.get("odds") or []
    for market in markets:
        mname = str(market.get("marketName") or "").lower().strip()
        choices = market.get("odds") or []

        # 1X2 ─────────────────────────────────────────────────
        if ("match winner" in mname) or (mname in ("1x2", "full time result")):
            for c in choices:
                name = str(c.get("name") or "").lower().strip()
                v = _safe_float(c.get("value") or c.get("odds"))
                if v is None:
                    continue
                if name == "home": out["home"] = round(v, 2)
                elif name == "draw": out["draw"] = round(v, 2)
                elif name == "away": out["away"] = round(v, 2)

        # Тоталы голов ───────────────────────────────────────
        elif mname == "goals over/under":
            for c in choices:
                name = str(c.get("name") or "").strip()
                v = _safe_float(c.get("value") or c.get("odds"))
                if v is None:
                    continue
                low = name.lower()
                for line in OU_LINES:
                    if low == f"over {line}":
                        out[OU_OVER_KEY[line]] = round(v, 2)
                    elif low == f"under {line}":
                        out[OU_UNDER_KEY[line]] = round(v, 2)

        # Обе забьют ──────────────────────────────────────────
        elif mname in ("both teams score", "both teams to score", "btts"):
            for c in choices:
                name = str(c.get("name") or "").lower().strip()
                v = _safe_float(c.get("value") or c.get("odds"))
                if v is None:
                    continue
                if name in ("yes", "да"):   out["bttsy"] = round(v, 2)
                elif name in ("no", "нет"): out["bttsn"] = round(v, 2)

        # Угловые ТБ/ТМ 9.5 ──────────────────────────────────
        elif mname in ("corners over under", "corners over/under"):
            for c in choices:
                name = str(c.get("name") or "").lower().strip()
                v = _safe_float(c.get("value") or c.get("odds"))
                if v is None:
                    continue
                if name == "over 9.5":  out["c95o"] = round(v, 2)
                elif name == "under 9.5": out["c95u"] = round(v, 2)

        # ЖК ТБ/ТМ 4.5 ───────────────────────────────────────
        elif mname == "cards over/under":
            for c in choices:
                name = str(c.get("name") or "").lower().strip()
                v = _safe_float(c.get("value") or c.get("odds"))
                if v is None:
                    continue
                if name == "over 4.5":  out["yc45o"] = round(v, 2)
                elif name == "under 4.5": out["yc45u"] = round(v, 2)

        # Двойной шанс ───────────────────────────────────────
        elif mname == "double chance":
            for c in choices:
                name = str(c.get("name") or "").lower().strip().replace(" ", "")
                v = _safe_float(c.get("value") or c.get("odds"))
                if v is None:
                    continue
                if name in ("home/draw", "1x"):       out["dc1x"] = round(v, 2)
                elif name in ("home/away", "12"):     out["dc12"] = round(v, 2)
                elif name in ("draw/away", "x2"):     out["dcx2"] = round(v, 2)

        # Точный счёт (основное время) — у SStats market называется "Exact Score"
        elif mname in ("exact score", "correct score"):
            for c in choices:
                name = str(c.get("name") or "").strip()
                v = _safe_float(c.get("value") or c.get("odds"))
                if v is None:
                    continue
                key = _CS_NAME_TO_KEY.get(name)
                if key:
                    out[key] = round(v, 2)

        # Голлеры — Anytime ──────────────────────────────────
        elif mname == "anytime goal scorer":
            for c in choices:
                pname = str(c.get("name") or "").strip()
                if not pname or pname.lower() == "no goalscorer":
                    continue
                v = _safe_float(c.get("value") or c.get("odds"))
                if v is None:
                    continue
                pid = c.get("playerId") or c.get("player_id")
                if not pid:
                    # SStats не всегда отдаёт playerId — делаем стабильный хэш
                    pid = abs(hash(pname)) % 10_000_000
                scorers.append({
                    "player_id":   int(pid),
                    "player_name": pname[:60],
                    "odds_any":    round(v, 2),
                })

    return out, scorers


# Приоритет букмекеров для выбора коэффициентов (SStats bookmakerId).
# Bet365=8, 10Bet=1, Marathonbet=2, Unibet=16 — у всех есть нужные рынки.
_BOOKMAKER_PREFERENCE = (8, 1, 2, 16)


async def fetch_odds_for_game(game_id: int) -> tuple[dict, list]:
    """Получить доматчевые коэффициенты всех поддерживаемых рынков + голлеров.

    GET /Odds/{gameId}. Возвращает `(odds_dict, scorers_list)`. Если рынок у
    предпочтённого букмекера отсутствует — дозаполняется из других
    (через `setdefault`). Голлеры берутся от первого букмекера, у кого их
    хотя бы несколько (обычно Bet365).
    """
    data = await sstats_get(f"Odds/{game_id}")
    if not data:
        return {}, []

    raw = data.get("data", [])
    odds_list = raw if isinstance(raw, list) else [raw] if raw else []
    if not odds_list:
        return {}, []

    # Упорядочим букмекеров по приоритету.
    def pref(bk: dict) -> int:
        bk_id = bk.get("bookmaker_id") or bk.get("bookmakerId") or 0
        try:
            return _BOOKMAKER_PREFERENCE.index(bk_id)
        except ValueError:
            return 999

    ordered = sorted(odds_list, key=pref)

    merged:     dict = {}
    all_scorers: list = []
    for bk in ordered:
        markets, scorers = _extract_markets(bk)
        for k, v in markets.items():
            merged.setdefault(k, v)
        if scorers and not all_scorers:
            all_scorers = scorers  # первый букмекер с голлерами выигрывает
    return merged, all_scorers


def _odds_from_row(f: dict) -> dict:
    """Собирает полный словарь коэффициентов из строки cached_fixtures с
    автоматическим fallback'ом для отсутствующих рынков.

    Также возвращает флаг `real_1x2` (есть ли в БД реальный 1X2) — для
    вывода «реальные / расчётные» в UI.
    """
    home, away = f.get("home_team", "?"), f.get("away_team", "?")
    fb = _fallback_all(home, away)
    out = {}
    for code, col in ODDS_COL.items():
        v = f.get(col)
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        out[code] = v if (v and v > 1.0) else fb[code]
    return out


def _bet_result(bet_on: str, result: dict) -> str:
    """Рассчитывает результат ставки по итогам матча.

    `result` — dict из `fetch_game_result`: home_goals, away_goals,
    total_corners, total_yellows, scorer_ids, scorer_names.

    Возвращает `win` / `lose` / `void` (последнее — если данных для расчёта
    нет, напр. статистика угловых не пришла; ставка будет возвращена).
    """
    hg = int(result.get("home_goals", 0))
    ag = int(result.get("away_goals", 0))
    total = hg + ag

    # 1X2
    if bet_on == "home":  return "win" if hg >  ag else "lose"
    if bet_on == "away":  return "win" if ag >  hg else "lose"
    if bet_on == "draw":  return "win" if hg == ag else "lose"

    # Тоталы голов
    if bet_on == "o15":   return "win" if total >  1.5 else "lose"
    if bet_on == "u15":   return "win" if total <  1.5 else "lose"
    if bet_on == "o25":   return "win" if total >  2.5 else "lose"
    if bet_on == "u25":   return "win" if total <  2.5 else "lose"
    if bet_on == "o35":   return "win" if total >  3.5 else "lose"
    if bet_on == "u35":   return "win" if total <  3.5 else "lose"

    # Обе забьют
    if bet_on == "bttsy": return "win" if (hg > 0 and ag > 0) else "lose"
    if bet_on == "bttsn": return "win" if (hg == 0 or ag == 0) else "lose"

    # Двойной шанс
    if bet_on == "dc1x":  return "win" if hg >= ag else "lose"
    if bet_on == "dc12":  return "win" if hg != ag else "lose"
    if bet_on == "dcx2":  return "win" if ag >= hg else "lose"

    # Точный счёт (топ-6)
    if bet_on in CORRECT_SCORES:
        want_h, want_a = CORRECT_SCORES[bet_on]
        return "win" if (hg == want_h and ag == want_a) else "lose"

    # Угловые ТБ/ТМ 9.5
    if bet_on in ("c95o", "c95u"):
        tc = result.get("total_corners")
        if tc is None:
            return "void"
        if bet_on == "c95o": return "win" if tc > 9.5 else "lose"
        return "win" if tc < 9.5 else "lose"

    # Жёлтые карточки ТБ/ТМ 4.5
    if bet_on in ("yc45o", "yc45u"):
        ty = result.get("total_yellows")
        if ty is None:
            return "void"
        if bet_on == "yc45o": return "win" if ty > 4.5 else "lose"
        return "win" if ty < 4.5 else "lose"

    # Голлеры Anytime — «gsa<player_id>»
    if bet_on.startswith(GOAL_SCORER_PREFIX):
        try:
            pid = int(bet_on[len(GOAL_SCORER_PREFIX):])
        except ValueError:
            logger.warning(f"_bet_result: bad scorer bet_on={bet_on!r}")
            return "lose"
        if pid in (result.get("scorer_ids") or set()):
            return "win"
        return "lose"

    logger.warning(f"_bet_result: unknown bet_on={bet_on!r}")
    return "lose"


def _is_known_bet(bet_on: str) -> bool:
    """Валидирует код ставки — для UI и handler-гейтов."""
    if bet_on in BET_LABELS:
        return True
    if bet_on.startswith(GOAL_SCORER_PREFIX):
        tail = bet_on[len(GOAL_SCORER_PREFIX):]
        return tail.isdigit() and 1 <= len(tail) <= 12
    return False


async def _bet_display_label(bet_on: str, fixture_id: int | None = None) -> str:
    """Человеческая подпись для ставки.

    Для голлеров (gsa<pid>) пытаемся найти игрока в fixture_scorers —
    показываем «Автор гола: <имя>». Иначе — лейбл из BET_LABELS.

    Если в текущем матче игрок не найден (фикстура давно обновилась и
    голлеры переписаны без него) — ищем по player_id во всех матчах,
    т.к. id игрока — глобальный SStats.
    """
    if bet_on in BET_LABELS:
        return BET_LABELS[bet_on]
    if bet_on.startswith(GOAL_SCORER_PREFIX):
        tail = bet_on[len(GOAL_SCORER_PREFIX):]
        if tail.isdigit():
            pid = int(tail)
            # 1) В контексте конкретного матча.
            if fixture_id is not None:
                try:
                    s = await get_fixture_scorer(fixture_id, pid)
                    if s:
                        return f"Автор гола: {s['player_name']}"
                except Exception:
                    pass
            # 2) Во всех матчах (любая запись подойдёт — id уникальный).
            try:
                s = await find_scorer_by_player_id(pid)
                if s:
                    return f"Автор гола: {s['player_name']}"
            except Exception:
                pass
        return f"Автор гола (#{tail})"
    return bet_on


async def fetch_upcoming_fixtures(force=False) -> int:
    """
    Загружает предстоящие матчи из SStats.net + коэффициенты.
    Кэш 3 дня, force=True для принудительного обновления.
    """
    # Если уже идёт обновление — ждём его завершения, не запускаем второе
    if _update_lock.locked():
        logger.info("Update already in progress, skipping")
        fixtures = await load_fixtures()
        return len(fixtures)

    async with _update_lock:
        return await _do_fetch_upcoming_fixtures(force)


async def _do_fetch_upcoming_fixtures(force=False) -> int:
    last_fetch = await get_last_fetch_time()
    now        = datetime.now(timezone.utc)
    # Кэш фикстур и коэффициентов: 6 ч (раньше было 3 дня — из-за этого
    # коэффициенты могли быть устаревшими по 3 суток). 21 600 с достаточно
    # чтобы не упереться в API rate-limit, но коэффициенты обновляются
    # минимум 4 раза в сутки.
    needs_update = (
        force or
        last_fetch is None or
        (now - last_fetch.replace(tzinfo=timezone.utc)).total_seconds() > 21_600
    )

    if not needs_update:
        fixtures = await load_fixtures()
        logger.info(f"Using cached fixtures: {len(fixtures)}")
        return len(fixtures)

    logger.info("Fetching fixtures from SStats.net...")

    all_fixtures = []
    now_ts    = int(now.timestamp())
    week_from = now.strftime("%Y-%m-%d")
    week_to   = (now + timedelta(days=7)).strftime("%Y-%m-%d")

    for league_id, league_name in LEAGUES.items():
        try:
            # Матчи на ближайшие 7 дней
            data = await sstats_get("Games/list", {
                "leagueid": league_id,
                "from": week_from,
                "to":   week_to,
                "limit": 20,
            })
            games = data.get("data", [])
            logger.info(f"League {league_id} ({league_name}): {len(games)} games")

            for g in games:
                try:
                    gid = g.get("id")
                    if not gid:
                        continue

                    # Время матча — реальная структура SStats: dateUtc (unix timestamp)
                    start_ts_raw = g.get("dateUtc") or g.get("timestamp")
                    date_str     = g.get("date") or g.get("datetime") or ""
                    try:
                        if start_ts_raw:
                            dt = datetime.fromtimestamp(int(start_ts_raw), tz=timezone.utc)
                        elif date_str:
                            dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                        else:
                            dt = now + timedelta(days=1)
                    except Exception:
                        dt = now + timedelta(days=1)

                    # Пропускаем уже начавшиеся
                    if dt.timestamp() < now_ts:
                        continue

                    # Команды — реальная структура: homeTeam: {id, name, ...}
                    home = g.get("homeTeam") or g.get("home") or {}
                    away = g.get("awayTeam") or g.get("away") or {}
                    if isinstance(home, dict):
                        home = home.get("name") or home.get("title") or "?"
                    if isinstance(away, dict):
                        away = away.get("name") or away.get("title") or "?"

                    base = {
                        "fixture_id": gid,
                        "league_id":  league_id,
                        "league_name": league_name,
                        "home":  str(home),
                        "away":  str(away),
                        "date":  dt.strftime("%Y-%m-%d"),
                        "timestamp": int(dt.timestamp()),
                    }
                    # Заготовки под все коэффициенты
                    for code in ODDS_COL:
                        base[ODDS_COL[code]] = None
                    all_fixtures.append(base)
                except Exception as e:
                    logger.error(f"parse game: {e}")

            await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"league {league_id}: {e}")

    if not all_fixtures:
        logger.warning("No fixtures from SStats.net")
        return 0

    # Получаем коэффициенты. Для каждого матча один запрос /Odds/{id},
    # который сразу отдаёт 1X2, тоталы, BTTS, угловые, карточки, DC, CS и голлеров.
    odds_ok_1x2    = 0
    odds_ok_ou25   = 0
    odds_ok_btts   = 0
    odds_ok_corners = 0
    scorers_total   = 0
    fixtures_with_scorers = 0
    for f in all_fixtures:
        await asyncio.sleep(1.2)
        markets, scorers = await fetch_odds_for_game(f["fixture_id"])
        fb = _fallback_all(f["home"], f["away"])
        for code, col in ODDS_COL.items():
            f[col] = markets.get(code) if markets.get(code) else fb[code]
        f["_scorers"] = scorers  # отложим до save
        if markets.get("home") and markets.get("draw") and markets.get("away"):
            odds_ok_1x2 += 1
        if markets.get("o25") and markets.get("u25"):
            odds_ok_ou25 += 1
        if markets.get("bttsy") and markets.get("bttsn"):
            odds_ok_btts += 1
        if markets.get("c95o") or markets.get("c95u"):
            odds_ok_corners += 1
        if scorers:
            fixtures_with_scorers += 1
            scorers_total += len(scorers)

    total = len(all_fixtures)
    logger.info(
        f"Fixtures: {total} | Real 1X2: {odds_ok_1x2} | "
        f"ТБ/ТМ 2.5: {odds_ok_ou25} | BTTS: {odds_ok_btts} | "
        f"Corners: {odds_ok_corners} | Scorers: {scorers_total} "
        f"({fixtures_with_scorers} матчей)"
    )
    await save_fixtures(all_fixtures)
    # Сохраняем голлеров отдельно, после save_fixtures (FK целостность не важна,
    # но порядок для логов читабельнее).
    for f in all_fixtures:
        scorers = f.pop("_scorers", [])
        if scorers:
            try:
                await save_fixture_scorers(f["fixture_id"], scorers)
            except Exception as e:
                logger.warning(f"save_fixture_scorers({f['fixture_id']}): {e}")
    return total


async def fetch_game_result(game_id: int) -> dict | None:
    """GET /Games/{id} — результат матча + статистика + события.

    Возвращает dict c ключами:
      * home_goals, away_goals          — финальный счёт
      * total_corners                    — cornerKicksHome + cornerKicksAway (или None)
      * total_yellows                    — yellowCardsHome + yellowCardsAway (или None)
      * home_team_id, away_team_id       — team.id для атрибуции голов
      * scorer_ids                       — set() player_id, забивших хотя бы раз
      * scorer_names                     — set() нижнекейсных имён (резерв если pid=None)
    Либо None, если матч не закончен / нет счёта.
    """
    data = await sstats_get(f"Games/{game_id}")
    if not data:
        return None

    # Реальная структура /Games/{id}:
    #   data.data = {"game": {...}, "statistics": {...}, "events": [...]}
    inner = data.get("data") or {}
    if isinstance(inner, list):
        inner = inner[0] if inner else {}

    game   = inner.get("game") or inner  # fallback на плоскую структуру
    stats  = inner.get("statistics") or {}
    events = inner.get("events") or []

    # 🛡 Жёсткая проверка: матч действительно ЗАКОНЧЕН.
    #
    # Раньше использовался слабый чек по `homeFTResult or homeResult`, и
    # ставки иногда закрывались до конца матча — SStats может вернуть
    # `homeFTResult` равный live-счёту в момент перерыва или приостановки.
    # Теперь требуется чтобы оба условия были истинны:
    # 1) Статус матча — один из явно «закрытых» (FT/Ended/Finished/AET/PEN).
    # 2) Поля `homeFTResult/awayFTResult` НЕ None. Live-счёт `homeResult`
    #    больше не используется как fallback.
    status_name = str(game.get("statusName") or "").strip().lower()
    raw_status = game.get("status")
    status_code = raw_status if isinstance(raw_status, int) else 0
    if isinstance(raw_status, str) and not status_name:
        status_name = raw_status.strip().lower()

    _FINISHED_NAMES = {
        "ft", "full time", "fulltime", "finished", "ended",
        "after extra time", "aet", "after penalties", "ap", "pen",
        "penalties",
    }
    finished_by_name = (
        status_name in _FINISHED_NAMES
        or "finish" in status_name
        or "ended" in status_name
    )
    # SStats обычно использует status_code: 1=NS, 2=Live, 3=FT/Ended.
    finished_by_code = status_code == 3

    home_score = game.get("homeFTResult")
    away_score = game.get("awayFTResult")
    finished = (finished_by_name or finished_by_code) and \
        home_score is not None and away_score is not None
    if not finished:
        return None

    # Тотал угловых/ЖК (могут быть None если SStats не отдал статистику)
    ch = stats.get("cornerKicksHome")
    ca = stats.get("cornerKicksAway")
    total_corners = (int(ch) + int(ca)) if ch is not None and ca is not None else None

    yh = stats.get("yellowCardsHome")
    ya = stats.get("yellowCardsAway")
    total_yellows = (int(yh) + int(ya)) if yh is not None and ya is not None else None

    home_team_id = (game.get("homeTeam") or {}).get("id")
    away_team_id = (game.get("awayTeam") or {}).get("id")

    # Собираем голлеров. type=1 — Goal/Penalty/Own Goal. Автогол не засчитываем
    # забившему (пас идёт в actual scorer, но у SStats в "Own Goal" player = кто
    # забил сам в свои — его anytime goalscorer SStats обычно НЕ считает).
    scorer_ids: set = set()
    scorer_names: set = set()
    for ev in events:
        if ev.get("type") != 1:
            continue
        ename = str(ev.get("name") or "").lower()
        if ename not in ("normal goal", "penalty", "header", "free kick"):
            # Own Goal — пропускаем (букмекеры не засчитывают автогол в anytime)
            continue
        p = ev.get("player") or {}
        pid = p.get("id")
        pname = (p.get("name") or "").strip().lower()
        if pid:
            scorer_ids.add(int(pid))
        if pname:
            scorer_names.add(pname)

    return {
        "home_goals":    int(home_score),
        "away_goals":    int(away_score),
        "total_corners": total_corners,
        "total_yellows": total_yellows,
        "home_team_id":  home_team_id,
        "away_team_id":  away_team_id,
        "scorer_ids":    scorer_ids,
        "scorer_names":  scorer_names,
    }

# ════════════════════════════════════════════════════════════
#  JOBS
# ════════════════════════════════════════════════════════════

async def bets_settle_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Каждые 2ч проверяет завершённые матчи и рассчитывает ставки."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        pending = await conn.fetch(
            "SELECT DISTINCT match_id FROM ("
            "  SELECT match_id FROM bets WHERE result='pending'"
            "  UNION "
            "  SELECT match_id FROM express_bet_legs WHERE result='pending'"
            ") t"
        )
        # Сразу подтянем `start_ts` всех нужных фикстур, чтобы не дёргать
        # БД в цикле и иметь страховочный буфер от ранних закрытий.
        match_ids = [str(r["match_id"]) for r in pending]
        start_rows = []
        if match_ids:
            start_rows = await conn.fetch(
                "SELECT fixture_id, start_ts FROM cached_fixtures "
                "WHERE fixture_id = ANY($1::bigint[])",
                [int(m) for m in match_ids if str(m).isdigit()],
            )
    start_ts_by_fix: dict[int, int] = {
        int(r["fixture_id"]): int(r["start_ts"]) for r in start_rows
    }
    now_ts = int(datetime.now(timezone.utc).timestamp())
    # Минимальная длительность матча с учётом перерыва и доб. времени —
    # 110 минут. Раньше 110 минут = ставку точно НЕ закрываем, даже если
    # SStats внезапно отдаст «FT». Это защищает от багов API
    # (преждевременный FT-флаг при паузе/задержке).
    _MIN_MATCH_SECONDS = 110 * 60

    for row in pending:
        try:
            fixture_id = int(row["match_id"])
        except (TypeError, ValueError):
            continue
        # Раннее закрытие: если матч начался <110 мин назад — ждём.
        ks = start_ts_by_fix.get(fixture_id)
        if ks is not None and now_ts < ks + _MIN_MATCH_SECONDS:
            continue
        await asyncio.sleep(1)
        result = await fetch_game_result(fixture_id)
        if not result:
            continue

        hg, ag = result["home_goals"], result["away_goals"]
        bets   = await get_pending_by_fixture(fixture_id)

        # --- экспресс-leg'и по этому матчу ---
        express_ids_touched: set[int] = set()
        express_legs = await get_pending_express_legs_by_fixture(fixture_id)
        for leg in express_legs:
            leg_result = _bet_result(leg["bet_on"], result)
            # Void-нога больше НЕ превращается в «win ×1» — это была дыра
            # (эксплойт: бесплатный выигрыш при отмене матча). Теперь void
            # вычитается из total_odds экспресса (см. update_express_leg_result).
            await update_express_leg_result(leg["id"], leg_result)
            express_ids_touched.add(leg["express_id"])

        for exp_id in express_ids_touched:
            finalize = await try_finalize_express(exp_id)
            if not finalize:
                continue
            final, exp = finalize
            uid = exp["user_id"]
            # Перечитаем total_odds из БД — он мог быть пересчитан из-за void-ног.
            try:
                _p = await get_pool()
                async with _p.acquire() as _c:
                    _row = await _c.fetchrow(
                        "SELECT total_odds FROM express_bets WHERE id=$1", exp["id"]
                    )
                if _row:
                    exp = dict(exp)
                    exp["total_odds"] = _row["total_odds"]
            except Exception:
                pass
            if final == "win":
                winnings = int(exp["amount"] * float(exp["total_odds"]))
                text = (
                    f"🔗 <b>Экспресс сыграл!</b>\n\n"
                    f"💰 Ставка: <b>{fmt_coins(exp['amount'])}</b>  "
                    f"x{exp['total_odds']}\n"
                    f"🎉 Выигрыш: <b>+{fmt_coins(winnings)}</b>"
                )
            elif final == "refund":
                winnings = int(exp["amount"])
                text = (
                    f"🔗 <b>Экспресс возвращён</b>\n\n"
                    f"Все события отменены — ставка возвращается.\n"
                    f"💰 Возврат: <b>+{fmt_coins(winnings)}</b>"
                )
            else:
                winnings = 0
                text = (
                    f"🔗 <b>Экспресс не сыграл</b>\n\n"
                    f"💸 Потеряно: <b>{fmt_coins(exp['amount'])}</b>"
                )
            try:
                await ctx.bot.send_message(uid, text, parse_mode="HTML")
            except Exception:
                pass
            # --- капер-реверсы на экспресс ---
            reward = await reward_capper_for_bet(
                {
                    "via_tip_id": exp.get("via_tip_id"),
                    "user_id":    exp["user_id"],
                    "amount":     exp["amount"],
                    "result":     final,
                },
                winnings,
            )
            if reward:
                c_id, c_sum = reward
                try:
                    await ctx.bot.send_message(
                        c_id,
                        f"🎯 <b>Доход капера</b>\n"
                        f"Экспресс #{exp['id']} "
                        + ("сыграл" if final == "win" else "не сыграл") + ".\n"
                        f"Начислено: <b>+{fmt_coins(c_sum)}</b>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        for bet in bets:
            outcome = _bet_result(bet["bet_on"], result)
            reward = None
            label = await _bet_display_label(bet["bet_on"], fixture_id)
            if outcome == "win":
                winnings = int(bet["amount"] * bet["odds"])
                await settle_bet(bet["id"], "win", winnings, bet["user_id"])
                try:
                    await ctx.bot.send_message(
                        bet["user_id"],
                        f"⚽ <b>Ставка сыграла!</b>\n\n"
                        f"🏆 {bet['match_info']}\n"
                        f"📌 {label}\n"
                        f"💰 Ставка: <b>{fmt_coins(bet['amount'])}</b>  x{bet['odds']}\n"
                        f"🎉 Выигрыш: <b>+{fmt_coins(winnings)}</b>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                bet_row_final = {**bet, "result": "win"}
                reward = await reward_capper_for_bet(bet_row_final, winnings)
            elif outcome == "void":
                # Возврат: отдаём сумму ставки обратно, не считаем ни win ни lose.
                await settle_bet(bet["id"], "void", int(bet["amount"]), bet["user_id"])
                try:
                    await ctx.bot.send_message(
                        bet["user_id"],
                        f"↩️ <b>Ставка возвращена</b>\n\n"
                        f"🏆 {bet['match_info']}\n"
                        f"📌 {label}\n"
                        f"💰 Возврат: <b>{fmt_coins(int(bet['amount']))}</b>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            else:
                await settle_bet(bet["id"], "lose", 0, bet["user_id"])
                try:
                    await ctx.bot.send_message(
                        bet["user_id"],
                        f"⚽ <b>Ставка не сыграла</b>\n\n"
                        f"🏆 {bet['match_info']}\n"
                        f"💸 Потеряно: <b>{fmt_coins(bet['amount'])}</b>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
                bet_row_final = {**bet, "result": "lose"}
                reward = await reward_capper_for_bet(bet_row_final, 0)
            if reward:
                c_id, c_sum = reward
                try:
                    await ctx.bot.send_message(
                        c_id,
                        f"🎯 <b>Доход капера</b>\n"
                        f"{bet['match_info']} — "
                        + ("🎉 сыграла" if outcome == "win" else "❌ не сыграла") + ".\n"
                        f"Начислено: <b>+{fmt_coins(c_sum)}</b>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        logger.info(f"Settled {fixture_id}: {hg}:{ag}, {len(bets)} bets")
        await update_fixture_result(fixture_id, hg, ag)


async def fixtures_refresh_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Раз в 3 дня обновляет матчи."""
    logger.info("Scheduled refresh...")
    await fetch_upcoming_fixtures(force=True)

# ════════════════════════════════════════════════════════════
#  KEYBOARDS
# ════════════════════════════════════════════════════════════

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚽ Ставки на матчи",   callback_data="cb:matches")],
        [InlineKeyboardButton("🔗 Экспресс",          callback_data="cb:express")],
        [InlineKeyboardButton("🎲 Кастомные ставки", callback_data="cb:cbets")],
        [InlineKeyboardButton("🎯 Каперы",            callback_data="cb:cappers")],
        [InlineKeyboardButton("📋 Мои ставки",        callback_data="cb:my_bets")],
        [InlineKeyboardButton("💰 Баланс",            callback_data="cb:balance"),
         InlineKeyboardButton("🎁 Бонус",             callback_data="cb:bonus")],
        [InlineKeyboardButton("🎫 Промокод",          callback_data="cb:promo")],
        [InlineKeyboardButton("📨 Запросить новую лигу", callback_data="cb:req_league")],
        [InlineKeyboardButton("🏠 Меню Казимирно",    callback_data="cb:kazik")],
    ])

def kb_leagues():
    """Корневое меню регионов."""
    rows = [
        [InlineKeyboardButton(name, callback_data=f"cb:lcat_{code}")]
        for code, name in LEAGUE_REGIONS.items()
    ]
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="cb:main")])
    return InlineKeyboardMarkup(rows)

def kb_league_groups(region_code: str):
    """Подразделы внутри региона. Если подраздел один — сразу список лиг."""
    groups = LEAGUE_GROUPS.get(region_code, [])
    rows = []
    if len(groups) == 1:
        for lid in groups[0][2]:
            name = LEAGUES.get(lid)
            if name:
                rows.append([InlineKeyboardButton(name, callback_data=f"cb:league_{lid}")])
    else:
        for code, title, _ in groups:
            rows.append([InlineKeyboardButton(
                title, callback_data=f"cb:lgrp_{region_code}_{code}"
            )])
    rows.append([InlineKeyboardButton("◀️ К регионам", callback_data="cb:matches")])
    return InlineKeyboardMarkup(rows)

def kb_league_list(region_code: str, group_code: str):
    """Список лиг внутри подраздела."""
    rows = []
    for code, _, lids in LEAGUE_GROUPS.get(region_code, []):
        if code == group_code:
            for lid in lids:
                name = LEAGUES.get(lid)
                if name:
                    rows.append([InlineKeyboardButton(name, callback_data=f"cb:league_{lid}")])
            break
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data=f"cb:lcat_{region_code}")])
    return InlineKeyboardMarkup(rows)

def _league_group(lid: int) -> tuple[str, str] | None:
    """Возвращает (region_code, group_code) для лиги или None."""
    for region, groups in LEAGUE_GROUPS.items():
        for code, _, lids in groups:
            if lid in lids:
                return region, code
    return None

def kb_fixtures(fixtures, league_id: int | None = None):
    rows = []
    for f in fixtures:
        dt    = datetime.fromtimestamp(f["start_ts"], tz=timezone.utc)
        label = f"⚽ {f['home_team']} vs {f['away_team']}  {dt.strftime('%d.%m %H:%M')}"
        rows.append([InlineKeyboardButton(label, callback_data=f"cb:fix_{f['fixture_id']}")])
    # Назад — в подраздел, откуда выбрали лигу; иначе к регионам
    back_cb = "cb:matches"
    if league_id is not None:
        grp = _league_group(league_id)
        if grp:
            region, group = grp
            groups = LEAGUE_GROUPS.get(region, [])
            back_cb = (
                f"cb:lgrp_{region}_{group}" if len(groups) > 1 else f"cb:lcat_{region}"
            )
    rows.append([InlineKeyboardButton("◀️ К лигам", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)

def kb_fixture_bet(fixture_id, home, away, odds: dict, has_scorers: bool = False):
    """Клавиатура выбора рынка: 1X2, тоталы, «Обе», угловые, ЖК, ДШ, точный счёт.
    + кнопка «Авторы голов», если есть данные в fixture_scorers.
    """
    rows = []

    def cb(code: str) -> str:
        return f"cb:bt_{fixture_id}_{code}"

    # 1X2
    rows.append([InlineKeyboardButton(f"1  {home[:14]}  x{odds['home']}", callback_data=cb("home"))])
    rows.append([InlineKeyboardButton(f"X  Ничья  x{odds['draw']}",       callback_data=cb("draw"))])
    rows.append([InlineKeyboardButton(f"2  {away[:14]}  x{odds['away']}", callback_data=cb("away"))])

    # Тоталы голов — парно
    for line in OU_LINES:
        ok = OU_OVER_KEY[line]
        uk = OU_UNDER_KEY[line]
        rows.append([
            InlineKeyboardButton(f"ТБ {line}  x{odds[ok]}", callback_data=cb(ok)),
            InlineKeyboardButton(f"ТМ {line}  x{odds[uk]}", callback_data=cb(uk)),
        ])

    # Обе забьют
    rows.append([
        InlineKeyboardButton(f"Обе-Да  x{odds['bttsy']}",  callback_data=cb("bttsy")),
        InlineKeyboardButton(f"Обе-Нет  x{odds['bttsn']}", callback_data=cb("bttsn")),
    ])

    # Двойной шанс
    rows.append([
        InlineKeyboardButton(f"1X  x{odds['dc1x']}", callback_data=cb("dc1x")),
        InlineKeyboardButton(f"12  x{odds['dc12']}", callback_data=cb("dc12")),
        InlineKeyboardButton(f"X2  x{odds['dcx2']}", callback_data=cb("dcx2")),
    ])

    # Угловые ТБ/ТМ 9.5
    rows.append([
        InlineKeyboardButton(f"Угл ТБ 9.5  x{odds['c95o']}", callback_data=cb("c95o")),
        InlineKeyboardButton(f"Угл ТМ 9.5  x{odds['c95u']}", callback_data=cb("c95u")),
    ])

    # ЖК 4.5
    rows.append([
        InlineKeyboardButton(f"ЖК ТБ 4.5  x{odds['yc45o']}", callback_data=cb("yc45o")),
        InlineKeyboardButton(f"ЖК ТМ 4.5  x{odds['yc45u']}", callback_data=cb("yc45u")),
    ])

    # Точный счёт (2 ряда по 3)
    rows.append([
        InlineKeyboardButton(f"1:0  x{odds['cs10']}", callback_data=cb("cs10")),
        InlineKeyboardButton(f"2:1  x{odds['cs21']}", callback_data=cb("cs21")),
        InlineKeyboardButton(f"2:0  x{odds['cs20']}", callback_data=cb("cs20")),
    ])
    rows.append([
        InlineKeyboardButton(f"0:0  x{odds['cs00']}", callback_data=cb("cs00")),
        InlineKeyboardButton(f"1:1  x{odds['cs11']}", callback_data=cb("cs11")),
        InlineKeyboardButton(f"2:2  x{odds['cs22']}", callback_data=cb("cs22")),
    ])

    # Авторы голов — отдельный экран
    if has_scorers:
        rows.append([InlineKeyboardButton("👟 Авторы голов",
                                          callback_data=f"cb:gs_{fixture_id}_0")])

    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="cb:matches")])
    return InlineKeyboardMarkup(rows)


def kb_fixture_scorers(fixture_id: int, scorers: list, page: int):
    """Клавиатура экрана «Авторы голов» с пагинацией по 6 игроков."""
    rows = []
    per_page = 6
    total = len(scorers)
    pages = max(1, (total + per_page - 1) // per_page)
    page  = max(0, min(page, pages - 1))
    start = page * per_page
    end   = min(start + per_page, total)
    for s in scorers[start:end]:
        rows.append([InlineKeyboardButton(
            f"👟 {s['player_name'][:26]}  x{s['odds_any']}",
            callback_data=f"cb:bt_{fixture_id}_{GOAL_SCORER_PREFIX}{s['player_id']}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️",
                                        callback_data=f"cb:gs_{fixture_id}_{page-1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="cb:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("➡️",
                                        callback_data=f"cb:gs_{fixture_id}_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀️ К матчу", callback_data=f"cb:fix_{fixture_id}")])
    return InlineKeyboardMarkup(rows)

def kb_amounts(fixture_id, bet_on):
    amounts = [50, 100, 250, 500, 1000, 2000]
    rows, row = [], []
    for a in amounts:
        row.append(InlineKeyboardButton(f"{a} {COIN_EMOJI}", callback_data=f"cb:ba_{fixture_id}_{bet_on}_{a}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("✍️ Своя сумма", callback_data=f"cb:ba_{fixture_id}_{bet_on}_custom"),
        InlineKeyboardButton("◀️ Назад",      callback_data=f"cb:fix_{fixture_id}"),
    ])
    return InlineKeyboardMarkup(rows)

def kb_back():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="cb:main")]])

def kb_admin():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика",      callback_data="cb:adm_stats"),
         InlineKeyboardButton("🔄 Обновить матчи",  callback_data="cb:adm_update")],
        [InlineKeyboardButton("💰 Выдать Винкоины",  callback_data="cb:adm_give"),
         InlineKeyboardButton("📢 Рассылка",         callback_data="cb:adm_broadcast")],
        [InlineKeyboardButton("🎲 Создать каст. ставку", callback_data="cb:adm_cbet_new"),
         InlineKeyboardButton("✅ Закрыть каст. ставку",  callback_data="cb:adm_cbet_settle")],
        [InlineKeyboardButton("🎟 Реальные ставки",       callback_data="cb:adm_rbets")],
        [InlineKeyboardButton("🎁 Создать промокод",      callback_data="cb:adm_promo_new")],
        [InlineKeyboardButton("📨 Запросы лиг",           callback_data="cb:adm_league_reqs")],
        [InlineKeyboardButton("◀️ Назад",           callback_data="cb:main")],
    ])


# ════════════════════════════════════════════════════════════
#  ADMIN: реальные ставки — ручной резолв
# ════════════════════════════════════════════════════════════

def kb_admin_rbets():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏟 Закрыть по матчу",     callback_data="cb:adm_rbf_list_0")],
        [InlineKeyboardButton("📋 Открытые ставки",     callback_data="cb:adm_rbl_0")],
        [InlineKeyboardButton("🔁 Закрытые ставки",     callback_data="cb:adm_rbcl_0")],
        [InlineKeyboardButton("◀️ Назад",                callback_data="cb:admin_back")],
    ])


async def admin_get_pending_fixtures(limit: int = 200) -> list[dict]:
    """Список фикстур, по которым есть открытые одиночные ставки или ноги
    экспрессов. Сортируем по `start_ts ASC` (ближайшие/прошедшие матчи
    сверху). Подмешиваем `home/away` из cached_fixtures для отображения.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH ids AS (
                SELECT match_id::text AS mid FROM bets WHERE result='pending'
                UNION
                SELECT match_id::text AS mid FROM express_bet_legs WHERE result='pending'
            ),
            counts AS (
                SELECT mid::bigint AS fixture_id,
                       (SELECT COUNT(*) FROM bets b
                          WHERE b.match_id::text=ids.mid AND b.result='pending')::int AS singles,
                       (SELECT COUNT(*) FROM express_bet_legs l
                          WHERE l.match_id::text=ids.mid AND l.result='pending')::int AS legs
                FROM ids
                WHERE mid ~ '^[0-9]+$'
            )
            SELECT c.fixture_id, c.singles, c.legs,
                   f.home_team, f.away_team, f.start_ts, f.match_date,
                   f.league_name
            FROM counts c
            LEFT JOIN cached_fixtures f ON f.fixture_id = c.fixture_id
            ORDER BY COALESCE(f.start_ts, 0) ASC, c.fixture_id ASC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def admin_get_pending_singles(offset: int, limit: int = 10) -> tuple[list[dict], int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_row = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM bets WHERE result='pending'"
        )
        total = int(total_row["n"]) if total_row else 0
        rows = await conn.fetch(
            "SELECT * FROM bets WHERE result='pending' "
            "ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
    return [dict(r) for r in rows], total


async def admin_get_settled_singles(offset: int, limit: int = 10) -> tuple[list[dict], int]:
    """Закрытые ставки (win/lose/void). Свежие сверху."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_row = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM bets WHERE result IN ('win','lose','void')"
        )
        total = int(total_row["n"]) if total_row else 0
        rows = await conn.fetch(
            "SELECT * FROM bets WHERE result IN ('win','lose','void') "
            "ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
    return [dict(r) for r in rows], total


async def admin_get_pending_legs(offset: int, limit: int = 10) -> tuple[list[dict], int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_row = await conn.fetchrow(
            "SELECT COUNT(*) AS n FROM express_bet_legs WHERE result='pending'"
        )
        total = int(total_row["n"]) if total_row else 0
        rows = await conn.fetch(
            "SELECT l.*, e.user_id AS exp_user_id, e.amount AS exp_amount, "
            "       e.total_odds AS exp_total_odds "
            "FROM express_bet_legs l JOIN express_bets e ON l.express_id = e.id "
            "WHERE l.result='pending' ORDER BY l.id DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
    return [dict(r) for r in rows], total


def kb_admin_rbets_list_fixtures(fixtures: list[dict], page: int) -> InlineKeyboardMarkup:
    PAGE = 8
    start = page * PAGE
    chunk = fixtures[start:start + PAGE]
    rows: list[list[InlineKeyboardButton]] = []
    for f in chunk:
        title = f"{f.get('home_team') or '?'} – {f.get('away_team') or '?'}"
        n = int(f.get("singles") or 0) + int(f.get("legs") or 0)
        rows.append([InlineKeyboardButton(
            f"#{f['fixture_id']} · {title[:30]} · {n} ст.",
            callback_data=f"cb:adm_rbf_{f['fixture_id']}",
        )])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"cb:adm_rbf_list_{page-1}"))
    if start + PAGE < len(fixtures):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"cb:adm_rbf_list_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="cb:adm_rbets")])
    return InlineKeyboardMarkup(rows)


def kb_admin_rb_fixture(fixture_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Ввести результат",
                              callback_data=f"cb:adm_rbf_input_{fixture_id}")],
        [InlineKeyboardButton("📋 Ставки этого матча",
                              callback_data=f"cb:adm_rbf_bets_{fixture_id}_0")],
        [InlineKeyboardButton("◀️ Назад", callback_data="cb:adm_rbf_list_0")],
    ])


def kb_admin_rb_singles_list(
    bets: list[dict], page: int, total: int, page_size: int = 10
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for b in bets:
        info = (b.get("match_info") or "")[:25]
        rows.append([InlineKeyboardButton(
            f"#{b['id']} · {fmt_coins(b['amount'])} · {info}",
            callback_data=f"cb:adm_rbs_{b['id']}",
        )])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"cb:adm_rbl_{page-1}"))
    if (page + 1) * page_size < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"cb:adm_rbl_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="cb:adm_rbets")])
    return InlineKeyboardMarkup(rows)


def kb_admin_rb_bet_actions(bet_id: int, back: str = "adm_rbl_0") -> InlineKeyboardMarkup:
    """Действия с одной ставкой: win/lose/void."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Победа",  callback_data=f"cb:adm_rbsx_{bet_id}_win"),
         InlineKeyboardButton("❌ Поражение", callback_data=f"cb:adm_rbsx_{bet_id}_lose")],
        [InlineKeyboardButton("↩️ Возврат (void)",
                              callback_data=f"cb:adm_rbsx_{bet_id}_void")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"cb:{back}")],
    ])


async def _admin_settle_single_bet(
    ctx: ContextTypes.DEFAULT_TYPE, bet_id: int, outcome: str
) -> tuple[bool, str]:
    """Устанавливает результат одиночной ставки (win/lose/void).

    Работает и с pending, и с уже закрытыми. При смене исхода у ранее
    закрытой ставки баланс автоматически корректируется (откатывается
    старая выплата, применяется новая). Например, win → lose:
    `balance -= winnings`, `total_won -= winnings`.

    Возвращает (ok, описание).
    """
    if outcome not in ("win", "lose", "void"):
        return False, "Неизвестный исход"
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            bet = await conn.fetchrow(
                "SELECT * FROM bets WHERE id=$1 FOR UPDATE", bet_id
            )
            if not bet:
                return False, "Ставка не найдена"
            old = bet["result"] or "pending"
            if old == outcome:
                return False, f"уже {outcome}"
            amount = int(bet["amount"])
            odds = float(bet["odds"]) or 1.0
            winnings = int(amount * odds)
            user_id = int(bet["user_id"])
            # Старая выплата (то, что юзеру кредитнули раньше).
            old_credit = (
                winnings if old == "win"
                else amount if old == "void"
                else 0
            )
            # Новая выплата.
            new_credit = (
                winnings if outcome == "win"
                else amount if outcome == "void"
                else 0
            )
            delta_balance = new_credit - old_credit
            # total_won отражает только реальные выигрыши (не возвраты).
            total_won_delta = (winnings if outcome == "win" else 0) \
                - (winnings if old == "win" else 0)
            await conn.execute(
                "UPDATE bets SET result=$1 WHERE id=$2", outcome, bet_id,
            )
            if delta_balance != 0 or total_won_delta != 0:
                await conn.execute(
                    "UPDATE players SET balance = balance + $1, "
                    "total_won = total_won + $2 WHERE tg_id = $3",
                    delta_balance, total_won_delta, user_id,
                )

    label = await _bet_display_label(bet["bet_on"], int(bet["match_id"]))
    is_change = old != "pending"
    prefix = (
        f"🔄 <b>Исход ставки изменён администратором</b>\n"
        f"<i>было: {old} → стало: {outcome}</i>\n\n"
    ) if is_change else ""
    try:
        if outcome == "win":
            text = (
                prefix +
                f"⚽ <b>Ставка сыграла!</b>"
                + ("" if is_change else " (закрыто администратором)") +
                f"\n\n🏆 {bet['match_info']}\n📌 {label}\n"
                f"💰 Ставка: <b>{fmt_coins(amount)}</b>  x{odds}\n"
                f"🎉 Выигрыш: <b>+{fmt_coins(winnings)}</b>"
            )
            if is_change and delta_balance != winnings:
                text += f"\n\n⚖️ Баланс скорректирован: " \
                        f"{'+' if delta_balance >= 0 else ''}{fmt_coins(delta_balance)}"
            await ctx.bot.send_message(user_id, text, parse_mode="HTML")
        elif outcome == "void":
            text = (
                prefix +
                f"↩️ <b>Ставка возвращена</b>"
                + ("" if is_change else " (закрыто администратором)") +
                f"\n\n🏆 {bet['match_info']}\n📌 {label}\n"
                f"💰 Возврат: <b>{fmt_coins(amount)}</b>"
            )
            if is_change:
                text += f"\n\n⚖️ Баланс скорректирован: " \
                        f"{'+' if delta_balance >= 0 else ''}{fmt_coins(delta_balance)}"
            await ctx.bot.send_message(user_id, text, parse_mode="HTML")
        else:  # lose
            text = (
                prefix +
                f"⚽ <b>Ставка не сыграла</b>"
                + ("" if is_change else " (закрыто администратором)") +
                f"\n\n🏆 {bet['match_info']}\n📌 {label}\n"
                f"💸 Потеряно: <b>{fmt_coins(amount)}</b>"
            )
            if is_change and delta_balance != 0:
                text += f"\n\n⚖️ Баланс скорректирован: " \
                        f"{'+' if delta_balance >= 0 else ''}{fmt_coins(delta_balance)}"
            await ctx.bot.send_message(user_id, text, parse_mode="HTML")
    except Exception:
        pass

    if outcome == "win":
        return True, f"win (+{fmt_coins(winnings)})"
    if outcome == "void":
        return True, f"void ({fmt_coins(amount)})"
    return True, "lose"


def _parse_admin_fixture_result(txt: str) -> tuple[dict | None, str | None]:
    """Парсит ввод админа для ручного резолва матча.

    Формат:
        2:1
        corners=11
        yellows=3
        scorers=Холланд, Салах

    Возвращает (result_dict, err). result_dict совпадает с тем, что отдаёт
    `fetch_game_result`: home_goals, away_goals, total_corners,
    total_yellows, scorer_ids (пустой), scorer_names (lower-cased).
    Достаточно для `_bet_result()`. Все поля кроме счёта — опциональны.
    """
    out: dict = {
        "home_goals": None, "away_goals": None,
        "total_corners": None, "total_yellows": None,
        "home_team_id": None, "away_team_id": None,
        "scorer_ids": set(),
        "scorer_names": set(),
    }
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    if not lines:
        return None, "Пустой ввод"
    score_line = lines[0]
    # Принимаем «2:1», «2-1», «2 1»
    parts = score_line.replace("-", ":").replace(" ", ":").split(":")
    parts = [p for p in parts if p != ""]
    if len(parts) != 2:
        return None, f"Не понял счёт: {score_line!r} (нужно «2:1»)"
    try:
        out["home_goals"] = int(parts[0])
        out["away_goals"] = int(parts[1])
    except ValueError:
        return None, f"Счёт должен быть числовым: {score_line!r}"
    for line in lines[1:]:
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip().lower()
        v = v.strip()
        if k in ("corners", "угловые"):
            try:
                out["total_corners"] = int(v)
            except ValueError:
                return None, f"corners= должно быть число: {v!r}"
        elif k in ("yellows", "жк", "карточки"):
            try:
                out["total_yellows"] = int(v)
            except ValueError:
                return None, f"yellows= должно быть число: {v!r}"
        elif k in ("scorers", "голлеры"):
            for name in v.split(","):
                name = name.strip().lower()
                if name:
                    out["scorer_names"].add(name)
    return out, None


async def _admin_resolve_fixture_bets(
    ctx: ContextTypes.DEFAULT_TYPE,
    fixture_id: int,
    result: dict,
) -> dict:
    """Прогоняет _bet_result() по всем pending одиночным ставкам и leg'ам
    данного матча. Возвращает счётчики. Уведомления игрокам шлются
    автоматически.

    Особенность: для голлеров используем `scorer_names` (lower-case
    подстрока в имени игрока из БД). Если у админа в списке нет имени,
    которое букмекер показывал в Anytime, ставка пойдёт `lose`. Чтобы не
    штрафовать игрока за нашу неточность — если scorer_names пуст, такие
    ставки закрываем как `void`.
    """
    counts = {"win": 0, "lose": 0, "void": 0, "skipped": 0}
    pool = await get_pool()

    # ── одиночные ──
    async with pool.acquire() as conn:
        bets = await conn.fetch(
            "SELECT * FROM bets WHERE match_id=$1 AND result='pending'",
            str(fixture_id),
        )
    bets = [dict(b) for b in bets]
    # Подгружаем словарь scorers из БД для матчинга «scorer_names → pid».
    db_scorers: list[dict] = []
    try:
        db_scorers = await load_fixture_scorers(fixture_id)
    except Exception:
        db_scorers = []
    matched_ids: set[int] = set()
    for s in db_scorers:
        nm = (s.get("player_name") or "").strip().lower()
        if not nm:
            continue
        for needle in result.get("scorer_names") or set():
            # Простой матчинг: подстрока в любом направлении.
            if needle in nm or nm in needle:
                pid = s.get("player_id")
                if pid is not None:
                    try:
                        matched_ids.add(int(pid))
                    except (TypeError, ValueError):
                        pass
                break
    result_filled = dict(result)
    result_filled["scorer_ids"] = matched_ids

    for b in bets:
        bet_on = b["bet_on"] or ""
        # Голлер без введённого списка имён — возврат, чтобы не штрафовать.
        if bet_on.startswith(GOAL_SCORER_PREFIX) and not result.get("scorer_names"):
            ok, _ = await _admin_settle_single_bet(ctx, int(b["id"]), "void")
            counts["void" if ok else "skipped"] += 1
            continue
        outcome = _bet_result(bet_on, result_filled)
        ok, _ = await _admin_settle_single_bet(ctx, int(b["id"]), outcome)
        counts[outcome if ok else "skipped"] += 1

    # ── leg'и экспрессов ──
    legs = await get_pending_express_legs_by_fixture(fixture_id)
    affected_express: set[int] = set()
    for leg in legs:
        leg_outcome = _bet_result(leg["bet_on"], result_filled)
        if leg["bet_on"].startswith(GOAL_SCORER_PREFIX) \
                and not result.get("scorer_names"):
            leg_outcome = "void"
        await update_express_leg_result(leg["id"], leg_outcome)
        counts[leg_outcome] = counts.get(leg_outcome, 0) + 1
        affected_express.add(int(leg["express_id"]))

    # Финализируем экспрессы.
    for exp_id in affected_express:
        finalize = await try_finalize_express(exp_id)
        if not finalize:
            continue
        final, exp_row = finalize
        try:
            if final == "win":
                winnings = int(exp_row["amount"] * float(exp_row["total_odds"]))
                await ctx.bot.send_message(
                    int(exp_row["user_id"]),
                    f"🎯 <b>Экспресс сыграл!</b> (закрыто администратором)\n"
                    f"💰 Ставка: <b>{fmt_coins(int(exp_row['amount']))}</b> "
                    f"x{float(exp_row['total_odds']):.2f}\n"
                    f"🎉 Выигрыш: <b>+{fmt_coins(winnings)}</b>",
                    parse_mode="HTML",
                )
            elif final == "refund":
                await ctx.bot.send_message(
                    int(exp_row["user_id"]),
                    f"↩️ <b>Экспресс возвращён</b> (все ноги отменены)\n"
                    f"💰 Возврат: <b>{fmt_coins(int(exp_row['amount']))}</b>",
                    parse_mode="HTML",
                )
            else:
                await ctx.bot.send_message(
                    int(exp_row["user_id"]),
                    f"❌ <b>Экспресс не сыграл</b> (закрыто администратором).",
                    parse_mode="HTML",
                )
        except Exception:
            pass

    return counts


async def db_league_request_save(tg_id: int, username: str | None, text: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO cb_league_requests (tg_id, username, request_text) "
            "VALUES ($1, $2, $3) RETURNING id",
            tg_id, username, text,
        )
        return int(row["id"]) if row else 0


async def db_league_requests_list(status: str | None = None, limit: int = 20) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM cb_league_requests WHERE status = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                status, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM cb_league_requests ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        return [dict(r) for r in rows]


async def db_league_request_set_status(req_id: int, status: str, note: str | None = None) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.execute(
            "UPDATE cb_league_requests "
            "SET status = $1, admin_note = $2, handled_at = NOW() "
            "WHERE id = $3",
            status, note, req_id,
        )
        return r.endswith("1")

def kb_cbets_list(cbets: list):
    rows = []
    for cb in cbets:
        rows.append([InlineKeyboardButton(
            f"🎲 {cb['title'][:48]}", callback_data=f"cb:cbet_{cb['id']}"
        )])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="cb:main")])
    return InlineKeyboardMarkup(rows)

def kb_cbet_options(cbet_id: int, options: list):
    rows = []
    for i, opt in enumerate(options):
        name = opt.get("name", f"#{i+1}")
        odds = opt.get("odds", 1.0)
        rows.append([InlineKeyboardButton(
            f"{i+1}. {name[:30]}  x{odds}", callback_data=f"cb:cbo_{cbet_id}_{i}"
        )])
    rows.append([InlineKeyboardButton("◀️ К ставкам", callback_data="cb:cbets")])
    return InlineKeyboardMarkup(rows)

def kb_cbet_amounts(cbet_id: int, option_index: int):
    amounts = [50, 100, 250, 500, 1000, 2000]
    rows, row = [], []
    for a in amounts:
        row.append(InlineKeyboardButton(
            f"{a} {COIN_EMOJI}",
            callback_data=f"cb:cba_{cbet_id}_{option_index}_{a}",
        ))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("✍️ Своя сумма",
            callback_data=f"cb:cba_{cbet_id}_{option_index}_custom"),
        InlineKeyboardButton("◀️ Назад", callback_data=f"cb:cbet_{cbet_id}"),
    ])
    return InlineKeyboardMarkup(rows)

def kb_cbet_settle_list(cbets: list):
    rows = []
    for cb in cbets:
        rows.append([InlineKeyboardButton(
            f"#{cb['id']} {cb['title'][:40]}", callback_data=f"cb:scbet_{cb['id']}"
        )])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="cb:admin_back")])
    return InlineKeyboardMarkup(rows)

def kb_cbet_settle_options(cbet_id: int, options: list):
    rows = []
    for i, opt in enumerate(options):
        rows.append([InlineKeyboardButton(
            f"✅ {i+1}. {opt.get('name','')[:30]}",
            callback_data=f"cb:scbo_{cbet_id}_{i}",
        )])
    rows.append([InlineKeyboardButton(
        "❌ Отменить (возврат)", callback_data=f"cb:scbo_{cbet_id}_cancel"
    )])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="cb:adm_cbet_settle")])
    return InlineKeyboardMarkup(rows)

def kb_promo():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎟 Ввести код", callback_data="cb:promo_enter")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="cb:main")],
    ])

def kb_express_builder(legs: list, has_balance: bool):
    rows = [[InlineKeyboardButton("➕ Добавить матч", callback_data="cb:exp_add")]]
    if legs:
        for i, leg in enumerate(legs):
            rows.append([InlineKeyboardButton(
                f"🗑 #{i+1} {leg['match_info'][:40]}",
                callback_data=f"cb:exp_rm_{i}",
            )])
    if len(legs) >= MIN_EXPRESS_LEGS and has_balance:
        rows.append([InlineKeyboardButton("💰 Сделать ставку", callback_data="cb:exp_place")])
    if legs:
        rows.append([InlineKeyboardButton("♻️ Очистить", callback_data="cb:exp_clear")])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="cb:main")])
    return InlineKeyboardMarkup(rows)

def kb_express_leagues():
    """Корневое меню регионов (для экспресса)."""
    rows = [
        [InlineKeyboardButton(name, callback_data=f"cb:excat_{code}")]
        for code, name in LEAGUE_REGIONS.items()
    ]
    rows.append([InlineKeyboardButton("◀️ К экспрессу", callback_data="cb:express")])
    return InlineKeyboardMarkup(rows)

def kb_express_groups(region_code: str):
    """Подразделы внутри региона (для экспресса)."""
    groups = LEAGUE_GROUPS.get(region_code, [])
    rows = []
    if len(groups) == 1:
        for lid in groups[0][2]:
            name = LEAGUES.get(lid)
            if name:
                rows.append([InlineKeyboardButton(name, callback_data=f"cb:exl_{lid}")])
    else:
        for code, title, _ in groups:
            rows.append([InlineKeyboardButton(
                title, callback_data=f"cb:exgrp_{region_code}_{code}"
            )])
    rows.append([InlineKeyboardButton("◀️ К регионам", callback_data="cb:exp_add")])
    return InlineKeyboardMarkup(rows)

def kb_express_league_list(region_code: str, group_code: str):
    """Список лиг внутри подраздела (для экспресса)."""
    rows = []
    for code, _, lids in LEAGUE_GROUPS.get(region_code, []):
        if code == group_code:
            for lid in lids:
                name = LEAGUES.get(lid)
                if name:
                    rows.append([InlineKeyboardButton(name, callback_data=f"cb:exl_{lid}")])
            break
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data=f"cb:excat_{region_code}")])
    return InlineKeyboardMarkup(rows)

def kb_express_fixtures(fixtures, used_ids: set, league_id: int | None = None):
    rows = []
    for f in fixtures:
        if f["fixture_id"] in used_ids:
            continue
        dt    = datetime.fromtimestamp(f["start_ts"], tz=timezone.utc)
        label = f"⚽ {f['home_team']} vs {f['away_team']}  {dt.strftime('%d.%m %H:%M')}"
        rows.append([InlineKeyboardButton(label, callback_data=f"cb:exf_{f['fixture_id']}")])
    back_cb = "cb:exp_add"
    if league_id is not None:
        grp = _league_group(league_id)
        if grp:
            region, group = grp
            groups = LEAGUE_GROUPS.get(region, [])
            back_cb = (
                f"cb:exgrp_{region}_{group}" if len(groups) > 1 else f"cb:excat_{region}"
            )
    rows.append([InlineKeyboardButton("◀️ К лигам", callback_data=back_cb)])
    return InlineKeyboardMarkup(rows)

def kb_express_pick_bet(fixture_id, home, away, odds: dict, has_scorers: bool = False):
    """Клавиатура выбора исхода для экспресса: все 1X2/тоталы/Обе/ДШ/Угл/ЖК/ТС/голлеры."""
    rows = []

    def cb(code: str) -> str:
        return f"cb:exb_{fixture_id}_{code}"

    rows.append([InlineKeyboardButton(f"1  {home[:14]}  x{odds['home']}", callback_data=cb("home"))])
    rows.append([InlineKeyboardButton(f"X  Ничья  x{odds['draw']}",       callback_data=cb("draw"))])
    rows.append([InlineKeyboardButton(f"2  {away[:14]}  x{odds['away']}", callback_data=cb("away"))])
    for line in OU_LINES:
        ok = OU_OVER_KEY[line]
        uk = OU_UNDER_KEY[line]
        rows.append([
            InlineKeyboardButton(f"ТБ {line}  x{odds[ok]}", callback_data=cb(ok)),
            InlineKeyboardButton(f"ТМ {line}  x{odds[uk]}", callback_data=cb(uk)),
        ])
    rows.append([
        InlineKeyboardButton(f"Обе-Да  x{odds['bttsy']}",  callback_data=cb("bttsy")),
        InlineKeyboardButton(f"Обе-Нет  x{odds['bttsn']}", callback_data=cb("bttsn")),
    ])
    rows.append([
        InlineKeyboardButton(f"1X  x{odds['dc1x']}", callback_data=cb("dc1x")),
        InlineKeyboardButton(f"12  x{odds['dc12']}", callback_data=cb("dc12")),
        InlineKeyboardButton(f"X2  x{odds['dcx2']}", callback_data=cb("dcx2")),
    ])
    rows.append([
        InlineKeyboardButton(f"Угл ТБ 9.5  x{odds['c95o']}", callback_data=cb("c95o")),
        InlineKeyboardButton(f"Угл ТМ 9.5  x{odds['c95u']}", callback_data=cb("c95u")),
    ])
    rows.append([
        InlineKeyboardButton(f"ЖК ТБ 4.5  x{odds['yc45o']}", callback_data=cb("yc45o")),
        InlineKeyboardButton(f"ЖК ТМ 4.5  x{odds['yc45u']}", callback_data=cb("yc45u")),
    ])
    rows.append([
        InlineKeyboardButton(f"1:0 x{odds['cs10']}", callback_data=cb("cs10")),
        InlineKeyboardButton(f"2:1 x{odds['cs21']}", callback_data=cb("cs21")),
        InlineKeyboardButton(f"2:0 x{odds['cs20']}", callback_data=cb("cs20")),
    ])
    rows.append([
        InlineKeyboardButton(f"0:0 x{odds['cs00']}", callback_data=cb("cs00")),
        InlineKeyboardButton(f"1:1 x{odds['cs11']}", callback_data=cb("cs11")),
        InlineKeyboardButton(f"2:2 x{odds['cs22']}", callback_data=cb("cs22")),
    ])
    if has_scorers:
        rows.append([InlineKeyboardButton("👟 Авторы голов",
                                          callback_data=f"cb:exgs_{fixture_id}_0")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="cb:exp_add")])
    return InlineKeyboardMarkup(rows)


def kb_express_scorers(fixture_id: int, scorers: list, page: int):
    rows = []
    per_page = 6
    total = len(scorers)
    pages = max(1, (total + per_page - 1) // per_page)
    page  = max(0, min(page, pages - 1))
    start = page * per_page
    end   = min(start + per_page, total)
    for s in scorers[start:end]:
        rows.append([InlineKeyboardButton(
            f"👟 {s['player_name'][:26]}  x{s['odds_any']}",
            callback_data=f"cb:exb_{fixture_id}_{GOAL_SCORER_PREFIX}{s['player_id']}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️",
                                        callback_data=f"cb:exgs_{fixture_id}_{page-1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="cb:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("➡️",
                                        callback_data=f"cb:exgs_{fixture_id}_{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀️ К матчу",
                                      callback_data=f"cb:exf_{fixture_id}")])
    return InlineKeyboardMarkup(rows)

def kb_express_amounts():
    amounts = [50, 100, 250, 500, 1000, 2000]
    rows, row = [], []
    for a in amounts:
        row.append(InlineKeyboardButton(
            f"{a} {COIN_EMOJI}", callback_data=f"cb:exa_{a}"
        ))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("✍️ Своя сумма", callback_data="cb:exa_custom"),
        InlineKeyboardButton("◀️ Назад",       callback_data="cb:express"),
    ])
    return InlineKeyboardMarkup(rows)

def kb_cappers_menu(is_capper: bool, can_take_test: bool):
    rows = [[InlineKeyboardButton("📰 Лента типов", callback_data="cb:cap_feed")]]
    if is_capper:
        rows.append([InlineKeyboardButton(
            "📝 Поделиться моей ставкой", callback_data="cb:cap_my_bets"
        )])
    elif can_take_test:
        rows.append([InlineKeyboardButton(
            "🎓 Пройти тест и стать капером", callback_data="cb:cap_quiz_start"
        )])
    rows.append([InlineKeyboardButton("ℹ️ Как работает каперство", callback_data="cb:cap_info")])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="cb:main")])
    return InlineKeyboardMarkup(rows)

def kb_cap_feed(tips: list):
    rows = []
    for t in tips:
        label = f"#{t['id']} " + (
            "🔗 Экспресс" if t["bet_type"] == "express" else "⚽ Одиночная"
        )
        name = t.get("capper_username") or t.get("capper_name") or str(t["capper_id"])
        rows.append([InlineKeyboardButton(f"{label} · {name}",
                                          callback_data=f"cb:cap_tip_{t['id']}")])
    rows.append([InlineKeyboardButton("◀️ К каперам", callback_data="cb:cappers")])
    return InlineKeyboardMarkup(rows)

def kb_cap_tip(tip: dict, can_follow: bool):
    rows = []
    if can_follow:
        rows.append([InlineKeyboardButton(
            "💰 Повторить ставку", callback_data=f"cb:cap_follow_{tip['id']}"
        )])
    rows.append([InlineKeyboardButton("◀️ К ленте", callback_data="cb:cap_feed")])
    return InlineKeyboardMarkup(rows)

def kb_cap_follow_amounts(tip_id: int):
    amounts = [50, 100, 250, 500, 1000, 2000]
    rows, row = [], []
    for a in amounts:
        row.append(InlineKeyboardButton(
            f"{a} {COIN_EMOJI}", callback_data=f"cb:cap_fa_{tip_id}_{a}"
        ))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("✍️ Своя сумма", callback_data=f"cb:cap_fa_{tip_id}_custom"),
        InlineKeyboardButton("◀️ Назад", callback_data=f"cb:cap_tip_{tip_id}"),
    ])
    return InlineKeyboardMarkup(rows)

def kb_cap_share_list(bets: list, expresses: list):
    rows = []
    for b in bets:
        rows.append([InlineKeyboardButton(
            f"⚽ #{b['id']} {b['match_info'][:40]} · x{b['odds']}",
            callback_data=f"cb:cap_share_b_{b['id']}",
        )])
    for e in expresses:
        rows.append([InlineKeyboardButton(
            f"🔗 Экспресс #{e['id']} · {len(e['legs'])} ног · x{e['total_odds']}",
            callback_data=f"cb:cap_share_e_{e['id']}",
        )])
    if not rows:
        rows.append([InlineKeyboardButton(
            "Нет подходящих ставок", callback_data="cb:cappers"
        )])
    rows.append([InlineKeyboardButton("◀️ К каперам", callback_data="cb:cappers")])
    return InlineKeyboardMarkup(rows)

def kb_cap_quiz(qidx: int):
    q_text, opts, _ = CAPPER_QUIZ[qidx]
    rows = []
    for i, opt in enumerate(opts):
        rows.append([InlineKeyboardButton(opt, callback_data=f"cb:cap_qa_{qidx}_{i}")])
    rows.append([InlineKeyboardButton("🚫 Прервать тест", callback_data="cb:cappers")])
    return InlineKeyboardMarkup(rows)

# ════════════════════════════════════════════════════════════
#  HANDLERS
# ════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await get_or_create(
        update.effective_user.id,
        update.effective_user.username,
        update.effective_user.first_name,
    )
    await update.message.reply_text(
        f"👋 Привет, <b>{update.effective_user.first_name}</b>!\n\n"
        f"💰 Баланс: <b>{fmt_coins(user['coins'])}</b>\n\n"
        f"Делай ставки на реальные футбольные матчи.",
        reply_markup=kb_main(), parse_mode="HTML"
    )

async def cmd_kazikt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "👑 <b>Админ-панель</b>", reply_markup=kb_admin(), parse_mode="HTML"
    )

async def cmd_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    msg = await update.message.reply_text("⏳ Обновляю матчи и коэффициенты...")
    count = await fetch_upcoming_fixtures(force=True)
    await msg.edit_text(
        f"✅ Обновлено <b>{count}</b> матчей",
        parse_mode="HTML"
    )

# ── callbacks ─────────────────────────────────────────────────────────────────

async def _safe_edit(q, *args, **kwargs):
    """Обёртка над q.edit_message_text: глотает 'message is not modified'."""
    try:
        return await q.edit_message_text(*args, **kwargs)
    except Exception as e:
        msg = str(e).lower()
        if "message is not modified" in msg or "not modified" in msg:
            return None
        raise


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    # Обрезаем префикс "cb:" — все callback_data казибета префиксятся этим
    d_raw = q.data or ""
    d   = d_raw[3:] if d_raw.startswith("cb:") else d_raw
    uid = q.from_user.id
    await q.answer()

    try:
        await _on_callback_dispatch(q, ctx, d, uid)
    except Exception:
        logger.exception("casibet on_callback failed for data=%r uid=%s", d_raw, uid)
        try:
            await q.edit_message_text(
                "⚠️ Внутренняя ошибка при обработке кнопки.\n"
                "Сообщите админу; в логах — traceback.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="cb:main")]
                ]),
            )
        except Exception:
            # даже fallback не смог отредактировать — уже ничего не сделать
            pass


async def _on_callback_dispatch(q, ctx: ContextTypes.DEFAULT_TYPE, d: str, uid: int):
    if d == "kazik":
        # Возврат в главное меню Казимирно
        ctx.user_data.clear()
        try:
            import main as _main  # локальный импорт, чтобы избежать циклов
            await q.edit_message_text(
                _main.MAIN_MENU_TEXT,
                parse_mode="Markdown",
                reply_markup=_main.main_menu_keyboard(uid),
            )
        except Exception:
            logger.exception("casibet: failed to open Kazimirno main menu")
            await q.edit_message_text(
                "🏠 Отправь /menu чтобы открыть главное меню Казимирно.",
                parse_mode="HTML",
            )
        return

    if d == "main":
        ctx.user_data.clear()
        user = await get_player(uid)
        if not user:
            await q.edit_message_text("❌ Сначала зарегистрируйся в Казимире через /kazik",
                                       parse_mode="HTML")
            return
        await q.edit_message_text(
            f"🎯 <b>КАЗИБЕТ</b>\n💰 Баланс: <b>{fmt_coins(user['coins'])}</b>",
            reply_markup=kb_main(), parse_mode="HTML"
        )

    elif d == "matches":
        await q.edit_message_text(
            "⚽ <b>Выбери регион:</b>",
            reply_markup=kb_leagues(), parse_mode="HTML"
        )

    elif d.startswith("lcat_"):
        region = d.split("_", 1)[1]
        title = LEAGUE_REGIONS.get(region, "Регион")
        await q.edit_message_text(
            f"{title}\n\nВыбери раздел:",
            reply_markup=kb_league_groups(region), parse_mode="HTML",
        )

    elif d.startswith("lgrp_"):
        _, region, group = d.split("_", 2)
        # находим название подраздела для заголовка
        gtitle = next((t for c, t, _ in LEAGUE_GROUPS.get(region, []) if c == group), "Лиги")
        await q.edit_message_text(
            f"{gtitle}\n\nВыбери лигу:",
            reply_markup=kb_league_list(region, group), parse_mode="HTML",
        )

    elif d.startswith("league_"):
        league_id = int(d.split("_")[1])
        fixtures  = await load_fixtures(league_id)
        if not fixtures:
            grp = _league_group(league_id)
            if grp:
                region, group = grp
                groups = LEAGUE_GROUPS.get(region, [])
                back_cb = (
                    f"cb:lgrp_{region}_{group}" if len(groups) > 1 else f"cb:lcat_{region}"
                )
            else:
                back_cb = "cb:matches"
            await q.edit_message_text(
                f"😔 В <b>{LEAGUES.get(league_id,'?')}</b> нет предстоящих матчей.\n\n"
                f"Попробуй другую лигу.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К лигам", callback_data=back_cb)]
                ]),
                parse_mode="HTML"
            )
            return
        await q.edit_message_text(
            f"⚽ <b>{LEAGUES.get(league_id,'?')}</b>\nВыбери матч:",
            reply_markup=kb_fixtures(fixtures, league_id), parse_mode="HTML"
        )

    elif d.startswith("fix_"):
        fixture_id = int(d.split("_")[1])
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM cached_fixtures WHERE fixture_id=$1", fixture_id
            )
        if not row:
            await q.answer("Матч не найден", show_alert=True); return
        f  = dict(row)
        dt = datetime.fromtimestamp(f["start_ts"], tz=timezone.utc)
        o  = _odds_from_row(f)
        scorers = await load_fixture_scorers(fixture_id)

        src = "реальные" if (f.get("odds_home") and f.get("odds_draw") and f.get("odds_away")) else "расчётные"

        text = (
            f"⚽ <b>{f['home_team']} vs {f['away_team']}</b>\n"
            f"🏆 {f['league_name']}\n"
            f"📅 {dt.strftime('%d.%m.%Y %H:%M')} UTC\n\n"
            f"<b>Коэффициенты ({src}):</b>\n"
            f"1️⃣  {f['home_team']}: <b>x{o['home']}</b>\n"
            f"🤝  Ничья: <b>x{o['draw']}</b>\n"
            f"2️⃣  {f['away_team']}: <b>x{o['away']}</b>\n\n"
            f"<b>Тоталы:</b>  1.5 <b>{o['o15']}/{o['u15']}</b>  "
            f"2.5 <b>{o['o25']}/{o['u25']}</b>  3.5 <b>{o['o35']}/{o['u35']}</b>\n"
            f"<b>Обе забьют:</b>  Да <b>x{o['bttsy']}</b>  /  Нет <b>x{o['bttsn']}</b>\n"
            f"<b>Двойной шанс:</b>  1X <b>x{o['dc1x']}</b>  12 <b>x{o['dc12']}</b>  X2 <b>x{o['dcx2']}</b>\n"
            f"<b>Угловые 9.5:</b>  ТБ <b>x{o['c95o']}</b>  /  ТМ <b>x{o['c95u']}</b>\n"
            f"<b>ЖК 4.5:</b>  ТБ <b>x{o['yc45o']}</b>  /  ТМ <b>x{o['yc45u']}</b>"
        )
        await q.edit_message_text(
            text,
            reply_markup=kb_fixture_bet(
                fixture_id, f["home_team"], f["away_team"], o,
                has_scorers=bool(scorers),
            ),
            parse_mode="HTML"
        )

    elif d.startswith("gs_"):
        # Экран «Авторы голов» с пагинацией.  cb:gs_{fixture_id}_{page}
        parts      = d.split("_", 2)
        fixture_id = int(parts[1])
        page       = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        scorers    = await load_fixture_scorers(fixture_id)
        if not scorers:
            await q.answer("Нет данных по голлерам", show_alert=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT home_team, away_team FROM cached_fixtures WHERE fixture_id=$1",
                fixture_id,
            )
        home = row["home_team"] if row else ""
        away = row["away_team"] if row else ""
        await q.edit_message_text(
            f"👟 <b>Авторы голов</b>\n"
            f"⚽ {home} vs {away}\n\n"
            f"Выбери игрока (он должен забить хотя бы один гол):",
            reply_markup=kb_fixture_scorers(fixture_id, scorers, page),
            parse_mode="HTML",
        )

    elif d == "noop":
        await q.answer()

    elif d.startswith("bt_"):
        parts      = d.split("_", 2)
        fixture_id = int(parts[1])
        bet_on     = parts[2]
        if not _is_known_bet(bet_on):
            await q.answer("❌ Неизвестный рынок", show_alert=True); return
        await q.edit_message_text(
            "💵 Выбери сумму ставки:",
            reply_markup=kb_amounts(fixture_id, bet_on)
        )

    elif d.startswith("ba_"):
        parts      = d.split("_", 3)
        fixture_id = int(parts[1])
        bet_on     = parts[2]
        amount_str = parts[3]
        if amount_str == "custom":
            ctx.user_data["pending_bet"] = dict(fixture_id=fixture_id, bet_on=bet_on)
            ctx.user_data["fsm"]         = WAIT_BET_AMOUNT
            await q.edit_message_text(f"✍️ Введи сумму ({MIN_BET}–{MAX_BET:,} {COIN_EMOJI} {COIN_NAME}):")
            return
        await _do_bet(q, uid, fixture_id, bet_on, int(amount_str))

    elif d == "my_bets":
        bets     = await get_user_bets(uid)
        cbets    = await get_user_cbet_entries(uid)
        expresses = await get_user_express_bets(uid)
        if not bets and not cbets and not expresses:
            await q.edit_message_text("📋 У тебя пока нет ставок.", reply_markup=kb_back())
            return
        st  = {"pending": "⏳", "win": "✅", "lose": "❌", "refund": "↩️", "void": "↩️"}
        lines = ["📋 <b>Мои ставки</b>"]
        if bets:
            lines.append("\n<b>⚽ Матчи:</b>")
            for b in bets:
                extra = (
                    f" → +{int(b['amount']*b['odds']):,} {COIN_EMOJI}"
                    if b["result"] == "win" else ""
                )
                label = await _bet_display_label(b["bet_on"], b.get("match_id"))
                lines.append(
                    f"{st.get(b['result'],'❓')} {b['match_info']}\n"
                    f"   {label} | "
                    f"{b['amount']:,} {COIN_EMOJI} x{b['odds']}{extra}"
                )
        if expresses:
            lines.append("\n<b>🔗 Экспрессы:</b>")
            for e in expresses:
                if e["result"] == "win":
                    extra = f" → +{int(e['amount']*e['total_odds']):,} {COIN_EMOJI}"
                else:
                    extra = ""
                lines.append(
                    f"{st.get(e['result'],'❓')} Экспресс #{e['id']} · "
                    f"{e['amount']:,} {COIN_EMOJI} × x{e['total_odds']}{extra}"
                )
                for leg in e["legs"]:
                    label = await _bet_display_label(leg["bet_on"], leg.get("match_id"))
                    lines.append(
                        f"   {st.get(leg['result'],'❓')} {leg['match_info']} · "
                        f"{label} x{leg['odds']}"
                    )
        if cbets:
            lines.append("\n<b>🎲 Кастомные:</b>")
            for b in cbets:
                idx  = b["option_index"]
                opts = b["cbet_options"]
                opt_name = (
                    opts[idx].get("name", f"#{idx+1}")
                    if 0 <= idx < len(opts) else f"#{idx+1}"
                )
                if b["result"] == "win":
                    extra = f" → +{int(b['amount']*b['odds']):,} {COIN_EMOJI}"
                elif b["result"] == "refund":
                    extra = f" → возврат {b['amount']:,} {COIN_EMOJI}"
                else:
                    extra = ""
                lines.append(
                    f"{st.get(b['result'],'❓')} {b['cbet_title']}\n"
                    f"   📌 {opt_name} | "
                    f"{b['amount']:,} {COIN_EMOJI} x{b['odds']}{extra}"
                )
        await q.edit_message_text(
            "\n".join(lines), reply_markup=kb_back(), parse_mode="HTML"
        )

    elif d == "balance":
        user = await get_player(uid)
        await q.edit_message_text(
            f"💰 <b>Баланс: {fmt_coins(user['coins'])}</b>",
            reply_markup=kb_back(), parse_mode="HTML"
        )

    elif d == "bonus":
        user = await get_player(uid)
        last = user.get("last_bonus")
        now  = datetime.now(timezone.utc)
        if last:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            diff = (now - last).total_seconds()
            if diff < BONUS_COOLDOWN:
                h = int((BONUS_COOLDOWN - diff) // 3600)
                m = int(((BONUS_COOLDOWN - diff) % 3600) // 60)
                await q.answer(f"⏳ Следующий бонус через {h}ч {m}м", show_alert=True)
                return
        await set_last_bonus(uid)
        user = await get_player(uid)
        await q.edit_message_text(
            f"🎁 <b>Бонус получен!</b>\n\n➕ <b>+{fmt_coins(DAILY_BONUS)}</b>\n"
            f"Баланс: {fmt_coins(user['coins'])}",
            reply_markup=kb_back(), parse_mode="HTML"
        )

    elif d == "adm_stats" and uid == ADMIN_ID:
        st = await db_stats()
        await q.edit_message_text(
            f"📊 <b>Статистика</b>\n\n"
            f"👥 Пользователей: <b>{st['users']}</b>\n"
            f"📋 Всего ставок: <b>{st['total']}</b>\n"
            f"⏳ Активных: <b>{st['pending']}</b>\n"
            f"✅ Выиграно: <b>{st['won']}</b>\n"
            f"❌ Проиграно: <b>{st['lost']}</b>\n"
            f"💵 {COIN_NAME} у игроков: <b>{st['coins']:,}</b>",
            reply_markup=kb_admin(), parse_mode="HTML"
        )

    elif d == "adm_update" and uid == ADMIN_ID:
        await q.edit_message_text("⏳ Обновляю матчи...")
        count = await fetch_upcoming_fixtures(force=True)
        await q.edit_message_text(
            f"✅ Обновлено <b>{count}</b> матчей",
            reply_markup=kb_admin(), parse_mode="HTML"
        )

    elif d == "adm_give" and uid == ADMIN_ID:
        ctx.user_data["fsm"] = WAIT_GIVE_ID
        await q.edit_message_text("👤 Введи Telegram ID пользователя:")

    elif d == "adm_broadcast" and uid == ADMIN_ID:
        ctx.user_data["fsm"] = WAIT_BROADCAST
        await q.edit_message_text("📢 Введи текст рассылки:")

    elif d == "admin_back" and uid == ADMIN_ID:
        await q.edit_message_text(
            "👑 <b>Админ-панель</b>", reply_markup=kb_admin(), parse_mode="HTML"
        )

    # ── custom bets (user) ────────────────────────────────────────────────────
    elif d == "cbets":
        cbets = await list_open_custom_bets()
        if not cbets:
            await q.edit_message_text(
                "🎲 <b>Кастомные ставки</b>\n\nПока нет открытых ставок.",
                reply_markup=kb_back(), parse_mode="HTML"
            )
            return
        await q.edit_message_text(
            "🎲 <b>Кастомные ставки</b>\nВыбери ставку:",
            reply_markup=kb_cbets_list(cbets), parse_mode="HTML"
        )

    elif d.startswith("cbet_"):
        cbet_id = int(d.split("_", 1)[1])
        cb = await get_custom_bet(cbet_id)
        if not cb or cb["status"] != "open":
            await q.answer("Ставка недоступна", show_alert=True); return
        lines = [f"🎲 <b>{cb['title']}</b>\n\n<b>Варианты:</b>"]
        for i, opt in enumerate(cb["options"]):
            lines.append(f"{i+1}. {opt.get('name','')} — x{opt.get('odds',1.0)}")
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=kb_cbet_options(cbet_id, cb["options"]),
            parse_mode="HTML",
        )

    elif d.startswith("cbo_"):
        parts = d.split("_")
        cbet_id = int(parts[1]); option_index = int(parts[2])
        cb = await get_custom_bet(cbet_id)
        if not cb or cb["status"] != "open":
            await q.answer("Ставка недоступна", show_alert=True); return
        if option_index >= len(cb["options"]):
            await q.answer("Неверный вариант", show_alert=True); return
        opt = cb["options"][option_index]
        await q.edit_message_text(
            f"🎲 <b>{cb['title']}</b>\n"
            f"📌 {opt.get('name','')}  x{opt.get('odds',1.0)}\n\n"
            f"💵 Выбери сумму ставки:",
            reply_markup=kb_cbet_amounts(cbet_id, option_index),
            parse_mode="HTML",
        )

    elif d.startswith("cba_"):
        parts = d.split("_", 3)
        cbet_id = int(parts[1])
        option_index = int(parts[2])
        amount_str = parts[3]
        if amount_str == "custom":
            ctx.user_data["pending_cbet"] = dict(
                cbet_id=cbet_id, option_index=option_index
            )
            ctx.user_data["fsm"] = WAIT_CBET_AMOUNT
            await q.edit_message_text(
                f"✍️ Введи сумму ({MIN_BET}–{MAX_BET:,} {COIN_EMOJI} {COIN_NAME}):"
            )
            return
        await _do_cbet(q, uid, cbet_id, option_index, int(amount_str))

    # ── promo (user) ─────────────────────────────────────────────────────────
    elif d == "promo":
        await q.edit_message_text(
            "🎫 <b>Промокоды</b>\n\nВведи код, чтобы получить Винкоины.",
            reply_markup=kb_promo(), parse_mode="HTML"
        )

    elif d == "promo_enter":
        ctx.user_data["fsm"] = WAIT_PROMO_CODE
        await q.edit_message_text("🎟 Отправь промокод одним сообщением:")

    # ── запрос новой лиги (любой пользователь → уведомление админу) ───────────
    elif d == "req_league":
        ctx.user_data["fsm"] = WAIT_NEW_LEAGUE_REQUEST
        await q.edit_message_text(
            "📨 <b>Запрос новой лиги</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Хочешь, чтобы добавили новую лигу в КАЗИБЕТ?\n"
            "Напиши название лиги и страну одним сообщением.\n\n"
            "<i>Пример:</i>\n"
            "<code>Португалия — Примейра</code>\n"
            "<code>Турция — Суперлига</code>\n\n"
            "Запрос уйдёт админу, он рассмотрит.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Отмена", callback_data="cb:main")]
            ]),
            parse_mode="HTML",
        )

    # ── admin: custom bet create ─────────────────────────────────────────────
    elif d == "adm_cbet_new" and uid == ADMIN_ID:
        ctx.user_data["fsm"] = WAIT_CBET_TITLE
        await q.edit_message_text(
            "🎲 <b>Создание кастомной ставки</b>\n\n"
            "Отправь одним сообщением:\n"
            "• Первая строка — название ставки\n"
            "• Со второй строки — исходы через <code>|</code>, "
            f"до {MAX_CUSTOM_OPTIONS}:\n"
            "<code>Исход1 коэф1 | Исход2 коэф2 | …</code>\n\n"
            "Пример:\n"
            "<code>Кто победит?\n"
            "Петя 2.5 | Вася 1.8 | Ничья 3.2</code>",
            parse_mode="HTML",
        )

    elif d == "adm_cbet_settle" and uid == ADMIN_ID:
        cbets = await list_open_custom_bets()
        if not cbets:
            await q.edit_message_text(
                "Нет открытых кастомных ставок.",
                reply_markup=kb_admin(), parse_mode="HTML"
            )
            return
        await q.edit_message_text(
            "✅ <b>Закрыть кастомную ставку</b>\nВыбери ставку:",
            reply_markup=kb_cbet_settle_list(cbets), parse_mode="HTML"
        )

    elif d.startswith("scbet_") and uid == ADMIN_ID:
        cbet_id = int(d.split("_", 1)[1])
        cb = await get_custom_bet(cbet_id)
        if not cb or cb["status"] != "open":
            await q.answer("Ставка недоступна", show_alert=True); return
        lines = [f"🎲 <b>{cb['title']}</b>\n\nВыбери победивший вариант:"]
        for i, opt in enumerate(cb["options"]):
            lines.append(f"{i+1}. {opt.get('name','')} — x{opt.get('odds',1.0)}")
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=kb_cbet_settle_options(cbet_id, cb["options"]),
            parse_mode="HTML",
        )

    elif d.startswith("scbo_") and uid == ADMIN_ID:
        parts = d.split("_")
        cbet_id = int(parts[1])
        tail = parts[2]
        winning = None if tail == "cancel" else int(tail)
        notifs = await settle_custom_bet(cbet_id, winning)
        for n in notifs:
            try:
                if n["result"] == "win":
                    text = (
                        f"🎲 <b>Кастомная ставка сыграла!</b>\n"
                        f"💰 Ставка: <b>{fmt_coins(n['amount'])}</b>  x{n['odds']}\n"
                        f"🎉 Выигрыш: <b>+{fmt_coins(n['winnings'])}</b>"
                    )
                elif n["result"] == "refund":
                    text = (
                        f"🎲 <b>Кастомная ставка отменена.</b>\n"
                        f"↩️ Возвращено: <b>{fmt_coins(n['amount'])}</b>"
                    )
                else:
                    text = (
                        f"🎲 <b>Кастомная ставка не сыграла</b>\n"
                        f"💸 Потеряно: <b>{fmt_coins(n['amount'])}</b>"
                    )
                await ctx.bot.send_message(n["user_id"], text, parse_mode="HTML")
            except Exception:
                pass
        status = "отменена" if winning is None else f"закрыта (выиграл вариант #{winning+1})"
        await q.edit_message_text(
            f"✅ Ставка #{cbet_id} {status}. Уведомлено: {len(notifs)}",
            reply_markup=kb_admin(), parse_mode="HTML"
        )

    # ── admin: real bets — manual settle ────────────────────────────────────
    elif d == "adm_rbets" and uid == ADMIN_ID:
        await q.edit_message_text(
            "🎟 <b>Реальные ставки — ручное закрытие</b>\n\n"
            "• «🏟 Закрыть по матчу» — введи итоговый счёт + опц. угловые/жк/голлеры, "
            "и все ставки на этот матч пересчитаются автоматически.\n"
            "• «📋 Все открытые ставки» — список всех pending-ставок, можно "
            "выбрать конкретную и поставить win / lose / void.",
            reply_markup=kb_admin_rbets(), parse_mode="HTML",
        )

    elif d.startswith("adm_rbf_list_") and uid == ADMIN_ID:
        try:
            page = int(d[len("adm_rbf_list_"):])
        except ValueError:
            page = 0
        fixtures = await admin_get_pending_fixtures()
        if not fixtures:
            await q.edit_message_text(
                "🏟 <b>Закрыть по матчу</b>\n\nНет матчей с открытыми ставками.",
                reply_markup=kb_admin_rbets(), parse_mode="HTML",
            )
            return
        await q.edit_message_text(
            "🏟 <b>Выбери матч:</b>",
            reply_markup=kb_admin_rbets_list_fixtures(fixtures, page),
            parse_mode="HTML",
        )

    elif d.startswith("adm_rbf_input_") and uid == ADMIN_ID:
        try:
            fixture_id = int(d[len("adm_rbf_input_"):])
        except ValueError:
            await q.answer("Bad fixture id", show_alert=True); return
        ctx.user_data["fsm"] = WAIT_RB_FIXTURE_RESULT
        ctx.user_data["rb_fixture_id"] = fixture_id
        await q.edit_message_text(
            "✏️ <b>Введи результат матча</b>\n\n"
            "Формат (одной отправкой):\n"
            "<code>2:1\n"
            "corners=11\n"
            "yellows=3\n"
            "scorers=Холланд, Салах</code>\n\n"
            "Обязательная только первая строка (счёт). Остальные опционально:\n"
            "• <b>corners=</b> — всего угловых (для ТБ/ТМ 9.5).\n"
            "• <b>yellows=</b> — всего ЖК (для ТБ/ТМ 4.5).\n"
            "• <b>scorers=</b> — авторы голов через запятую "
            "(для ставок «автор гола»). Если не введёшь — голлерные ставки "
            "вернутся как void.\n\n"
            "После отправки все ставки на этот матч закроются автоматически.",
            parse_mode="HTML",
        )

    elif d.startswith("adm_rbf_bets_") and uid == ADMIN_ID:
        # adm_rbf_bets_<fixture>_<page>
        rest = d[len("adm_rbf_bets_"):]
        try:
            fix_str, page_str = rest.rsplit("_", 1)
            fixture_id = int(fix_str)
            page = int(page_str)
        except (ValueError, AttributeError):
            await q.answer("Bad payload", show_alert=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            total_row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM bets WHERE match_id=$1 "
                "AND result='pending'", str(fixture_id),
            )
            total = int(total_row["n"]) if total_row else 0
            rows = await conn.fetch(
                "SELECT * FROM bets WHERE match_id=$1 AND result='pending' "
                "ORDER BY created_at DESC LIMIT 10 OFFSET $2",
                str(fixture_id), page * 10,
            )
        bets = [dict(r) for r in rows]
        if not bets:
            await q.edit_message_text(
                f"📋 По матчу #{fixture_id} нет открытых одиночных ставок.",
                reply_markup=kb_admin_rb_fixture(fixture_id),
                parse_mode="HTML",
            )
            return
        # Reuse the singles list keyboard but route Back to fixture screen.
        rows_kb: list[list[InlineKeyboardButton]] = []
        for b in bets:
            label = await _bet_display_label(b["bet_on"], int(b["match_id"]))
            rows_kb.append([InlineKeyboardButton(
                f"#{b['id']} · {fmt_coins(b['amount'])} · {label[:25]}",
                callback_data=f"cb:adm_rbs_{b['id']}",
            )])
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                "⬅️", callback_data=f"cb:adm_rbf_bets_{fixture_id}_{page-1}"))
        if (page + 1) * 10 < total:
            nav.append(InlineKeyboardButton(
                "➡️", callback_data=f"cb:adm_rbf_bets_{fixture_id}_{page+1}"))
        if nav:
            rows_kb.append(nav)
        rows_kb.append([InlineKeyboardButton(
            "◀️ Назад", callback_data=f"cb:adm_rbf_{fixture_id}")])
        await q.edit_message_text(
            f"📋 <b>Ставки на матч #{fixture_id}</b> ({total} pending)\n"
            f"Выбери ставку для ручного закрытия:",
            reply_markup=InlineKeyboardMarkup(rows_kb), parse_mode="HTML",
        )

    elif d.startswith("adm_rbf_") and uid == ADMIN_ID:
        # Fallback: cb:adm_rbf_<fixture_id> → fixture menu.
        try:
            fixture_id = int(d[len("adm_rbf_"):])
        except ValueError:
            await q.answer("Bad fixture id", show_alert=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            f = await conn.fetchrow(
                "SELECT * FROM cached_fixtures WHERE fixture_id=$1", fixture_id,
            )
            n_singles = int((await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM bets WHERE match_id=$1 AND result='pending'",
                str(fixture_id),
            ))["n"])
            n_legs = int((await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM express_bet_legs "
                "WHERE match_id=$1 AND result='pending'",
                str(fixture_id),
            ))["n"])
        title = (f["home_team"] + " – " + f["away_team"]) if f else "?"
        date = f["match_date"] if f else "?"
        await q.edit_message_text(
            f"🏟 <b>{title}</b>\n"
            f"📅 {date}\n"
            f"📋 Открытых ставок: {n_singles} одиночных + {n_legs} в экспрессах\n\n"
            f"Выбери действие:",
            reply_markup=kb_admin_rb_fixture(fixture_id), parse_mode="HTML",
        )

    elif d.startswith("adm_rbl_") and uid == ADMIN_ID:
        try:
            page = int(d[len("adm_rbl_"):])
        except ValueError:
            page = 0
        bets, total = await admin_get_pending_singles(page * 10, 10)
        if not bets:
            await q.edit_message_text(
                "📋 Нет открытых одиночных ставок.",
                reply_markup=kb_admin_rbets(), parse_mode="HTML",
            )
            return
        # Render with proper labels
        rows_kb: list[list[InlineKeyboardButton]] = []
        for b in bets:
            try:
                label = await _bet_display_label(b["bet_on"], int(b["match_id"]))
            except Exception:
                label = b["bet_on"]
            rows_kb.append([InlineKeyboardButton(
                f"#{b['id']} · {fmt_coins(b['amount'])} · {label[:22]}",
                callback_data=f"cb:adm_rbs_{b['id']}",
            )])
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"cb:adm_rbl_{page-1}"))
        if (page + 1) * 10 < total:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"cb:adm_rbl_{page+1}"))
        if nav:
            rows_kb.append(nav)
        rows_kb.append([InlineKeyboardButton("◀️ Назад", callback_data="cb:adm_rbets")])
        await q.edit_message_text(
            f"📋 <b>Открытые ставки</b> · стр. {page+1} (всего {total})",
            reply_markup=InlineKeyboardMarkup(rows_kb), parse_mode="HTML",
        )

    elif d.startswith("adm_rbcl_") and uid == ADMIN_ID:
        # Список ЗАКРЫТЫХ ставок (для смены исхода).
        try:
            page = int(d[len("adm_rbcl_"):])
        except ValueError:
            page = 0
        bets, total = await admin_get_settled_singles(page * 10, 10)
        if not bets:
            await q.edit_message_text(
                "🔁 Нет закрытых ставок.",
                reply_markup=kb_admin_rbets(), parse_mode="HTML",
            )
            return
        rows_kb: list[list[InlineKeyboardButton]] = []
        for b in bets:
            try:
                label = await _bet_display_label(b["bet_on"], int(b["match_id"]))
            except Exception:
                label = b["bet_on"]
            res_emoji = {"win": "✅", "lose": "❌", "void": "↩️"}.get(b["result"], "❓")
            rows_kb.append([InlineKeyboardButton(
                f"{res_emoji} #{b['id']} · {fmt_coins(b['amount'])} · {label[:20]}",
                callback_data=f"cb:adm_rbs_{b['id']}",
            )])
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"cb:adm_rbcl_{page-1}"))
        if (page + 1) * 10 < total:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"cb:adm_rbcl_{page+1}"))
        if nav:
            rows_kb.append(nav)
        rows_kb.append([InlineKeyboardButton("◀️ Назад", callback_data="cb:adm_rbets")])
        await q.edit_message_text(
            f"🔁 <b>Закрытые ставки</b> · стр. {page+1} (всего {total})\n"
            f"Кликни любую — сможешь поменять исход (баланс юзера "
            f"скорректируется автоматически).",
            reply_markup=InlineKeyboardMarkup(rows_kb), parse_mode="HTML",
        )

    elif d.startswith("adm_rbsx_") and uid == ADMIN_ID:
        # adm_rbsx_<bet_id>_<outcome>
        rest = d[len("adm_rbsx_"):]
        try:
            bid_str, outcome = rest.rsplit("_", 1)
            bet_id = int(bid_str)
        except ValueError:
            await q.answer("Bad payload", show_alert=True); return
        ok, msg = await _admin_settle_single_bet(ctx, bet_id, outcome)
        if not ok:
            await q.answer(f"Не удалось: {msg}", show_alert=True); return
        await q.edit_message_text(
            f"✅ Ставка #{bet_id} закрыта: <b>{msg}</b>",
            reply_markup=kb_admin_rbets(), parse_mode="HTML",
        )

    elif d.startswith("adm_rbs_") and uid == ADMIN_ID:
        # Подробности и кнопки win/lose/void для одиночной ставки.
        try:
            bet_id = int(d[len("adm_rbs_"):])
        except ValueError:
            await q.answer("Bad bet id", show_alert=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM bets WHERE id=$1", bet_id)
        if not row:
            await q.answer("Ставка не найдена", show_alert=True); return
        b = dict(row)
        try:
            label = await _bet_display_label(b["bet_on"], int(b["match_id"]))
        except Exception:
            label = b["bet_on"]
        potential = int(int(b["amount"]) * float(b["odds"]))
        status = b["result"] or "pending"
        is_closed = status in ("win", "lose", "void")
        hint = (
            "\n\n💡 Эта ставка уже закрыта. Можешь поменять исход — "
            "баланс игрока скорректируется автоматически "
            "(старая выплата откатится, новая применится)."
        ) if is_closed else ""
        text = (
            f"🎟 <b>Ставка #{b['id']}</b>\n\n"
            f"👤 <code>{b['user_id']}</code>\n"
            f"🏆 {b['match_info']}\n"
            f"📌 {label}\n"
            f"💰 {fmt_coins(int(b['amount']))} · x{float(b['odds']):.2f}\n"
            f"🎉 Потенциал: {fmt_coins(potential)}\n"
            f"📊 Статус: <b>{status}</b>" + hint
        )
        back = "adm_rbcl_0" if is_closed else "adm_rbl_0"
        await q.edit_message_text(
            text,
            reply_markup=kb_admin_rb_bet_actions(bet_id, back=back),
            parse_mode="HTML",
        )

    # ── admin: promo create ─────────────────────────────────────────────────
    elif d == "adm_promo_new" and uid == ADMIN_ID:
        await q.edit_message_text(
            "🎁 <b>Создание промокода</b>\n\n"
            "Используй команду:\n"
            "<code>/createpromo НАЗВАНИЕ КОИНЫ АКТИВАЦИИ</code>\n\n"
            "Пример:\n<code>/createpromo WELCOME 500 100</code>",
            reply_markup=kb_admin(), parse_mode="HTML",
        )

    # ── admin: league requests ─────────────────────────────────────────────
    elif d == "adm_league_reqs" and uid == ADMIN_ID:
        try:
            reqs = await db_league_requests_list(limit=20)
        except Exception as e:
            await q.edit_message_text(
                f"❌ Ошибка: <code>{e}</code>",
                reply_markup=kb_admin(), parse_mode="HTML",
            )
            return
        if not reqs:
            await q.edit_message_text(
                "📨 <b>Запросы новых лиг</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<i>Пока нет запросов.</i>",
                reply_markup=kb_admin(), parse_mode="HTML",
            )
            return
        lines = ["📨 <b>Запросы новых лиг</b>", "━━━━━━━━━━━━━━━━━━━━"]
        status_emoji = {
            "pending":  "🟡",
            "accepted": "✅",
            "rejected": "❌",
        }
        rows_kb = []
        for r in reqs:
            se = status_emoji.get(r["status"], "•")
            when = r["created_at"].strftime("%d.%m %H:%M")
            uname = r["username"] or f"id{r['tg_id']}"
            text = (r["request_text"] or "")[:60]
            lines.append(
                f"\n{se} <b>#{r['id']}</b>  <code>{when}</code>  "
                f"{uname}\n   🏆 {text}"
            )
            if r["status"] == "pending":
                rows_kb.append([
                    InlineKeyboardButton(
                        f"✅ #{r['id']}",
                        callback_data=f"cb:lreq_ok:{r['id']}",
                    ),
                    InlineKeyboardButton(
                        f"❌ #{r['id']}",
                        callback_data=f"cb:lreq_no:{r['id']}",
                    ),
                ])
        rows_kb.append([InlineKeyboardButton("◀️ Админ-меню",
                                             callback_data="cb:main")])
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(rows_kb),
            parse_mode="HTML",
        )

    elif d.startswith("lreq_ok:") and uid == ADMIN_ID:
        req_id = int(d.split(":", 1)[1])
        ok = await db_league_request_set_status(req_id, "accepted")
        await q.answer("Принято" if ok else "Не найдено", show_alert=False)
        await q.edit_message_text(
            f"✅ Запрос #{req_id} принят.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 К списку",
                                      callback_data="cb:adm_league_reqs")]
            ]),
            parse_mode="HTML",
        )

    elif d.startswith("lreq_no:") and uid == ADMIN_ID:
        req_id = int(d.split(":", 1)[1])
        ok = await db_league_request_set_status(req_id, "rejected")
        await q.answer("Отклонено" if ok else "Не найдено", show_alert=False)
        await q.edit_message_text(
            f"❌ Запрос #{req_id} отклонён.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 К списку",
                                      callback_data="cb:adm_league_reqs")]
            ]),
            parse_mode="HTML",
        )

    # ── express bets (user) ─────────────────────────────────────────────────
    elif d == "express":
        legs = ctx.user_data.get("express_legs", [])
        user = await get_player(uid)
        balance = user["coins"] if user else 0
        text = _express_text(legs, balance)
        await q.edit_message_text(
            text, reply_markup=kb_express_builder(legs, has_balance=balance >= MIN_BET),
            parse_mode="HTML",
        )

    elif d == "exp_add":
        legs = ctx.user_data.get("express_legs", [])
        if len(legs) >= MAX_EXPRESS_LEGS:
            await q.answer(f"Максимум {MAX_EXPRESS_LEGS} событий", show_alert=True); return
        await q.edit_message_text(
            "⚽ <b>Выбери регион:</b>",
            reply_markup=kb_express_leagues(), parse_mode="HTML"
        )

    elif d.startswith("excat_"):
        region = d.split("_", 1)[1]
        title = LEAGUE_REGIONS.get(region, "Регион")
        await q.edit_message_text(
            f"{title}\n\nВыбери раздел:",
            reply_markup=kb_express_groups(region), parse_mode="HTML",
        )

    elif d.startswith("exgrp_"):
        _, region, group = d.split("_", 2)
        gtitle = next((t for c, t, _ in LEAGUE_GROUPS.get(region, []) if c == group), "Лиги")
        await q.edit_message_text(
            f"{gtitle}\n\nВыбери лигу:",
            reply_markup=kb_express_league_list(region, group), parse_mode="HTML",
        )

    elif d.startswith("exl_"):
        league_id = int(d.split("_")[1])
        fixtures  = await load_fixtures(league_id)
        legs = ctx.user_data.get("express_legs", [])
        used = {int(l["match_id"]) for l in legs}
        # отсеиваем уже добавленные матчи
        available = [f for f in fixtures if f["fixture_id"] not in used]
        if not available:
            grp = _league_group(league_id)
            if grp:
                region, group = grp
                groups = LEAGUE_GROUPS.get(region, [])
                back_cb = (
                    f"cb:exgrp_{region}_{group}" if len(groups) > 1 else f"cb:excat_{region}"
                )
            else:
                back_cb = "cb:exp_add"
            await q.edit_message_text(
                f"😔 В <b>{LEAGUES.get(league_id,'?')}</b> нет доступных матчей для экспресса.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К лигам", callback_data=back_cb)]
                ]), parse_mode="HTML"
            )
            return
        await q.edit_message_text(
            f"⚽ <b>{LEAGUES.get(league_id,'?')}</b>\nВыбери матч:",
            reply_markup=kb_express_fixtures(available, used, league_id),
            parse_mode="HTML",
        )

    elif d.startswith("exf_"):
        fixture_id = int(d.split("_")[1])
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM cached_fixtures WHERE fixture_id=$1", fixture_id
            )
        if not row:
            await q.answer("Матч не найден", show_alert=True); return
        f  = dict(row)
        o  = _odds_from_row(f)
        dt = datetime.fromtimestamp(f["start_ts"], tz=timezone.utc)
        scorers = await load_fixture_scorers(fixture_id)
        await q.edit_message_text(
            f"🔗 <b>Экспресс: выбор исхода</b>\n\n"
            f"⚽ {f['home_team']} vs {f['away_team']}\n"
            f"🏆 {f['league_name']}\n"
            f"🕐 {dt.strftime('%d.%m %H:%M UTC')}\n\n"
            f"Выбери исход:",
            reply_markup=kb_express_pick_bet(
                fixture_id, f["home_team"], f["away_team"], o,
                has_scorers=bool(scorers),
            ),
            parse_mode="HTML",
        )

    elif d.startswith("exgs_"):
        parts      = d.split("_", 2)
        fixture_id = int(parts[1])
        page       = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        scorers    = await load_fixture_scorers(fixture_id)
        if not scorers:
            await q.answer("Нет данных по голлерам", show_alert=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT home_team, away_team FROM cached_fixtures WHERE fixture_id=$1",
                fixture_id,
            )
        home = row["home_team"] if row else ""
        away = row["away_team"] if row else ""
        await q.edit_message_text(
            f"🔗 <b>Экспресс · Авторы голов</b>\n"
            f"⚽ {home} vs {away}\n\n"
            f"Выбери игрока (он должен забить хотя бы один гол):",
            reply_markup=kb_express_scorers(fixture_id, scorers, page),
            parse_mode="HTML",
        )

    elif d.startswith("exb_"):
        parts = d.split("_", 2)
        fixture_id = int(parts[1])
        bet_on     = parts[2]
        if not _is_known_bet(bet_on):
            await q.answer("❌ Неизвестный рынок", show_alert=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM cached_fixtures WHERE fixture_id=$1", fixture_id
            )
        if not row:
            await q.answer("Матч не найден", show_alert=True); return
        f    = dict(row)
        odds = await _resolve_bet_odds(f, bet_on)
        if odds is None:
            await q.answer("❌ Нет коэффициента", show_alert=True); return
        legs = ctx.user_data.setdefault("express_legs", [])
        if any(int(l["match_id"]) == fixture_id for l in legs):
            await q.answer("Матч уже в экспрессе", show_alert=True); return
        if len(legs) >= MAX_EXPRESS_LEGS:
            await q.answer(f"Максимум {MAX_EXPRESS_LEGS} событий", show_alert=True); return
        bet_label = await _bet_display_label(bet_on, fixture_id)
        legs.append({
            "match_id":   fixture_id,
            "match_info": f"{f['home_team']} vs {f['away_team']}",
            "bet_on":     bet_on,
            "bet_label":  bet_label,
            "odds":       float(odds),
            "league":     f.get("league_name", ""),
        })
        user    = await get_player(uid)
        balance = user["coins"] if user else 0
        await q.edit_message_text(
            _express_text(legs, balance),
            reply_markup=kb_express_builder(legs, has_balance=balance >= MIN_BET),
            parse_mode="HTML",
        )

    elif d.startswith("exp_rm_"):
        idx  = int(d.split("_")[2])
        legs = ctx.user_data.get("express_legs", [])
        if 0 <= idx < len(legs):
            legs.pop(idx)
        user    = await get_player(uid)
        balance = user["coins"] if user else 0
        await q.edit_message_text(
            _express_text(legs, balance),
            reply_markup=kb_express_builder(legs, has_balance=balance >= MIN_BET),
            parse_mode="HTML",
        )

    elif d == "exp_clear":
        ctx.user_data.pop("express_legs", None)
        await q.edit_message_text(
            _express_text([], 0),
            reply_markup=kb_express_builder([], has_balance=False),
            parse_mode="HTML",
        )

    elif d == "exp_place":
        legs = ctx.user_data.get("express_legs", [])
        if len(legs) < MIN_EXPRESS_LEGS:
            await q.answer(f"Нужно минимум {MIN_EXPRESS_LEGS} события", show_alert=True)
            return
        await q.edit_message_text(
            "💵 Выбери сумму ставки:", reply_markup=kb_express_amounts()
        )

    elif d.startswith("exa_"):
        val = d.split("_", 1)[1]
        if val == "custom":
            ctx.user_data["fsm"] = WAIT_EXPRESS_AMOUNT
            await q.edit_message_text(
                f"✍️ Введи сумму ({MIN_BET}–{MAX_BET:,} {COIN_EMOJI} {COIN_NAME}):"
            )
            return
        try:
            amount = int(val)
        except ValueError:
            await q.answer("Неверная сумма", show_alert=True); return
        await _do_express(q, ctx, uid, amount)

    # ── cappers ─────────────────────────────────────────────────────────────
    elif d == "cappers":
        user = await get_player(uid)
        is_capper = bool(user and user.get("is_capper"))
        test_taken = bool(user and user.get("capper_test_taken"))
        # Тест доступен, только если игрок ещё ни разу его не проходил и не капер
        can_test = (not is_capper) and (not test_taken)
        if is_capper:
            status = "✅ Ты капер — можешь делиться ставками и зарабатывать с фолловеров."
        elif can_test:
            status = (
                f"📜 Стань капером: пройди тест "
                f"({len(CAPPER_QUIZ)} вопросов, {CAPPER_QUIZ_PASS}+ верных).\n"
                f"⚠️ Тест можно пройти <b>только один раз</b>.\n"
                f"Капер делится ставками и получает "
                f"{CAPPER_WIN_SHARE_PCT}% от выигрышей и "
                f"{CAPPER_LOSE_SHARE_PCT}% от проигрышей фолловеров."
            )
        else:
            status = (
                "❌ Тест капера уже пройден — повторное прохождение недоступно.\n"
                "Если хочешь статус капера — попроси админа "
                "(<code>/makecapper USER_ID</code>)."
            )
        await q.edit_message_text(
            f"🎯 <b>Каперы</b>\n\n{status}",
            reply_markup=kb_cappers_menu(is_capper, can_test),
            parse_mode="HTML",
        )

    elif d == "cap_info":
        text = (
            "ℹ️ <b>Как работает каперство</b>\n\n"
            "<b>Кто такой капер</b>\n"
            "Капер — игрок, который делится своими ставками и экспрессами "
            "с остальными. Его типы видны всем в ленте, и их можно повторять "
            "одним тапом.\n\n"
            "<b>Как стать капером</b>\n"
            f"• Пройти тест из {len(CAPPER_QUIZ)} вопросов. Нужно минимум "
            f"<b>{CAPPER_QUIZ_PASS} правильных</b>.\n"
            "• Тест можно пройти <b>только один раз</b>. "
            "Если не сдал — повторных попыток не будет.\n"
            "• В этом случае статус капера можно получить только от админа "
            "(<code>/makecapper USER_ID</code>).\n\n"
            "<b>Что делает капер</b>\n"
            "• В «📝 Поделиться моей ставкой» выбирает pending ставку или "
            "экспресс → появляется тип в общей ленте.\n"
            "• Одну и ту же ставку/экспресс можно запостить один раз — "
            "повторный клик просто вернёт тот же тип.\n\n"
            "<b>Сколько платят фолловеры</b>\n"
            f"• Если фолловер <b>выиграл</b> по тип капера → капер получает "
            f"<b>{CAPPER_WIN_SHARE_PCT}%</b> от выигрыша фолловера.\n"
            f"• Если фолловер <b>проиграл</b> → капер получает "
            f"<b>{CAPPER_LOSE_SHARE_PCT}%</b> от суммы ставки фолловера.\n"
            "• За свои собственные ставки капер ничего доп. не получает "
            "(только сам выигрыш, если зашла).\n\n"
            "<b>Как всё это считается</b>\n"
            "• Автоматически при расчёте матча/экспресса в фоновой джобе.\n"
            "• Фолловеры видят у себя уведомление «Ставка сыграла/не сыграла», "
            "капер в этот же момент получает «🎯 Доход капера» с суммой.\n"
        )
        await q.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ К каперам", callback_data="cb:cappers")]
            ]),
            parse_mode="HTML",
        )

    elif d == "cap_feed":
        try:
            tips = await list_recent_tips(limit=15)
        except Exception as e:
            logger.exception("cap_feed load failed: %s", e)
            tips = []
        if not tips:
            await q.edit_message_text(
                "📰 Лента типов пуста. Пока никто не делился активными ставками.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К каперам", callback_data="cb:cappers")]
                ]),
            )
            return
        try:
            await q.edit_message_text(
                f"📰 <b>Лента типов</b> — {len(tips)} активных\nВыбери тип:",
                reply_markup=kb_cap_feed(tips), parse_mode="HTML",
            )
        except Exception as e:
            logger.exception("cap_feed render failed: %s", e)
            await q.edit_message_text(
                "⚠️ Не удалось загрузить ленту типов. Попробуйте позже.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ К каперам", callback_data="cb:cappers")]
                ]),
            )

    elif d.startswith("cap_tip_"):
        tip_id = int(d.split("_")[2])
        tip = await get_tip(tip_id)
        if not tip:
            await q.answer("Тип не найден", show_alert=True); return
        summary = await get_tip_summary(tip)
        name = tip.get("capper_username") or tip.get("capper_name") or str(tip["capper_id"])
        can_follow = tip["capper_id"] != uid
        await q.edit_message_text(
            f"🎯 <b>Тип #{tip['id']}</b> от <b>{name}</b>\n\n{summary}",
            reply_markup=kb_cap_tip(tip, can_follow),
            parse_mode="HTML",
        )

    elif d.startswith("cap_follow_"):
        tip_id = int(d.split("_")[2])
        tip = await get_tip(tip_id)
        if not tip:
            await q.answer("Тип не найден", show_alert=True); return
        if tip["capper_id"] == uid:
            await q.answer("Нельзя ставить за собой", show_alert=True); return
        await q.edit_message_text(
            "💵 Выбери сумму:", reply_markup=kb_cap_follow_amounts(tip_id)
        )

    elif d.startswith("cap_fa_"):
        _, _, tip_id_str, val = d.split("_", 3)
        tip_id = int(tip_id_str)
        if val == "custom":
            ctx.user_data["fsm"] = WAIT_CAPPER_FOLLOW_AMT
            ctx.user_data["cap_follow_tip"] = tip_id
            await q.edit_message_text(
                f"✍️ Введи сумму ({MIN_BET}–{MAX_BET:,} {COIN_EMOJI} {COIN_NAME}):"
            )
            return
        try:
            amount = int(val)
        except ValueError:
            await q.answer("Неверная сумма", show_alert=True); return
        await _follow_tip(q, uid, tip_id, amount)

    elif d == "cap_my_bets":
        user = await get_player(uid)
        if not user or not user.get("is_capper"):
            await q.answer("Только для каперов", show_alert=True); return
        # только pending ставки и экспрессы
        all_bets = await get_user_bets(uid)
        pending_bets = [b for b in all_bets if b["result"] == "pending"]
        all_exps = await get_user_express_bets(uid)
        pending_exps = [e for e in all_exps if e["result"] == "pending"]
        await q.edit_message_text(
            "📝 <b>Поделиться ставкой</b>\n\nВыбери, какой тип опубликовать:",
            reply_markup=kb_cap_share_list(pending_bets, pending_exps),
            parse_mode="HTML",
        )

    elif d.startswith("cap_share_b_"):
        bet_id = int(d.split("_")[3])
        user = await get_player(uid)
        if not user or not user.get("is_capper"):
            await q.answer("Только для каперов", show_alert=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM bets WHERE id=$1 AND user_id=$2", bet_id, uid
            )
        if not row:
            await q.answer("Ставка не найдена", show_alert=True); return
        existing = await get_tip_for_bet(bet_id)
        if existing:
            tip_id = existing["id"]
        else:
            tip_id = await create_tip(uid, "single", bet_id)
        await q.edit_message_text(
            f"✅ <b>Тип опубликован!</b>\n#{tip_id} — одиночная ставка.\n\n"
            f"Теперь она видна в «🎯 Каперы → 📰 Лента типов».",
            reply_markup=kb_cappers_menu(True, False), parse_mode="HTML",
        )

    elif d.startswith("cap_share_e_"):
        exp_id = int(d.split("_")[3])
        user = await get_player(uid)
        if not user or not user.get("is_capper"):
            await q.answer("Только для каперов", show_alert=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM express_bets WHERE id=$1 AND user_id=$2", exp_id, uid
            )
        if not row:
            await q.answer("Экспресс не найден", show_alert=True); return
        existing = await get_tip_for_express(exp_id)
        if existing:
            tip_id = existing["id"]
        else:
            tip_id = await create_tip(uid, "express", exp_id)
        await q.edit_message_text(
            f"✅ <b>Тип опубликован!</b>\n#{tip_id} — экспресс.\n\n"
            f"Теперь он виден в «🎯 Каперы → 📰 Лента типов».",
            reply_markup=kb_cappers_menu(True, False), parse_mode="HTML",
        )

    # ── quiz для становления капером ────────────────────────────────────────
    elif d == "cap_quiz_start":
        user = await get_player(uid)
        if user and user.get("is_capper"):
            await q.answer("Ты уже капер", show_alert=True); return
        if user and user.get("capper_test_taken"):
            await q.answer("Тест уже пройден — повторно недоступно", show_alert=True); return
        ctx.user_data["cap_quiz_score"] = 0
        await _show_quiz_question(q, 0)

    elif d.startswith("cap_qa_"):
        parts = d.split("_")
        qidx = int(parts[2]); ans = int(parts[3])
        if qidx >= len(CAPPER_QUIZ):
            return
        _, _, correct = CAPPER_QUIZ[qidx]
        score = ctx.user_data.get("cap_quiz_score", 0)
        if ans == correct:
            score += 1
        ctx.user_data["cap_quiz_score"] = score
        next_idx = qidx + 1
        if next_idx < len(CAPPER_QUIZ):
            await _show_quiz_question(q, next_idx)
        else:
            # финал — тест считается пройденным (один раз, без повторных попыток)
            ctx.user_data.pop("cap_quiz_score", None)
            await mark_capper_test_taken(uid)
            if score >= CAPPER_QUIZ_PASS:
                await set_capper(uid, True)
                await q.edit_message_text(
                    f"🎉 <b>Поздравляю!</b> {score}/{len(CAPPER_QUIZ)}.\n"
                    f"Ты теперь капер — можешь делиться ставками и зарабатывать на фолловерах.",
                    reply_markup=kb_main(), parse_mode="HTML",
                )
            else:
                await q.edit_message_text(
                    f"❌ Тест не пройден: {score}/{len(CAPPER_QUIZ)} "
                    f"(нужно {CAPPER_QUIZ_PASS}+).\n"
                    f"Повторно пройти тест нельзя. "
                    f"Статус капера можно получить только через админа.",
                    reply_markup=kb_main(), parse_mode="HTML",
                )


# ── place bet ─────────────────────────────────────────────────────────────────

async def _bet_title(bet_on: str, fixture_id: int, home: str, away: str) -> str:
    """Человеческое название ставки для конкретного матча (async из-за голлеров)."""
    if bet_on == "home": return f"🏠 {home}"
    if bet_on == "away": return f"✈️ {away}"
    if bet_on == "draw": return "🤝 Ничья"
    if bet_on.startswith(GOAL_SCORER_PREFIX):
        return await _bet_display_label(bet_on, fixture_id)
    return BET_LABELS.get(bet_on, bet_on)


async def _resolve_bet_odds(fixture_row: dict, bet_on: str) -> float | None:
    """Возвращает коэффициент ставки: для BET_LABELS — из cached_fixtures,
    для голлеров — из fixture_scorers. None если не нашли (ошибочный код).
    """
    if bet_on in BET_LABELS:
        return _odds_from_row(fixture_row)[bet_on]
    if bet_on.startswith(GOAL_SCORER_PREFIX):
        tail = bet_on[len(GOAL_SCORER_PREFIX):]
        if not tail.isdigit():
            return None
        s = await get_fixture_scorer(fixture_row["fixture_id"], int(tail))
        return float(s["odds_any"]) if s else None
    return None


async def _do_bet(q, uid, fixture_id, bet_on, amount):
    user = await get_player(uid)
    if not user or user["coins"] < amount:
        await q.answer("❌ Недостаточно Винкоинов!", show_alert=True); return
    if not MIN_BET <= amount <= MAX_BET:
        await q.answer(f"❌ От {MIN_BET} до {MAX_BET:,}", show_alert=True); return
    if not _is_known_bet(bet_on):
        await q.answer("❌ Неизвестный рынок", show_alert=True); return

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM cached_fixtures WHERE fixture_id=$1", fixture_id
        )
    if not row:
        await q.answer("Матч не найден", show_alert=True); return

    f    = dict(row)
    odds = await _resolve_bet_odds(f, bet_on)
    if odds is None:
        await q.answer("❌ Нет коэффициента", show_alert=True); return

    ok = await save_bet(
        uid, fixture_id, f"{f['home_team']} vs {f['away_team']}", bet_on, amount, odds
    )
    if not ok:
        await q.answer("❌ Недостаточно Винкоинов!", show_alert=True)
        return
    user  = await get_player(uid)
    title = await _bet_title(bet_on, fixture_id, f["home_team"], f["away_team"])

    await q.edit_message_text(
        f"✅ <b>Ставка принята!</b>\n\n"
        f"⚽ {f['home_team']} vs {f['away_team']}\n"
        f"🏆 {f['league_name']}\n"
        f"📌 {title}\n"
        f"💰 {fmt_coins(amount)}  x{odds}\n"
        f"🏆 Потенциал: {fmt_coins(int(amount*odds))}\n\n"
        f"Баланс: {fmt_coins(user['coins'])}",
        reply_markup=kb_main(), parse_mode="HTML"
    )


async def _do_cbet(q, uid, cbet_id, option_index, amount):
    if not MIN_BET <= amount <= MAX_BET:
        await q.answer(f"❌ От {MIN_BET} до {MAX_BET:,}", show_alert=True); return

    cb = await get_custom_bet(cbet_id)
    if not cb or cb["status"] != "open":
        await q.answer("Ставка недоступна", show_alert=True); return
    if option_index < 0 or option_index >= len(cb["options"]):
        await q.answer("Неверный вариант", show_alert=True); return

    if await _user_cbet_recent(uid, cbet_id):
        await q.answer("Ставка недоступна", show_alert=True); return

    opt  = cb["options"][option_index]
    odds = float(opt.get("odds", 1.0))

    ok = await save_cbet_entry(uid, cbet_id, option_index, amount, odds)
    if not ok:
        await q.answer("❌ Недостаточно Винкоинов!", show_alert=True); return

    user = await get_player(uid)
    await q.edit_message_text(
        f"✅ <b>Ставка принята!</b>\n\n"
        f"🎲 {cb['title']}\n"
        f"📌 {opt.get('name','')}\n"
        f"💰 {fmt_coins(amount)}  x{odds}\n"
        f"🏆 Потенциал: {fmt_coins(int(amount*odds))}\n\n"
        f"Баланс: {fmt_coins(user['coins'])}",
        reply_markup=kb_main(), parse_mode="HTML"
    )


def _leg_label(leg: dict) -> str:
    """Display-лейбл ноги экспресса: ранее сохранённый `bet_label` или лейбл
    из BET_LABELS. Для голлеров без метки — «Автор гола (#N)»."""
    if leg.get("bet_label"):
        return leg["bet_label"]
    bo = leg["bet_on"]
    if bo in BET_LABELS:
        return BET_LABELS[bo]
    if bo.startswith(GOAL_SCORER_PREFIX):
        return f"Автор гола (#{bo[len(GOAL_SCORER_PREFIX):]})"
    return bo


def _express_text(legs: list, balance: int) -> str:
    """Формирует текст конструктора экспресса."""
    lines = [f"🔗 <b>Экспресс</b>  (от {MIN_EXPRESS_LEGS} до {MAX_EXPRESS_LEGS} событий)"]
    lines.append(f"💰 Баланс: <b>{fmt_coins(balance)}</b>")
    lines.append("")
    if not legs:
        lines.append("Событий пока нет. Нажми «➕ Добавить матч».")
        return "\n".join(lines)
    total_odds = 1.0
    for i, leg in enumerate(legs, start=1):
        total_odds *= float(leg["odds"])
        lines.append(
            f"{i}. {leg['match_info']}\n"
            f"   {_leg_label(leg)} | x{leg['odds']}"
        )
    lines.append("")
    lines.append(f"🧮 Общий коэф: <b>x{round(total_odds, 2)}</b>")
    return "\n".join(lines)


async def _do_express(q, ctx, uid, amount: int):
    if not MIN_BET <= amount <= MAX_BET:
        await q.answer(f"❌ От {MIN_BET} до {MAX_BET:,}", show_alert=True); return
    legs = ctx.user_data.get("express_legs", [])
    if len(legs) < MIN_EXPRESS_LEGS:
        await q.answer(f"Нужно минимум {MIN_EXPRESS_LEGS} события", show_alert=True); return

    exp_id = await create_express_bet(uid, amount, legs)
    if not exp_id:
        await q.answer("❌ Недостаточно Винкоинов!", show_alert=True); return

    # считаем общий коэф для показа
    total_odds = 1.0
    for leg in legs:
        total_odds *= float(leg["odds"])
    total_odds = round(total_odds, 2)

    rows = [
        f"{i+1}. {l['match_info']}   {_leg_label(l)} | x{l['odds']}"
        for i, l in enumerate(legs)
    ]
    ctx.user_data.pop("express_legs", None)
    user = await get_player(uid)
    await q.edit_message_text(
        f"✅ <b>Экспресс #{exp_id} принят!</b>\n\n"
        + "\n".join(rows) + "\n\n"
        + f"💰 Ставка: <b>{fmt_coins(amount)}</b>\n"
        + f"🧮 Коэф: <b>x{total_odds}</b>\n"
        + f"🏆 Потенциал: <b>{fmt_coins(int(amount*total_odds))}</b>\n\n"
        + f"Баланс: {fmt_coins(user['coins'])}",
        reply_markup=kb_main(), parse_mode="HTML"
    )


async def _show_quiz_question(q, qidx: int):
    q_text, opts, _ = CAPPER_QUIZ[qidx]
    await q.edit_message_text(
        f"🎓 <b>Вопрос {qidx+1}/{len(CAPPER_QUIZ)}</b>\n\n{q_text}",
        reply_markup=kb_cap_quiz(qidx), parse_mode="HTML",
    )


async def _follow_tip(q, uid: int, tip_id: int, amount: int):
    if not MIN_BET <= amount <= MAX_BET:
        await q.answer(f"❌ От {MIN_BET} до {MAX_BET:,}", show_alert=True); return
    tip = await get_tip(tip_id)
    if not tip:
        await q.answer("Тип не найден", show_alert=True); return
    if tip["capper_id"] == uid:
        await q.answer("Нельзя ставить за собой", show_alert=True); return

    pool = await get_pool()
    if tip["bet_type"] == "single":
        async with pool.acquire() as conn:
            src = await conn.fetchrow(
                "SELECT * FROM bets WHERE id=$1", tip["ref_id"]
            )
        if not src:
            await q.answer("Исходная ставка удалена", show_alert=True); return
        bet_id = await save_bet(
            uid, src["match_id"], src["match_info"], src["bet_on"],
            amount, float(src["odds"]), via_tip_id=tip_id,
        )
        if not bet_id:
            await q.answer("❌ Недостаточно Винкоинов!", show_alert=True); return
        user = await get_player(uid)
        await q.edit_message_text(
            f"✅ <b>Ставка по типу #{tip_id} принята!</b>\n\n"
            f"⚽ {src['match_info']}\n"
            f"💰 {fmt_coins(amount)}  x{src['odds']}\n"
            f"🏆 Потенциал: {fmt_coins(int(amount*float(src['odds'])))}\n\n"
            f"Баланс: {fmt_coins(user['coins'])}",
            reply_markup=kb_main(), parse_mode="HTML",
        )
        return

    if tip["bet_type"] == "express":
        async with pool.acquire() as conn:
            src_exp = await conn.fetchrow(
                "SELECT * FROM express_bets WHERE id=$1", tip["ref_id"]
            )
            src_legs = await conn.fetch(
                "SELECT match_id, match_info, bet_on, odds "
                "FROM express_bet_legs WHERE express_id=$1 ORDER BY id",
                tip["ref_id"],
            )
        if not src_exp or not src_legs:
            await q.answer("Исходный экспресс удалён", show_alert=True); return
        legs = [dict(l) for l in src_legs]
        # create_express_bet ожидает match_id, match_info, bet_on, odds
        exp_id = await create_express_bet(uid, amount, legs)
        if not exp_id:
            await q.answer("❌ Недостаточно Винкоинов!", show_alert=True); return
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE express_bets SET via_tip_id=$1 WHERE id=$2",
                tip_id, exp_id,
            )
        total_odds = float(src_exp["total_odds"])
        user = await get_player(uid)
        await q.edit_message_text(
            f"✅ <b>Экспресс #{exp_id} по типу #{tip_id} принят!</b>\n\n"
            f"Коэф: x{total_odds}\n"
            f"💰 Ставка: {fmt_coins(amount)}\n"
            f"🏆 Потенциал: {fmt_coins(int(amount*total_odds))}\n\n"
            f"Баланс: {fmt_coins(user['coins'])}",
            reply_markup=kb_main(), parse_mode="HTML",
        )


def _parse_cbet_message(text: str):
    """Парсит единое сообщение вида:

        Название ставки
        Исход1 коэф1 | Исход2 коэф2 | …

    Исходы могут быть на одной строке или разбиты на несколько. В каждом исходе
    последний токен — коэффициент, остальное — название исхода.
    Возвращает (title, options, err)."""
    lines = text.splitlines()
    title = None
    rest_lines: list[str] = []
    for line in lines:
        if title is None:
            if line.strip():
                title = line.strip()
        else:
            rest_lines.append(line)
    if not title:
        return None, None, "Пустое название."
    title = title[:100]

    rest = " ".join(rest_lines).strip()
    if not rest:
        return None, None, (
            "Нет исходов. На второй строке укажи «Исход1 коэф1 | Исход2 коэф2»."
        )

    options = []
    for i, raw in enumerate(rest.split("|"), start=1):
        piece = raw.strip()
        if not piece:
            continue
        tokens = piece.split()
        if len(tokens) < 2:
            return None, None, (
                f"Исход {i}: нужен формат «Название коэф»."
            )
        try:
            odds = float(tokens[-1].replace(",", "."))
        except ValueError:
            return None, None, (
                f"Исход {i}: не удалось распознать коэффициент «{tokens[-1]}»."
            )
        if odds < 1.01:
            return None, None, f"Исход {i}: коэффициент должен быть ≥ 1.01."
        name = " ".join(tokens[:-1]).strip()
        if not name:
            return None, None, f"Исход {i}: пустое название."
        options.append({"name": name[:80], "odds": round(odds, 2)})

    if len(options) < 2:
        return None, None, "Нужно минимум 2 исхода."
    if len(options) > MAX_CUSTOM_OPTIONS:
        return None, None, f"Максимум {MAX_CUSTOM_OPTIONS} исходов."
    return title, options, None


# ── text input FSM ────────────────────────────────────────────────────────────

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    fsm = ctx.user_data.get("fsm")
    uid = update.effective_user.id
    txt = update.message.text.strip()

    if fsm == WAIT_BET_AMOUNT:
        try:
            amount = int(txt)
        except ValueError:
            await update.message.reply_text("❌ Введи целое число"); return
        if not MIN_BET <= amount <= MAX_BET:
            await update.message.reply_text(f"❌ От {MIN_BET} до {MAX_BET:,}"); return
        pb = ctx.user_data.get("pending_bet", {})
        ctx.user_data.clear()
        user = await get_player(uid)
        if user["coins"] < amount:
            await update.message.reply_text("❌ Недостаточно Винкоинов!", reply_markup=kb_main())
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM cached_fixtures WHERE fixture_id=$1", pb["fixture_id"]
            )
        if not row:
            await update.message.reply_text("❌ Матч не найден", reply_markup=kb_main())
            return
        f      = dict(row)
        bet_on = pb["bet_on"]
        if not _is_known_bet(bet_on):
            await update.message.reply_text("❌ Неизвестный рынок", reply_markup=kb_main())
            return
        odds = await _resolve_bet_odds(f, bet_on)
        if odds is None:
            await update.message.reply_text("❌ Нет коэффициента", reply_markup=kb_main())
            return
        ok = await save_bet(
            uid, pb["fixture_id"], f"{f['home_team']} vs {f['away_team']}",
            bet_on, amount, odds
        )
        if not ok:
            await update.message.reply_text(
                "❌ Недостаточно Винкоинов!", reply_markup=kb_main()
            )
            return
        user = await get_player(uid)
        title = await _bet_title(bet_on, pb["fixture_id"], f['home_team'], f['away_team'])
        await update.message.reply_text(
            f"✅ <b>Ставка принята!</b>\n\n"
            f"⚽ {f['home_team']} vs {f['away_team']}\n"
            f"📌 {title}\n"
            f"💰 {fmt_coins(amount)}  x{odds}\n"
            f"🏆 Потенциал: {fmt_coins(int(amount*odds))}\n\n"
            f"Баланс: {fmt_coins(user['coins'])}",
            reply_markup=kb_main(), parse_mode="HTML"
        )

    elif fsm == WAIT_GIVE_ID and uid == ADMIN_ID:
        try:
            ctx.user_data["give_uid"] = int(txt)
            ctx.user_data["fsm"]      = WAIT_GIVE_AMT
            await update.message.reply_text("💰 Введи сумму:")
        except ValueError:
            await update.message.reply_text("❌ Неверный ID")

    elif fsm == WAIT_GIVE_AMT and uid == ADMIN_ID:
        try:
            amount   = int(txt)
            give_uid = ctx.user_data.pop("give_uid")
            ctx.user_data.clear()
            await add_coins(give_uid, amount)
            await update.message.reply_text(
                f"✅ Выдано {fmt_coins(amount)} → {give_uid}", reply_markup=kb_admin()
            )
        except ValueError:
            await update.message.reply_text("❌ Неверная сумма")

    elif fsm == WAIT_BROADCAST and uid == ADMIN_ID:
        ctx.user_data.clear()
        users = await all_user_ids()
        sent  = 0
        for target in users:
            try:
                await ctx.bot.send_message(target, f"📢 {txt}")
                sent += 1
            except Exception:
                pass
        await update.message.reply_text(
            f"✅ Отправлено {sent}/{len(users)}", reply_markup=kb_admin()
        )

    elif fsm == WAIT_RB_FIXTURE_RESULT and uid == ADMIN_ID:
        fixture_id = int(ctx.user_data.get("rb_fixture_id") or 0)
        ctx.user_data.clear()
        if not fixture_id:
            await update.message.reply_text(
                "❌ Сессия истекла. Открой админку заново.",
                reply_markup=kb_admin(),
            )
            return
        result, err = _parse_admin_fixture_result(txt)
        if err or not result:
            await update.message.reply_text(
                f"❌ Не понял ввод: {err or 'формат'}\n\n"
                "Пример:\n<code>2:1\ncorners=11\nyellows=3\n"
                "scorers=Холланд, Салах</code>",
                parse_mode="HTML",
            )
            return
        try:
            counts = await _admin_resolve_fixture_bets(ctx, fixture_id, result)
        except Exception as e:
            logger.exception("admin resolve fixture failed")
            await update.message.reply_text(
                f"❌ Ошибка резолва: <code>{e}</code>",
                reply_markup=kb_admin(), parse_mode="HTML",
            )
            return
        await update.message.reply_text(
            f"✅ Матч #{fixture_id} закрыт по введённому результату\n"
            f"Счёт: <b>{result['home_goals']}:{result['away_goals']}</b>\n"
            f"• Победы: {counts.get('win', 0)}\n"
            f"• Поражения: {counts.get('lose', 0)}\n"
            f"• Возвраты: {counts.get('void', 0)}",
            reply_markup=kb_admin(), parse_mode="HTML",
        )

    elif fsm == WAIT_CBET_TITLE and uid == ADMIN_ID:
        title, options, err = _parse_cbet_message(txt)
        if err:
            await update.message.reply_text(
                f"❌ {err}\n\nОжидаемый формат:\n"
                "<code>Название ставки\n"
                "Исход1 коэф1 | Исход2 коэф2 | …</code>",
                parse_mode="HTML",
            )
            return
        ctx.user_data.clear()
        cbet_id = await create_custom_bet(title, options, uid)
        lines = [
            f"✅ <b>Кастомная ставка #{cbet_id} создана</b>",
            f"🎲 {title}",
            "",
            "<b>Исходы:</b>",
        ]
        for i, opt in enumerate(options):
            lines.append(f"{i+1}. {opt['name']} — x{opt['odds']}")
        await update.message.reply_text(
            "\n".join(lines), reply_markup=kb_admin(), parse_mode="HTML"
        )

    elif fsm == WAIT_CBET_AMOUNT:
        try:
            amount = int(txt)
        except ValueError:
            await update.message.reply_text("❌ Введи целое число"); return
        if not MIN_BET <= amount <= MAX_BET:
            await update.message.reply_text(f"❌ От {MIN_BET} до {MAX_BET:,}"); return
        pb = ctx.user_data.pop("pending_cbet", {})
        ctx.user_data.clear()
        if not pb:
            await update.message.reply_text("❌ Сессия истекла", reply_markup=kb_main())
            return
        cbet_id = pb["cbet_id"]; option_index = pb["option_index"]
        cb = await get_custom_bet(cbet_id)
        if not cb or cb["status"] != "open":
            await update.message.reply_text("❌ Ставка недоступна", reply_markup=kb_main())
            return
        if option_index < 0 or option_index >= len(cb["options"]):
            await update.message.reply_text("❌ Неверный вариант", reply_markup=kb_main())
            return
        if await _user_cbet_recent(uid, cbet_id):
            await update.message.reply_text("❌ Ставка недоступна", reply_markup=kb_main())
            return
        opt  = cb["options"][option_index]
        odds = float(opt.get("odds", 1.0))
        ok = await save_cbet_entry(uid, cbet_id, option_index, amount, odds)
        if not ok:
            await update.message.reply_text(
                "❌ Недостаточно Винкоинов!", reply_markup=kb_main()
            )
            return
        user = await get_player(uid)
        await update.message.reply_text(
            f"✅ <b>Ставка принята!</b>\n\n"
            f"🎲 {cb['title']}\n"
            f"📌 {opt.get('name','')}\n"
            f"💰 {fmt_coins(amount)}  x{odds}\n"
            f"🏆 Потенциал: {fmt_coins(int(amount*odds))}\n\n"
            f"Баланс: {fmt_coins(user['coins'])}",
            reply_markup=kb_main(), parse_mode="HTML"
        )

    elif fsm == WAIT_PROMO_CODE:
        ctx.user_data.clear()
        await _activate_promo_for(update, uid, txt)

    elif fsm == WAIT_NEW_LEAGUE_REQUEST:
        ctx.user_data.pop("fsm", None)
        request_text = txt[:500]
        if not request_text:
            await update.message.reply_text(
                "❌ Пустой запрос. Попробуй ещё раз.", reply_markup=kb_main()
            )
            return
        user_obj = update.effective_user
        uname_raw = user_obj.username or user_obj.first_name or ""
        uname = f"@{user_obj.username}" if user_obj.username else (user_obj.first_name or "—")
        # Сохраняем в БД, чтобы админ мог потом посмотреть список
        try:
            req_id = await db_league_request_save(uid, uname_raw, request_text)
        except Exception as e:
            logger.warning("league request save failed: %s", e)
            req_id = 0
        admin_text = (
            "📨 <b>Запрос новой лиги (КАЗИБЕТ)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 От: {uname}\n"
            f"🆔 TG ID: <code>{uid}</code>\n"
            f"📝 #{req_id or '—'}\n\n"
            f"🏆 Лига:\n<code>{request_text}</code>"
        )
        kb_admin_act = None
        if req_id:
            kb_admin_act = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Принять",
                                      callback_data=f"cb:lreq_ok:{req_id}"),
                 InlineKeyboardButton("❌ Отклонить",
                                      callback_data=f"cb:lreq_no:{req_id}")],
                [InlineKeyboardButton("📋 Все запросы",
                                      callback_data="cb:adm_league_reqs")],
            ])
        try:
            await ctx.bot.send_message(
                ADMIN_ID, admin_text, parse_mode="HTML",
                reply_markup=kb_admin_act,
            )
            ok = True
        except Exception as e:
            logger.warning("failed to notify admin about league request: %s", e)
            ok = False
        if ok:
            await update.message.reply_text(
                "✅ <b>Запрос отправлен</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📝 Номер запроса: <b>#{req_id or '—'}</b>\n"
                "Админ получил уведомление и рассмотрит добавление лиги.\n"
                "Спасибо за предложение!",
                reply_markup=kb_main(), parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "❌ Не удалось отправить уведомление админу. "
                "Попробуй позже или напиши напрямую.",
                reply_markup=kb_main(), parse_mode="HTML",
            )

    elif fsm == WAIT_CAPPER_FOLLOW_AMT:
        try:
            amount = int(txt)
        except ValueError:
            await update.message.reply_text("❌ Введи целое число"); return
        if not MIN_BET <= amount <= MAX_BET:
            await update.message.reply_text(f"❌ От {MIN_BET} до {MAX_BET:,}"); return
        tip_id = ctx.user_data.get("cap_follow_tip")
        ctx.user_data.pop("fsm", None)
        ctx.user_data.pop("cap_follow_tip", None)
        if not tip_id:
            await update.message.reply_text("❌ Тип не найден", reply_markup=kb_main()); return
        # имитируем q-объект для _follow_tip
        class _Reply:
            async def answer(self, text=None, show_alert=False):
                if text:
                    await update.message.reply_text(text)
            async def edit_message_text(self, *args, **kwargs):
                kwargs.pop("parse_mode", None)
                kwargs.pop("reply_markup", None)
                await update.message.reply_text(
                    args[0] if args else kwargs.get("text", ""),
                    reply_markup=kb_main(), parse_mode="HTML"
                )
        await _follow_tip(_Reply(), uid, tip_id, amount)

    elif fsm == WAIT_EXPRESS_AMOUNT:
        try:
            amount = int(txt)
        except ValueError:
            await update.message.reply_text("❌ Введи целое число"); return
        if not MIN_BET <= amount <= MAX_BET:
            await update.message.reply_text(f"❌ От {MIN_BET} до {MAX_BET:,}"); return
        legs = ctx.user_data.get("express_legs", [])
        if len(legs) < MIN_EXPRESS_LEGS:
            ctx.user_data.pop("fsm", None)
            await update.message.reply_text(
                f"❌ Нужно минимум {MIN_EXPRESS_LEGS} события", reply_markup=kb_main()
            )
            return
        exp_id = await create_express_bet(uid, amount, legs)
        ctx.user_data.pop("fsm", None)
        if not exp_id:
            await update.message.reply_text(
                "❌ Недостаточно Винкоинов!", reply_markup=kb_main()
            )
            return
        total_odds = 1.0
        for leg in legs:
            total_odds *= float(leg["odds"])
        total_odds = round(total_odds, 2)
        rows = [
            f"{i+1}. {l['match_info']}   {_leg_label(l)} | x{l['odds']}"
            for i, l in enumerate(legs)
        ]
        ctx.user_data.pop("express_legs", None)
        user = await get_player(uid)
        await update.message.reply_text(
            f"✅ <b>Экспресс #{exp_id} принят!</b>\n\n"
            + "\n".join(rows) + "\n\n"
            + f"💰 Ставка: <b>{fmt_coins(amount)}</b>\n"
            + f"🧮 Коэф: <b>x{total_odds}</b>\n"
            + f"🏆 Потенциал: <b>{fmt_coins(int(amount*total_odds))}</b>\n\n"
            + f"Баланс: {fmt_coins(user['coins'])}",
            reply_markup=kb_main(), parse_mode="HTML"
        )


async def _activate_promo_for(update: Update, uid: int, code_raw: str):
    code = code_raw.strip().upper()
    if not code or len(code) > 64:
        await update.message.reply_text(
            "❌ Некорректный код.", reply_markup=kb_main()
        )
        return
    # убеждаемся, что игрок существует
    await get_or_create(uid, update.effective_user.username, update.effective_user.first_name)
    status, coins = await activate_promo(uid, code)
    if status == "ok":
        user = await get_player(uid)
        await update.message.reply_text(
            f"🎉 <b>Промокод активирован!</b>\n"
            f"➕ <b>+{fmt_coins(coins)}</b>\n"
            f"Баланс: {fmt_coins(user['coins'])}",
            reply_markup=kb_main(), parse_mode="HTML",
        )
    elif status == "already":
        await update.message.reply_text(
            "⚠️ Ты уже активировал этот промокод.", reply_markup=kb_main()
        )
    elif status == "exhausted":
        await update.message.reply_text(
            "⌛ У промокода закончились активации.", reply_markup=kb_main()
        )
    else:
        await update.message.reply_text(
            "❌ Промокод не найден.", reply_markup=kb_main()
        )


# ════════════════════════════════════════════════════════════
#  ДОПОЛНИТЕЛЬНЫЕ КОМАНДЫ
# ════════════════════════════════════════════════════════════

async def cmd_createpromo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "Использование:\n<code>/createpromo НАЗВАНИЕ КОИНЫ АКТИВАЦИИ</code>\n"
            "Пример: <code>/createpromo WELCOME 500 100</code>",
            parse_mode="HTML",
        )
        return
    code = args[0].strip().upper()[:64]
    try:
        coins = int(args[1])
        activations = int(args[2])
    except ValueError:
        await update.message.reply_text("❌ КОИНЫ и АКТИВАЦИИ должны быть числами.")
        return
    if coins <= 0 or activations <= 0:
        await update.message.reply_text("❌ Значения должны быть положительными.")
        return
    ok = await create_promo(code, coins, activations, update.effective_user.id)
    if not ok:
        await update.message.reply_text(f"❌ Промокод <code>{code}</code> уже существует.",
                                        parse_mode="HTML")
        return
    await update.message.reply_text(
        "✅ <b>Промокод создан</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🎟 Код: <code>{code}</code>\n"
        f"🪙 Монеты: <b>{coins:,}</b>\n"
        f"👥 Максимум использований: <b>{activations}</b>",
        parse_mode="HTML",
    )


async def cmd_makecapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Использование:\n<code>/makecapper USER_ID</code>",
            parse_mode="HTML",
        )
        return
    try:
        target = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID должен быть числом.")
        return
    player = await get_player(target)
    if not player:
        await update.message.reply_text(f"❌ Игрок {target} не найден.")
        return
    await set_capper(target, True)
    await update.message.reply_text(
        f"✅ Игрок <code>{target}</code> теперь капер.",
        parse_mode="HTML",
    )
    try:
        await ctx.bot.send_message(
            target,
            "🎯 Админ сделал тебя капером! Теперь ты можешь делиться ставками "
            "через «🎯 Каперы → 📝 Поделиться моей ставкой» и получать "
            f"{CAPPER_WIN_SHARE_PCT}% от выигрышей и {CAPPER_LOSE_SHARE_PCT}% "
            "от проигрышей фолловеров.",
        )
    except Exception:
        pass


async def cmd_promo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    args = ctx.args or []
    if args:
        await _activate_promo_for(update, uid, args[0])
        return
    await get_or_create(uid, update.effective_user.username, update.effective_user.first_name)
    ctx.user_data["fsm"] = WAIT_PROMO_CODE
    await update.message.reply_text(
        "🎟 Отправь промокод одним сообщением (или используй "
        "<code>/promo КОД</code>).",
        parse_mode="HTML",
    )


# ════════════════════════════════════════════════════════════
#  ИНТЕГРАЦИЯ С ОСНОВНЫМ БОТОМ
# ════════════════════════════════════════════════════════════

async def casibet_post_init(app: Application) -> None:
    """Вызывается из post_init основного бота после init_db.

    Раньше тут было `await fetch_upcoming_fixtures()`, и если кэш матчей
    был пуст или устарел, это блокировало старт бота на 5+ минут (фикстуры
    + коэффициенты по каждому матчу). Telegram getUpdates стартовал только
    ПОСЛЕ окончания этого вызова — пользователи не могли пользоваться
    ботом всё это время. Теперь fetch уходит в фоновую задачу, бот
    готов принимать сообщения сразу.
    """
    await init_db()

    async def _initial_fetch() -> None:
        try:
            await fetch_upcoming_fixtures()
        except Exception as e:
            logger.warning("casibet fetch_upcoming_fixtures failed: %s", e)

    asyncio.create_task(_initial_fetch())
    # Settle job — раз в 2 часа, fixtures refresh — раз в 6 часов
    # (раньше был 3 дня, что слишком редко при TTL кэша 6 ч).
    app.job_queue.run_repeating(bets_settle_job,      interval=7200,  first=300)
    app.job_queue.run_repeating(fixtures_refresh_job, interval=21600, first=60)


async def casibet_open_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Открывает корневое меню казибета. Вызывается из главного меню основного бота."""
    q = update.callback_query
    if q is not None:
        await q.answer()
        uid = q.from_user.id
        user = await get_or_create(uid, q.from_user.username, q.from_user.first_name)
        await q.edit_message_text(
            f"🎯 <b>КАЗИБЕТ</b>\n"
            f"Ставки на реальные футбольные матчи.\n\n"
            f"💰 Баланс: <b>{fmt_coins(user['coins'])}</b>",
            reply_markup=kb_main(), parse_mode="HTML"
        )
    elif update.message is not None:
        uid = update.effective_user.id
        user = await get_or_create(uid, update.effective_user.username,
                                   update.effective_user.first_name)
        await update.message.reply_text(
            f"🎯 <b>КАЗИБЕТ</b>\n"
            f"Ставки на реальные футбольные матчи.\n\n"
            f"💰 Баланс: <b>{fmt_coins(user['coins'])}</b>",
            reply_markup=kb_main(), parse_mode="HTML"
        )


async def casibet_handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Обрабатывает текстовый ввод, если у пользователя установлен FSM казибета.
    Возвращает True, если сообщение обработано (основной бот прекращает обработку)."""
    fsm = ctx.user_data.get("fsm")
    if fsm is None:
        return False
    # Все FSM-константы казибета — целые числа в диапазоне 1..20
    casibet_fsm_values = {
        WAIT_BET_AMOUNT, WAIT_GIVE_ID, WAIT_GIVE_AMT, WAIT_BROADCAST,
        WAIT_CBET_TITLE, WAIT_CBET_OPTIONS, WAIT_CBET_AMOUNT,
        WAIT_CBET_SETTLE_ID, WAIT_CBET_SETTLE_OPTION,
        WAIT_PROMO_CODE, WAIT_EXPRESS_AMOUNT, WAIT_CAPPER_FOLLOW_AMT,
        WAIT_NEW_LEAGUE_REQUEST,
    }
    if fsm not in casibet_fsm_values:
        return False
    try:
        await on_text(update, ctx)
    except Exception as e:
        logger.exception("casibet on_text error: %s", e)
    return True


def build_casibet_handlers(app: Application) -> None:
    """Регистрирует хендлеры казибета в Application основного бота.
    Главное меню Казимира вызывает casibet_open_menu() по кнопке 🎯 КАЗИБЕТ."""
    # Команды казибета с префиксом /cb_ чтобы не конфликтовать с основным ботом
    app.add_handler(CommandHandler("cb_admin",         cmd_admin))
    app.add_handler(CommandHandler("cb_update",        cmd_update))
    app.add_handler(CommandHandler("cb_createpromo",   cmd_createpromo))
    app.add_handler(CommandHandler("cb_makecapper",    cmd_makecapper))
    app.add_handler(CommandHandler("cb_promo",         cmd_promo))
    # Удобные алиасы:
    app.add_handler(CommandHandler("kazibet",          casibet_open_menu))
    app.add_handler(CommandHandler("stavki",           casibet_open_menu))
    # Все inline-кнопки казибета — префикс "cb:"
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^cb:"))
    logger.info("Casibet handlers registered")


__all__ = [
    "casibet_post_init",
    "casibet_open_menu",
    "casibet_handle_text",
    "build_casibet_handlers",
]
