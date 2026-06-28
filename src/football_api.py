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
