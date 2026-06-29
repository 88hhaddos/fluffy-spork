"""Локальная база данных ЧМ 2026 — выгрузка и обновление всех данных."""
import json
import logging
import asyncio
import os
from typing import Optional
from datetime import datetime

from src.football_api import FootballAPI

logger = logging.getLogger(__name__)

WC_LEAGUE_ID = 1
WC_YEAR = 2026
WC_DATA_DIR = os.path.join("docs", "wc_data")


async def fetch_all_wc_data(api: FootballAPI) -> dict:
    """Выгружает ВСЕ данные ЧМ 2026: матчи, детали, события, игроки."""
    os.makedirs(WC_DATA_DIR, exist_ok=True)

    matches = await api.get_matches_by_league(WC_LEAGUE_ID, WC_YEAR)
    logger.info(f"WC 2026: {len(matches)} matches found")

    finished = [m for m in matches if m.get("status") in (8, 9, 10, 17, 18)]
    upcoming = [m for m in matches if m.get("status") in (1, 2)]
    live = [m for m in matches if m.get("status") in (3, 4, 5, 6, 7, 11, 19)]

    all_match_details = []
    all_events = []
    all_players = {}
    all_scorers = {}
    all_assists = {}
    all_cards = {}

    for m in finished:
        game_id = m.get("id")
        if not game_id:
            continue

        details = await api.get_match_details(game_id)
        if not details:
            await asyncio.sleep(2)
            continue

        await asyncio.sleep(2.5)  # Avoid rate limit (30 req/min)

        home_name = (m.get("homeTeam") or {}).get("name", "?")
        away_name = (m.get("awayTeam") or {}).get("name", "?")
        sc_h = m.get("homeResult", 0)
        sc_a = m.get("awayResult", 0)
        date = (m.get("date") or "")[:19]

        match_info = {
            "id": game_id,
            "home": home_name,
            "away": away_name,
            "score": f"{sc_h}:{sc_a}",
            "date": date,
            "status": m.get("status"),
            "stage": m.get("stage") or details.get("stage") or "",
            "round": m.get("round") or "",
            "home_id": (m.get("homeTeam") or {}).get("id", 0),
            "away_id": (m.get("awayTeam") or {}).get("id", 0),
            "referee": details.get("referee", {}).get("name", "") if isinstance(details.get("referee"), dict) else "",
            "stadium": details.get("stadium", {}).get("name", "") if isinstance(details.get("stadium"), dict) else "",
            "attendance": details.get("attendance", 0),
        }

        events = details.get("events", [])
        match_events = []

        for ev in events:
            ev_type_num = ev.get("type", 0)
            ev_name = str(ev.get("name") or "").lower()
            player_obj = ev.get("player")
            if isinstance(player_obj, dict):
                player_name = player_obj.get("name", "?")
            else:
                player_name = str(player_obj or "?")
            assist_obj = ev.get("assistPlayer")
            if isinstance(assist_obj, dict):
                assist_name = assist_obj.get("name", "")
            else:
                assist_name = str(assist_obj or "")
            minute = ev.get("elapsed", "?")
            team_id = ev.get("teamId", 0)
            team_name = home_name if team_id == match_info["home_id"] else away_name

            if ev_type_num == 1:
                ev_type = "goal"
            elif ev_type_num == 2 and "red" in ev_name:
                ev_type = "red card"
            elif ev_type_num == 2:
                ev_type = "yellow card"
            elif ev_type_num == 3:
                ev_type = "substitution"
            else:
                ev_type = ev_name

            event_info = {
                "match": f"{home_name}-{away_name}",
                "type": ev_type,
                "player": player_name,
                "minute": minute,
                "team": team_name,
                "assist": assist_name,
            }
            match_events.append(event_info)
            all_events.append(event_info)

            clean_player = (player_name or "").split("(")[0].strip() if player_name else ""

            if ev_type == "goal":
                if clean_player and clean_player != "?":
                    all_scorers[clean_player] = all_scorers.get(clean_player, 0) + 1
                    if clean_player in all_players:
                        all_players[clean_player]["goals"] += 1
                if assist_name:
                    clean_assist = (assist_name or "").split("(")[0].strip() if assist_name else ""
                    if clean_assist and clean_assist != "?":
                        all_assists[clean_assist] = all_assists.get(clean_assist, 0) + 1
                        if clean_assist in all_players:
                            all_players[clean_assist]["assists"] += 1

            elif ev_type == "yellow card":
                if clean_player and clean_player != "?" and clean_player in all_players:
                    all_players[clean_player]["yellow"] += 1

            elif ev_type == "red card":
                if clean_player and clean_player != "?" and clean_player in all_players:
                    all_players[clean_player]["red"] += 1

        lineups_data = details.get("lineups", {})
        lineup_players = details.get("lineupPlayers", [])
        match_lineups = []
        
        home_formation = lineups_data.get("homeFormation", "") if isinstance(lineups_data, dict) else ""
        away_formation = lineups_data.get("awayFormation", "") if isinstance(lineups_data, dict) else ""

        for p in lineup_players:
            p_name = p.get("playerName", "?")
            p_team_id = p.get("teamId", 0)
            p_pos = p.get("position", "")
            p_start = p.get("startXI", False)
            p_team = home_name if p_team_id == match_info["home_id"] else away_name

            player_info = {
                "name": p_name,
                "position": p_pos,
                "minutes": 0,
                "sub": not p_start,
                "in_minute": 0,
                "out_minute": 0,
                "rating": 0,
                "goals": 0,
                "assists": 0,
                "yellow": 0,
                "red": 0,
                "team": p_team,
            }

            if p_name and p_name != "?":
                if p_name not in all_players:
                    all_players[p_name] = {
                        "name": p_name,
                        "team": p_team,
                        "total_minutes": 0,
                        "goals": 0,
                        "assists": 0,
                        "yellow": 0,
                        "red": 0,
                        "matches": 0,
                        "rating_sum": 0,
                        "rating_count": 0,
                    }
                all_players[p_name]["matches"] += 1

            existing_lineup = next((l for l in match_lineups if l["team"] == p_team), None)
            if not existing_lineup:
                formation = home_formation if p_team == home_name else away_formation
                existing_lineup = {"team": p_team, "formation": formation, "players": []}
                match_lineups.append(existing_lineup)
            existing_lineup["players"].append(player_info)

        stats = details.get("stats", {})
        match_stats = {}
        if stats:
            for key, val in stats.items():
                if isinstance(val, dict):
                    match_stats[key] = {
                        "home": val.get("home", 0),
                        "away": val.get("away", 0),
                    }

        match_info["events"] = match_events
        match_info["lineups"] = match_lineups
        match_info["stats"] = match_stats
        all_match_details.append(match_info)

    # ── Live матчи с деталями ──
    live_details = []
    for m in live:
        game_id = m.get("id")
        if not game_id:
            continue
        details = await api.get_match_details(game_id)
        if not details:
            await asyncio.sleep(2)
            continue
        await asyncio.sleep(2.5)

        home_name = (m.get("homeTeam") or {}).get("name", "?")
        away_name = (m.get("awayTeam") or {}).get("name", "?")
        sc_h = m.get("homeResult", 0)
        sc_a = m.get("awayResult", 0)

        live_match = {
            "id": game_id,
            "home": home_name,
            "away": away_name,
            "score": f"{sc_h}:{sc_a}",
            "date": (m.get("date") or "")[:19],
            "events": [],
            "lineups": [],
        }

        for ev in details.get("events", []):
            ev_type_num = ev.get("type", 0)
            ev_name = str(ev.get("name") or "").lower()
            player_obj = ev.get("player")
            player_name = player_obj.get("name", "?") if isinstance(player_obj, dict) else str(player_obj or "?")
            minute = ev.get("elapsed", "?")
            if ev_type_num == 1:
                ev_type = "goal"
            elif ev_type_num == 2 and "red" in ev_name:
                ev_type = "red card"
            elif ev_type_num == 2:
                ev_type = "yellow card"
            else:
                ev_type = ev_name
            live_match["events"].append({"type": ev_type, "player": player_name, "minute": minute})

        lineup_players = details.get("lineupPlayers", [])
        for p in lineup_players:
            p_name = p.get("playerName", "?")
            p_team_id = p.get("teamId", 0)
            p_team = home_name if p_team_id == (m.get("homeTeam") or {}).get("id", 0) else away_name
            existing = next((l for l in live_match["lineups"] if l["team"] == p_team), None)
            if not existing:
                existing = {"team": p_team, "players": []}
                live_match["lineups"].append(existing)
            existing["players"].append({"name": p_name, "sub": not p.get("startXI", False)})

        live_details.append(live_match)

        match_info = {
            "id": game_id,
            "home": home_name,
            "away": away_name,
            "score": f"{sc_h}:{sc_a}",
            "date": date,
            "status": m.get("status"),
            "stage": m.get("stage") or details.get("stage") or "",
            "round": m.get("round") or "",
            "home_id": (m.get("homeTeam") or {}).get("id", 0),
            "away_id": (m.get("awayTeam") or {}).get("id", 0),
            "referee": details.get("referee", {}).get("name", "") if isinstance(details.get("referee"), dict) else "",
            "stadium": details.get("stadium", {}).get("name", "") if isinstance(details.get("stadium"), dict) else "",
            "attendance": details.get("attendance", 0),
        }

        events = details.get("events", [])
        match_events = []

        for ev in events:
            ev_type = (ev.get("type") or "").lower()
            player = ev.get("player") or ev.get("text") or ""
            minute = ev.get("minute", "?")
            team = ev.get("team") or ""

            event_info = {
                "match": f"{home_name}-{away_name}",
                "type": ev_type,
                "player": str(player),
                "minute": minute,
                "team": str(team),
            }
            match_events.append(event_info)
            all_events.append(event_info)

            clean_player = str(player).split("(")[0].strip()

            if "goal" in ev_type or "гол" in ev_type:
                if clean_player and clean_player != "?":
                    all_scorers[clean_player] = all_scorers.get(clean_player, 0) + 1

            if "assist" in ev_type or "ассист" in ev_type:
                if clean_player and clean_player != "?":
                    all_assists[clean_player] = all_assists.get(clean_player, 0) + 1

            if "yellow" in ev_type or "жёлт" in ev_type or "желт" in ev_type:
                if clean_player and clean_player != "?":
                    all_cards[clean_player] = all_cards.get(clean_player, {})
                    all_cards[clean_player]["yellow"] = all_cards[clean_player].get("yellow", 0) + 1

            if "red" in ev_type or "красн" in ev_type:
                if clean_player and clean_player != "?":
                    all_cards[clean_player] = all_cards.get(clean_player, {})
                    all_cards[clean_player]["red"] = all_cards[clean_player].get("red", 0) + 1

        lineups = details.get("lineups", [])
        match_lineups = []
        for lineup in lineups:
            team_name = (lineup.get("team") or {}).get("name", "?")
            formation = lineup.get("formation", "")
            players_list = []

            for p in lineup.get("players", []):
                p_name = p.get("name", "?")
                p_pos = p.get("position", "")
                p_min = p.get("minutesPlayed", p.get("minutes", 0))
                p_sub = p.get("isSubstitute", False)
                p_in = p.get("inMinute", 0)
                p_out = p.get("outMinute", 0)
                p_rating = p.get("rating", 0)
                p_goals = p.get("goals", 0)
                p_assists = p.get("assists", 0)
                p_yellow = p.get("yellowCards", 0)
                p_red = p.get("redCards", 0)
                p_shots = p.get("shots", 0)
                p_passes = p.get("passes", 0)

                player_info = {
                    "name": p_name,
                    "position": p_pos,
                    "minutes": p_min,
                    "sub": p_sub,
                    "in_minute": p_in,
                    "out_minute": p_out,
                    "rating": p_rating,
                    "goals": p_goals,
                    "assists": p_assists,
                    "yellow": p_yellow,
                    "red": p_red,
                    "shots": p_shots,
                    "passes": p_passes,
                    "team": team_name,
                }
                players_list.append(player_info)

                if p_name and p_name != "?":
                    if p_name not in all_players:
                        all_players[p_name] = {
                            "name": p_name,
                            "team": team_name,
                            "total_minutes": 0,
                            "goals": 0,
                            "assists": 0,
                            "yellow": 0,
                            "red": 0,
                            "matches": 0,
                            "rating_sum": 0,
                            "rating_count": 0,
                        }
                    p_data = all_players[p_name]
                    p_data["total_minutes"] += p_min or 0
                    p_data["goals"] += p_goals or 0
                    p_data["assists"] += p_assists or 0
                    p_data["yellow"] += p_yellow or 0
                    p_data["red"] += p_red or 0
                    p_data["matches"] += 1
                    if p_rating:
                        p_data["rating_sum"] += p_rating
                        p_data["rating_count"] += 1

            match_lineups.append({
                "team": team_name,
                "formation": formation,
                "players": players_list,
            })

        stats = details.get("stats", {})
        match_stats = {}
        if stats:
            for key, val in stats.items():
                if isinstance(val, dict):
                    match_stats[key] = {
                        "home": val.get("home", 0),
                        "away": val.get("away", 0),
                    }
                elif isinstance(val, list) and len(val) >= 2:
                    match_stats[key] = {"home": val[0], "away": val[1]}

        match_info["events"] = match_events
        match_info["lineups"] = match_lineups
        match_info["stats"] = match_stats
        all_match_details.append(match_info)

    # ── Таблица ──
    raw_standings = await api.get_standings(WC_LEAGUE_ID, WC_YEAR)
    standings = []
    if isinstance(raw_standings, dict):
        for team_id, t in raw_standings.items():
            if isinstance(t, dict):
                team_name = "?"
                for m in matches:
                    if (m.get("homeTeam") or {}).get("id") == int(team_id):
                        team_name = (m.get("homeTeam") or {}).get("name", "?")
                        break
                    if (m.get("awayTeam") or {}).get("id") == int(team_id):
                        team_name = (m.get("awayTeam") or {}).get("name", "?")
                        break
                standings.append({
                    "team": team_name,
                    "team_id": int(team_id),
                    "points": t.get("points", 0),
                    "wins": t.get("wins", 0),
                    "draws": t.get("draws", 0),
                    "loss": t.get("loss", 0),
                    "scored": t.get("goalsScored", 0),
                    "missed": t.get("goalsMissed", 0),
                })
    elif isinstance(raw_standings, list):
        for t in raw_standings:
            if isinstance(t, dict):
                standings.append({
                    "team": t.get("teamName", t.get("team", "?")),
                    "team_id": t.get("teamId", 0),
                    "points": t.get("points", 0),
                    "wins": t.get("wins", 0),
                    "draws": t.get("draws", 0),
                    "loss": t.get("loss", 0),
                    "scored": t.get("goalsScored", 0),
                    "missed": t.get("goalsMissed", 0),
                })
    
    standings.sort(key=lambda x: x["points"], reverse=True)

    # ── Сохраняем ──
    data = {
        "updated": datetime.now().isoformat(),
        "total_matches": len(matches),
        "finished": len(finished),
        "upcoming": len(upcoming),
        "live": len(live),
        "matches": all_match_details,
        "upcoming_matches": [
            {
                "home": (m.get("homeTeam") or {}).get("name", "?"),
                "away": (m.get("awayTeam") or {}).get("name", "?"),
                "date": (m.get("date") or "")[:19],
                "id": m.get("id"),
            }
            for m in upcoming[:30]
        ],
        "live_matches": [
            {
                "home": (m.get("homeTeam") or {}).get("name", "?"),
                "away": (m.get("awayTeam") or {}).get("name", "?"),
                "score": f"{m.get('homeResult', 0)}:{m.get('awayResult', 0)}",
                "id": m.get("id"),
            }
            for m in live
        ],
        "standings": standings,
        "top_scorers": sorted(all_scorers.items(), key=lambda x: x[1], reverse=True)[:20],
        "top_assists": sorted(all_assists.items(), key=lambda x: x[1], reverse=True)[:20],
        "cards": all_cards,
        "players": all_players,
        "live_matches": live_details,
    }

    # ── Сохраняем в файлы ──
    main_file = os.path.join(WC_DATA_DIR, "wc_2026_full.json")
    with open(main_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"WC data saved to {main_file} ({len(all_match_details)} matches)")

    # Краткая версия для контекста
    summary = {
        "updated": data["updated"],
        "finished": data["finished"],
        "upcoming": data["upcoming"],
        "results": [
            f"{m['home']} {m['score']} {m['away']} ({m['date'][:10]})"
            for m in all_match_details
        ],
        "top_scorers": data["top_scorers"][:10],
        "top_assists": data["top_assists"][:10],
        "standings": data["standings"][:15],
        "upcoming": data["upcoming_matches"][:15],
        "live": data["live_matches"],
    }
    summary_file = os.path.join(WC_DATA_DIR, "wc_2026_summary.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Файлы по командам
    teams_dir = os.path.join(WC_DATA_DIR, "teams")
    os.makedirs(teams_dir, exist_ok=True)

    teams_data = {}
    for m in all_match_details:
        for team_name in [m["home"], m["away"]]:
            if team_name not in teams_data:
                teams_data[team_name] = {
                    "name": team_name,
                    "matches": [],
                    "wins": 0,
                    "draws": 0,
                    "loss": 0,
                    "scored": 0,
                    "missed": 0,
                    "players": {},
                }

            td = teams_data[team_name]
            td["matches"].append({
                "opponent": m["away"] if team_name == m["home"] else m["home"],
                "score": m["score"],
                "date": m["date"][:10],
                "stage": m.get("stage", ""),
            })

            sc_h, sc_a = m["score"].split(":")
            sc_h, sc_a = int(sc_h), int(sc_a)
            is_home = team_name == m["home"]

            if (is_home and sc_h > sc_a) or (not is_home and sc_a > sc_h):
                td["wins"] += 1
            elif sc_h == sc_a:
                td["draws"] += 1
            else:
                td["loss"] += 1

            td["scored"] += sc_h if is_home else sc_a
            td["missed"] += sc_a if is_home else sc_h

            for lineup in m.get("lineups", []):
                if lineup["team"] == team_name:
                    for p in lineup["players"]:
                        p_name = p["name"]
                        if p_name not in td["players"]:
                            td["players"][p_name] = {
                                "minutes": 0,
                                "goals": 0,
                                "assists": 0,
                                "yellow": 0,
                                "red": 0,
                                "matches": 0,
                            }
                        tp = td["players"][p_name]
                        tp["minutes"] += p.get("minutes", 0) or 0
                        tp["goals"] += p.get("goals", 0) or 0
                        tp["assists"] += p.get("assists", 0) or 0
                        tp["yellow"] += p.get("yellow", 0) or 0
                        tp["red"] += p.get("red", 0) or 0
                        tp["matches"] += 1

    for team_name, td in teams_data.items():
        safe_name = team_name.lower().replace(" ", "_").replace("/", "_")
        team_file = os.path.join(teams_dir, f"{safe_name}.json")
        with open(team_file, "w", encoding="utf-8") as f:
            json.dump(td, f, ensure_ascii=False, indent=2)

    logger.info(f"WC team files: {len(teams_data)} teams saved")

    return data


def load_wc_data() -> Optional[dict]:
    """Загружает кэшированные данные ЧМ."""
    main_file = os.path.join(WC_DATA_DIR, "wc_2026_full.json")
    try:
        with open(main_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.error(f"WC data load error: {e}")
        return None


def search_wc_data(query: str, data: dict) -> str:
    """Ищет релевантную информацию по запросу в данных ЧМ."""
    if not data:
        return ""
    
    query_lower = query.lower()
    parts = []

    # ── Поиск по командам ──
    all_teams = set()
    for m in data.get("matches", []):
        all_teams.add(m["home"])
        all_teams.add(m["away"])
    for m in data.get("upcoming_matches", []):
        all_teams.add(m["home"])
        all_teams.add(m["away"])

    # Also check upcoming + live for team names
    mentioned_teams = []
    for team in all_teams:
        if team.lower() in query_lower:
            mentioned_teams.append(team)

    # Also fuzzy match common names
    TEAM_ALIASES = {
        "аргентин": "Argentina", "аргентины": "Argentina", "аргентина": "Argentina",
        "герман": "Germany", "германии": "Germany", "немцы": "Germany",
        "бразил": "Brazil", "бразилии": "Brazil",
        "франц": "France", "франции": "France",
        "испан": "Spain", "испании": "Spain",
        "англ": "England", "англии": "England",
        "португал": "Portugal", "португалии": "Portugal",
        "мексик": "Mexico", "мексики": "Mexico",
        "япон": "Japan", "японии": "Japan", "япошек": "Japan",
        "парагв": "Paraguay", "парагвая": "Paraguay",
        "эквадор": "Ecuador", "эквадора": "Ecuador",
        "норвег": "Norway", "норвегии": "Norway",
        "холанд": "Norway", "хааланд": "Norway",
        "месси": "Argentina", "мбаппе": "France", "mbappe": "France",
        "хаверц": "Germany", "havertz": "Germany",
        "сша": "USA", "америк": "USA",
        "кюрасао": "Curaçao", "курасао": "Curaçao",
        "китай": "China PR",
        "урогв": "Uruguay", "уругвая": "Uruguay",
        "нидерланд": "Netherlands", "голланд": "Netherlands",
        "бельг": "Belgium", "бельгии": "Belgium",
        "хорват": "Croatia", "хорватии": "Croatia",
        "марокк": "Morocco", "марокко": "Morocco",
        "сенегал": "Senegal", "сенегала": "Senegal",
        "иран": "Iran", "ирак": "Iraq",
        "ка Nadа": "Canada", "канад": "Canada", "канады": "Canada",
        "швец": "Sweden", "швеции": "Sweden",
        "швейцар": "Switzerland", "швейцарии": "Switzerland",
        "тунис": "Tunisia", "туниса": "Tunisia",
        "австр": "Austria", "австрии": "Austria",
        "австрали": "Australia", "австралии": "Australia",
    }
    for alias, team_name in TEAM_ALIASES.items():
        if alias in query_lower and team_name in all_teams and team_name not in mentioned_teams:
            mentioned_teams.append(team_name)

    if mentioned_teams:
        for team in mentioned_teams[:2]:
            # ALL matches of this team (finished)
            team_matches = [m for m in data.get("matches", []) if team in (m.get("home", ""), m.get("away", ""))]
            # Upcoming
            team_upcoming = [m for m in data.get("upcoming_matches", []) if team in (m.get("home", ""), m.get("away", ""))]
            # Live
            team_live = [m for m in data.get("live_matches", []) if team in (m.get("home", ""), m.get("away", ""))]

            if team_matches or team_upcoming or team_live:
                lines = []

                # Finished matches with FULL details
                for m in team_matches:
                    is_home = m["home"] == team
                    lines.append(f"  {m['home']} {m['score']} {m['away']} ({m['date'][:10]})")
                    # All events
                    for ev in m.get("events", []):
                        if ev.get("type") == "goal":
                            lines.append(f"    ⚽ Гол: {ev['player']} ({ev['minute']}', {ev.get('team', '?')})")
                        elif ev.get("type") == "red card":
                            lines.append(f"    🟥 Красная: {ev['player']} ({ev['minute']}')")
                        elif ev.get("type") == "yellow card":
                            lines.append(f"    🟨 Жёлтая: {ev['player']} ({ev['minute']}')")
                    # Lineups
                    for lineup in m.get("lineups", []):
                        if lineup.get("team") == team:
                            starters = [p["name"] for p in lineup.get("players", []) if not p.get("sub")]
                            subs = [p["name"] for p in lineup.get("players", []) if p.get("sub")]
                            if starters:
                                lines.append(f"    Основа: {', '.join(starters[:11])}")
                            if subs:
                                lines.append(f"    Замены: {', '.join(subs[:5])}")
                    # Stats
                    stats = m.get("stats", {})
                    if stats:
                        stat_lines = []
                        for key, val in list(stats.items())[:8]:
                            if isinstance(val, dict):
                                stat_lines.append(f"{key}: {val.get('home', 0)}-{val.get('away', 0)}")
                        if stat_lines:
                            lines.append(f"    Стат: {', '.join(stat_lines)}")

                # Upcoming
                for m in team_upcoming:
                    lines.append(f"  Предстоит: {m['home']} — {m['away']} ({m.get('date', '')[:16]})")

                # Live
                for m in team_live:
                    lines.append(f"  🔴 {m['home']} {m['score']} {m['away']} (сейчас)")
                    for ev in m.get("events", []):
                        if ev.get("type") == "goal":
                            lines.append(f"    ⚽ Гол: {ev['player']} ({ev['minute']}')")

                # Team record
                if team_matches:
                    wins = sum(1 for m in team_matches if (m["home"] == team and m["score"].split(":")[0] > m["score"].split(":")[1]) or (m["away"] == team and m["score"].split(":")[1] > m["score"].split(":")[0]))
                    draws = sum(1 for m in team_matches if m["score"].split(":")[0] == m["score"].split(":")[1])
                    losses = len(team_matches) - wins - draws
                    scored = sum(int(m["score"].split(":")[0]) if m["home"] == team else int(m["score"].split(":")[1]) for m in team_matches)
                    conceded = sum(int(m["score"].split(":")[1]) if m["home"] == team else int(m["score"].split(":")[0]) for m in team_matches)
                    lines.append(f"  ИТОГО: {len(team_matches)} матчей, {wins}В {draws}Н {losses}П, голы {scored}-{conceded}")

                parts.append(f"### ВСЕ матчи {team} на ЧМ 2026:\n" + "\n".join(lines))

                # ALL players of this team
                team_players = {}
                for m in team_matches:
                    for lineup in m.get("lineups", []):
                        if lineup.get("team") == team:
                            for p in lineup.get("players", []):
                                name = p["name"]
                                if name not in team_players:
                                    team_players[name] = {"matches": 0, "goals": 0, "subs": 0}
                                team_players[name]["matches"] += 1
                                team_players[name]["goals"] += p.get("goals", 0) or 0
                                if p.get("sub"):
                                    team_players[name]["subs"] += 1

                if team_players:
                    sorted_players = sorted(team_players.items(), key=lambda x: x[1]["matches"], reverse=True)
                    player_lines = []
                    for name, stats in sorted_players[:15]:
                        sub_text = f" ({stats['subs']} замен)" if stats["subs"] > 0 else ""
                        player_lines.append(f"  {name}: {stats['matches']} матчей, {stats['goals']} гол{sub_text}")
                    parts.append(f"### Игроки {team}:\n" + "\n".join(player_lines))

    # ── Поиск по игрокам ──
    all_players = data.get("players", {})
    mentioned_players = []
    for p_name in all_players:
        if p_name.lower() in query_lower:
            mentioned_players.append(p_name)

    if mentioned_players:
        for p_name in mentioned_players[:3]:
            p = all_players[p_name]
            avg_rating = p["rating_sum"] / p["rating_count"] if p["rating_count"] > 0 else 0
            parts.append(
                f"### Игрок {p_name}:\n"
                f"  Команда: {p['team']}\n"
                f"  Матчей: {p['matches']}\n"
                f"  Минут: {p['total_minutes']}\n"
                f"  Голов: {p['goals']}\n"
                f"  Ассистов: {p['assists']}\n"
                f"  Жёлтых: {p['yellow']}, Красных: {p['red']}\n"
                f"  Рейтинг: {avg_rating:.1f}"
            )

    # ── Топ бомбардиры ──
    if any(kw in query_lower for kw in ["бомбардир", "scorer", "кто забил", "лучший", "гол", "топ"]):
        scorers = data.get("top_scorers", [])
        if scorers:
            lines = [f"  {name}: {goals} голов" for name, goals in scorers[:10]]
            parts.append(f"### Топ бомбардиры ЧМ 2026:\n" + "\n".join(lines))

    # ── Топ ассистенты ──
    if any(kw in query_lower for kw in ["ассист", "assist", "передач", "голев"]):
        assists = data.get("top_assists", [])
        if assists:
            lines = [f"  {name}: {a} ассистов" for name, a in assists[:10]]
            parts.append(f"### Топ ассистенты ЧМ 2026:\n" + "\n".join(lines))

    # ── Таблица (если спрашивают) ──
    if any(kw in query_lower for kw in ["таблица", "standings", "место", "позиция", "очки", "кто вышел", "плей офф", "плей-офф", "1/8", "1/4"]):
        standings = data.get("standings", [])
        if standings:
            lines = [f"  {i+1}. {s['team']} — {s['points']} очк ({s['wins']}В {s['draws']}Н {s['loss']}П)" for i, s in enumerate(standings[:15])]
            parts.append(f"### Турнирная таблица:\n" + "\n".join(lines))

    # ── Результаты (если спрашивают) ──
    if any(kw in query_lower for kw in ["результат", "счёт", "счет", "побед", "кто выиграл", "матч"]):
        matches = data.get("matches", [])
        if matches and not mentioned_teams and not mentioned_players:
            lines = [f"  {m['home']} {m['score']} {m['away']} ({m['date'][:10]})" for m in matches[-15:]]
            parts.append(f"### Последние результаты:\n" + "\n".join(lines))

    # ── Предстоящие ──
    if any(kw in query_lower for kw in ["предстоит", "ближай", "следующ", "когда", "расписан", "upcoming", "завтра"]):
        upcoming = data.get("upcoming_matches", [])
        if upcoming:
            lines = [f"  {m['home']} — {m['away']} ({m['date'][:16]})" for m in upcoming[:10]]
            parts.append(f"### Предстоящие матчи:\n" + "\n".join(lines))

    # ── Live (с деталями) ──
    live = data.get("live_matches", [])
    if live:
        live_lines = []
        for m in live:
            live_lines.append(f"  🔴 {m['home']} {m['score']} {m['away']} (идёт сейчас)")
            if m.get("events"):
                for ev in m["events"]:
                    if "goal" in ev["type"] or "гол" in ev["type"]:
                        live_lines.append(f"    ⚽ Гол: {ev['player']} ({ev['minute']}')")
                    elif "red" in ev["type"] or "красн" in ev["type"]:
                        live_lines.append(f"    🟥 Красная: {ev['player']} ({ev['minute']}')")
            if m.get("lineups"):
                for lineup in m["lineups"][:2]:
                    starters = [p["name"] for p in lineup["players"] if not p.get("sub")][:11]
                    if starters:
                        live_lines.append(f"    Состав {lineup['team']}: {', '.join(starters)}")
        parts.append(f"### Live матчи:\n" + "\n".join(live_lines))

    # Всегда показываем live если есть (даже без вопроса про live)
    if live and not any("Live" in p for p in parts):
        live_lines = [f"  🔴 {m['home']} {m['score']} {m['away']} (сейчас)" for m in live]
        parts.append(f"### Live матчи:\n" + "\n".join(live_lines))

    # ── Ставки ──
    if any(kw in query_lower for kw in ["ставк", "баланс", "выиграл", "проиграл", "поставил", "юаней", "bet"]):
        parts.append(f"### Ставки Закури: смотри в разделе /bets")

    if parts:
        result = "\n\n".join(parts)
        if len(result) > 8000:
            result = result[:8000] + "\n[... обрезано ...]"
        return result
    return ""
