"""SStats.net Football API client with caching."""
import aiohttp
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

SSTATS_BASE = "https://api.sstats.net"
SSTATS_KEY = ""

_cache: dict[str, tuple[float, any]] = {}
CACHE_TTL = {
    "today": 900,       # 15 min
    "upcoming": 1800,   # 30 min
    "match": 300,       # 5 min
    "prediction": 3600, # 1 hour
    "standings": 3600,  # 1 hour
    "leagues": 86400,   # 24 hours
    "live": 60,         # 1 min
}


def set_api_key(key: str):
    global SSTATS_KEY
    SSTATS_KEY = key


def _get_cache(key: str, ttl: int) -> Optional[any]:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < ttl:
            return data
    return None


def _set_cache(key: str, data: any):
    _cache[key] = (time.time(), data)


class FootballAPI:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or SSTATS_KEY
        self.session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"X-API-Key": self.api_key} if self.api_key else {},
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get(self, endpoint: str, params: dict = None, cache_key: str = "", ttl: int = 300) -> Optional[dict]:
        import urllib.parse
        cache_k = cache_key or f"{endpoint}?{urllib.parse.urlencode(params or {})}"

        cached = _get_cache(cache_k, ttl)
        if cached is not None:
            return cached

        try:
            session = await self.get_session()
            url = f"{SSTATS_BASE}{endpoint}"
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"SStats API {endpoint} failed: HTTP {resp.status}: {text[:200]}")
                    return None
                data = await resp.json()
                _set_cache(cache_k, data)
                return data
        except Exception as e:
            logger.error(f"SStats API error {endpoint}: {e}")
            return None

    async def get_today_matches(self) -> list[dict]:
        data = await self._get("/games/list", {"today": "true", "TimeZone": 3}, "today_matches", CACHE_TTL["today"])
        if data and "data" in data:
            return data["data"]
        return []

    async def get_upcoming_matches(self, limit: int = 10) -> list[dict]:
        data = await self._get("/games/list", {"upcoming": "true", "limit": limit, "TimeZone": 3, "order": 1}, "upcoming_matches", CACHE_TTL["upcoming"])
        if data and "data" in data:
            return data["data"]
        return []

    async def get_live_matches(self) -> list[dict]:
        data = await self._get("/games/list", {"live": "true", "TimeZone": 3}, "live_matches", CACHE_TTL["live"])
        if data and "data" in data:
            return data["data"]
        return []

    async def get_match_details(self, game_id: int) -> Optional[dict]:
        data = await self._get(f"/games/{game_id}", ttl=CACHE_TTL["match"])
        if data and "data" in data:
            return data["data"]
        return None

    async def get_match_prediction(self, game_id: int) -> Optional[dict]:
        data = await self._get(f"/games/glicko/{game_id}", ttl=CACHE_TTL["prediction"])
        if data and "data" in data:
            return data["data"]
        return None

    async def get_team_form(self, game_id: int, limit: int = 10) -> Optional[dict]:
        data = await self._get("/games/last-games-stats", {"gameId": game_id, "limit": limit}, f"form_{game_id}", CACHE_TTL["match"])
        if data and "data" in data:
            return data["data"]
        return None

    async def get_match_summary(self, game_id: int) -> Optional[str]:
        session = await self.get_session()
        url = f"{SSTATS_BASE}/games/text-summary"
        try:
            async with session.get(url, params={"id": game_id}) as resp:
                if resp.status != 200:
                    return None
                text = await resp.text()
                _set_cache(f"summary_{game_id}", text)
                return text
        except Exception as e:
            logger.error(f"SStats summary error: {e}")
            return None

    async def get_standings(self, league_id: int, year: int = 2026) -> list[dict]:
        data = await self._get("/games/season-table", {"league": league_id, "year": year}, f"standings_{league_id}_{year}", CACHE_TTL["standings"])
        if data and "data" in data:
            return data["data"]
        return []

    async def get_injuries(self, game_id: int) -> list[dict]:
        data = await self._get("/games/injuries", {"gameId": game_id}, f"injuries_{game_id}", CACHE_TTL["match"])
        if data and "data" in data:
            return data["data"]
        return []

    async def get_leagues(self) -> list[dict]:
        data = await self._get("/leagues", ttl=CACHE_TTL["leagues"])
        if data and "data" in data:
            return data["data"]
        return []

    async def find_league(self, name: str) -> Optional[dict]:
        leagues = await self.get_leagues()
        name_lower = name.lower()
        for league in leagues:
            league_name = (league.get("name") or "").lower()
            if name_lower in league_name or league_name in name_lower:
                return league
        return None

    async def get_matches_by_league(self, league_id: int, year: int) -> list[dict]:
        data = await self._get("/games/list", {"leagueid": league_id, "year": year, "TimeZone": 3}, f"league_{league_id}_{year}", CACHE_TTL["today"])
        if data and "data" in data:
            return data["data"]
        return []

    async def get_past_matches(self, league_id: int, year: int, limit: int = 20) -> list[dict]:
        """Завершённые матчи лиги за сезон."""
        data = await self._get("/games/list", {"leagueid": league_id, "year": year, "ended": "true", "limit": limit, "order": -1, "TimeZone": 3}, f"past_{league_id}_{year}", CACHE_TTL["today"])
        if data and "data" in data:
            return data["data"]
        return []

    async def get_recent_results(self, limit: int = 10) -> list[dict]:
        """Недавние завершённые матчи (все лиги)."""
        data = await self._get("/games/list", {"ended": "true", "limit": limit, "order": -1, "TimeZone": 3}, "recent_results", CACHE_TTL["today"])
        if data and "data" in data:
            return data["data"]
        return []

    async def get_match_for_prediction(self, game_id: int) -> dict:
        """Собирает все данные для прогноза в один dict."""
        details = await self.get_match_details(game_id)
        prediction = await self.get_match_prediction(game_id)
        form = await self.get_team_form(game_id)

        return {
            "match": details,
            "prediction": prediction,
            "form": form,
        }

    def format_match_short(self, match: dict) -> str:
        """Краткий формат матча для списка."""
        home = (match.get("homeTeam") or {}).get("name", "?")
        away = (match.get("awayTeam") or {}).get("name", "?")
        score_h = match.get("homeResult") or match.get("scoreHomeFT") or 0
        score_a = match.get("awayResult") or match.get("scoreAwayFT") or 0
        status = match.get("status", 0)
        date = match.get("date", "")

        w1 = match.get("winner1", 0)
        wx = match.get("winnerX", 0)
        w2 = match.get("winner2", 0)

        if status in (8, 9, 10, 17, 18):
            score_str = f"{score_h}:{score_a}"
        elif status in (3, 4, 5, 6, 7, 11, 19):
            score_str = f"{score_h}:{score_a} 🔴"
        elif status == 2:
            score_str = date[11:16] if len(date) > 15 else "?"
        else:
            score_str = "—"

        odds_str = ""
        if w1 and wx and w2:
            odds_str = f" | Кеф: {w1}/{wx}/{w2}"

        return f"⚽ {home} — {away} | {score_str}{odds_str}"

    # ─── Pari.ru API (real odds) ───

    PARI_ODDS_MAP = {
        # 1X2
        8250: "winner1",   # П1
        8253: "winner2",   # П2
        8256: "winnerX",   # Ничья
        # Двойной шанс
        8433: "dc_1X",     # 1X
        8430: "dc_12",     # 12
        8436: "dc_X2",     # X2
        # Тотулы голов
        217: "over25",     # ТБ 2.5
        292: "under25",    # ТМ 2.5
        218: "over30",     # ТБ 3.0
        219: "over35",     # ТБ 3.5
        215: "over15",     # ТБ 1.5
        # Обе забьют
        8259: "btts_yes",  # Обе забьют Да
        8262: "btts_no",   # Обе забьют Нет
        # Азиатская фора
        146: "ah_home_minus15",  # Ф1 -1.5
        147: "ah_home_minus10",  # Ф1 -1.0
        71:  "ah_away_plus15",   # Ф2 +1.5
        70:  "ah_away_plus10",   # Ф2 +1.0
        # Угловые
        735: "corners_over75",   # Угловые ТБ 7.5
        888: "corners_under75",  # Угловые ТМ 7.5
        737: "corners_over85",   # Угловые ТБ 8.5
        890: "corners_under85",  # Угловые ТМ 8.5
        739: "corners_over95",   # Угловые ТБ 9.5
        892: "corners_under95",  # Угловые ТМ 9.5
        8268: "corners_1",       # Угловые П1
        8265: "corners_2",       # Угловые П2
        8271: "corners_X",       # Угловые ничья
        # Желтые карточки
        1141: "yellow_over25",   # ЖК ТБ 2.5
        1216: "yellow_under25",  # ЖК ТМ 2.5
        1143: "yellow_over35",   # ЖК ТБ 3.5
        1218: "yellow_under35",  # ЖК ТМ 3.5
        8277: "yellow_1",        # ЖК П1
        8280: "yellow_2",        # ЖК П2
        8274: "yellow_X",        # ЖК ничья
        # Фолы
        1605: "fouls_over245",   # Фолы ТБ 24.5
        1758: "fouls_under245",  # Фолы ТМ 24.5
        1606: "fouls_over255",   # Фолы ТБ 25.5
        1759: "fouls_under255",  # Фолы ТМ 25.5
    }

    async def get_pari_upcoming_matches(self, limit: int = 50) -> list[dict]:
        """Получает предстоящие матчи с реальными коэффициентами Pari.ru."""
        data = await self._get("/Pari/matches", {
            "upcoming": "true",
            "includeOdds": "true",
            "limit": min(limit, 200),
            "timezone": 3,
        }, "pari_upcoming", 300)  # 5 min cache

        if not data or "data" not in data:
            return []

        result = []
        for m in data["data"]:
            info = m.get("matchInfo", {})
            odds_list = m.get("currentOdds") or []

            match = {
                "id": info.get("eventId", 0),
                "homeTeam": {"name": (info.get("homeTeam") or {}).get("name", "?"), "id": (info.get("homeTeam") or {}).get("id", 0)},
                "awayTeam": {"name": (info.get("awayTeam") or {}).get("name", "?"), "id": (info.get("awayTeam") or {}).get("id", 0)},
                "date": (info.get("startDate") or "")[:19],
                "status": info.get("status", "NotStarted"),
                "league": (info.get("tournament") or {}).get("name", ""),
                "homeResult": info.get("homeScore", 0) or 0,
                "awayResult": info.get("awayScore", 0) or 0,
            }

            for o in odds_list:
                oid = o.get("id", 0)
                if oid in self.PARI_ODDS_MAP and not o.get("isDeleted"):
                    match[self.PARI_ODDS_MAP[oid]] = o.get("value", 0)

            match["winner1"] = match.get("winner1", 0)
            match["winnerX"] = match.get("winnerX", 0)
            match["winner2"] = match.get("winner2", 0)

            result.append(match)

        return result

    async def get_pari_live_matches(self) -> list[dict]:
        """Получает live матчи с коэффициентами Pari.ru."""
        data = await self._get("/Pari/matches", {
            "live": "true",
            "includeOdds": "true",
            "limit": 50,
            "timezone": 3,
        }, "pari_live", 60)  # 1 min cache

        if not data or "data" not in data:
            return []

        result = []
        for m in data["data"]:
            info = m.get("matchInfo", {})
            odds_list = m.get("currentOdds") or []

            match = {
                "id": info.get("eventId", 0),
                "homeTeam": {"name": (info.get("homeTeam") or {}).get("name", "?"), "id": (info.get("homeTeam") or {}).get("id", 0)},
                "awayTeam": {"name": (info.get("awayTeam") or {}).get("name", "?"), "id": (info.get("awayTeam") or {}).get("id", 0)},
                "date": (info.get("startDate") or "")[:19],
                "status": "Live",
                "league": (info.get("tournament") or {}).get("name", ""),
                "homeResult": info.get("homeScore", 0) or 0,
                "awayResult": info.get("awayScore", 0) or 0,
            }

            for o in odds_list:
                oid = o.get("id", 0)
                if oid in self.PARI_ODDS_MAP and not o.get("isDeleted"):
                    match[self.PARI_ODDS_MAP[oid]] = o.get("value", 0)

            match["winner1"] = match.get("winner1", 0)
            match["winnerX"] = match.get("winnerX", 0)
            match["winner2"] = match.get("winner2", 0)

            result.append(match)

        return result

    async def get_pari_match_details(self, event_id: int) -> Optional[dict]:
        """Детали матча Pari.ru с коэффициентами и событиями."""
        data = await self._get(f"/Pari/match/{event_id}", ttl=60)
        if not data or "data" not in data:
            return None

        d = data["data"]
        info = d.get("matchInfo", {})
        current_odds = d.get("currentOdds") or []
        prematch_odds = d.get("prematchOdds") or []

        result = {
            "id": info.get("eventId", 0),
            "homeTeam": {"name": (info.get("homeTeam") or {}).get("name", "?")},
            "awayTeam": {"name": (info.get("awayTeam") or {}).get("name", "?")},
            "homeResult": info.get("homeScore", 0) or 0,
            "awayResult": info.get("awayScore", 0) or 0,
            "status": info.get("status", "?"),
            "odds": {},
        }

        for o in current_odds or prematch_odds:
            oid = o.get("id", 0)
            if oid in self.PARI_ODDS_MAP and not o.get("isDeleted"):
                result["odds"][self.PARI_ODDS_MAP[oid]] = o.get("value", 0)

        return result
