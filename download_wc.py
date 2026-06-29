"""Разовая выгрузка всех данных ЧМ 2026 в локальные файлы.
Запуск: python3 download_wc.py
"""
import asyncio
import json
import os
import sys
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", stream=sys.stdout)
logger = logging.getLogger("wc_download")

from src.football_api import FootballAPI

API_KEY = "sjzgn3bbco67pk8j"
WC_LEAGUE_ID = 1
WC_YEAR = 2026
OUT_DIR = os.path.join("docs", "wc_data")
TEAMS_DIR = os.path.join(OUT_DIR, "teams")
MATCHES_DIR = os.path.join(OUT_DIR, "matches")


async def download_all():
    api = FootballAPI(API_KEY)
    logger.info("Fetching all WC 2026 matches...")
    matches = await api.get_matches_by_league(WC_LEAGUE_ID, WC_YEAR)
    logger.info(f"Total matches: {len(matches)}")

    finished = [m for m in matches if m.get("status") in (8, 9, 10, 17, 18)]
    upcoming = [m for m in matches if m.get("status") in (1, 2)]
    live = [m for m in matches if m.get("status") in (3, 4, 5, 6, 7, 11, 19)]
    logger.info(f"Finished: {len(finished)}, Upcoming: {len(upcoming)}, Live: {len(live)}")

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(TEAMS_DIR, exist_ok=True)
    os.makedirs(MATCHES_DIR, exist_ok=True)

    all_matches = []
    all_scorers = {}
    all_assists = {}
    all_cards = {}
    all_players = {}
    teams_data = {}

    for i, m in enumerate(finished):
        game_id = m.get("id")
        if not game_id:
            continue

        home_name = (m.get("homeTeam") or {}).get("name", "?")
        away_name = (m.get("awayTeam") or {}).get("name", "?")
        home_id = (m.get("homeTeam") or {}).get("id", 0)
        away_id = (m.get("awayTeam") or {}).get("id", 0)
        sc_h = m.get("homeResult", 0) or 0
        sc_a = m.get("awayResult", 0) or 0
        date = (m.get("date") or "")[:19]

        logger.info(f"[{i+1}/{len(finished)}] {home_name} {sc_h}:{sc_a} {away_name}")

        details = await api.get_match_details(game_id)
        await asyncio.sleep(2.2)

        match_info = {
            "id": game_id,
            "home": home_name,
            "away": away_name,
            "home_id": home_id,
            "away_id": away_id,
            "score": f"{sc_h}:{sc_a}",
            "score_h": sc_h,
            "score_a": sc_a,
            "date": date,
            "status": m.get("status"),
            "stage": m.get("stage") or "",
            "round": m.get("round") or "",
            "venue": "",
            "referee": "",
            "attendance": 0,
            "events": [],
            "lineups": [],
            "stats": {},
        }

        if details:
            venue = details.get("venue")
            if isinstance(venue, dict):
                match_info["venue"] = venue.get("name", "")
            elif isinstance(venue, str):
                match_info["venue"] = venue

            ref = details.get("refereeName")
            if isinstance(ref, dict):
                match_info["referee"] = ref.get("name", "")
            elif isinstance(ref, str):
                match_info["referee"] = ref

            game = details.get("game", {})
            if isinstance(game, dict):
                match_info["attendance"] = game.get("attendance", 0) or 0
                match_info["stage"] = game.get("stage") or match_info["stage"]

            # Events
            events = details.get("events", [])
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
                team_name = home_name if team_id == home_id else away_name

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

                ev_info = {
                    "type": ev_type,
                    "player": player_name,
                    "minute": minute,
                    "team": team_name,
                    "assist": assist_name,
                }
                match_info["events"].append(ev_info)

                clean_player = (player_name or "").split("(")[0].strip() if player_name else ""
                if ev_type == "goal":
                    if clean_player and clean_player != "?":
                        all_scorers[clean_player] = all_scorers.get(clean_player, 0) + 1
                    if assist_name:
                        clean_assist = (assist_name or "").split("(")[0].strip() if assist_name else ""
                        if clean_assist and clean_assist != "?":
                            all_assists[clean_assist] = all_assists.get(clean_assist, 0) + 1
                elif ev_type == "yellow card":
                    if clean_player and clean_player != "?":
                        all_cards[clean_player] = all_cards.get(clean_player, {"yellow": 0, "red": 0})
                        all_cards[clean_player]["yellow"] += 1
                elif ev_type == "red card":
                    if clean_player and clean_player != "?":
                        all_cards[clean_player] = all_cards.get(clean_player, {"yellow": 0, "red": 0})
                        all_cards[clean_player]["red"] += 1

            # Lineups
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
                p_team = home_name if p_team_id == home_id else away_name

                player_info = {
                    "name": p_name,
                    "position": p_pos,
                    "sub": not p_start,
                    "team": p_team,
                }
                match_lineups.append(player_info)

                if p_name and p_name != "?":
                    if p_name not in all_players:
                        all_players[p_name] = {
                            "name": p_name,
                            "team": p_team,
                            "matches": 0,
                            "goals": 0,
                            "assists": 0,
                            "yellow": 0,
                            "red": 0,
                        }
                    all_players[p_name]["matches"] += 1
                    if p_team not in [t for t in teams_data]:
                        pass

            match_info["lineups"] = match_lineups

            # Player stats
            player_stats = details.get("playerStats", [])
            for ps in player_stats:
                ps_name = ps.get("playerName") or ps.get("playerId")
                if not ps_name:
                    continue
                # Find player in all_players by checking lineup
                for p in lineup_players:
                    if p.get("playerId") == ps.get("playerId"):
                        ps_name = p.get("playerName", "?")
                        break

                if ps_name and ps_name in all_players:
                    goals = ps.get("goalsTotal", 0) or 0
                    assists = ps.get("goalsAssists", 0) or 0
                    if goals:
                        all_players[ps_name]["goals"] = max(all_players[ps_name]["goals"], goals)
                    if assists:
                        all_players[ps_name]["assists"] = max(all_players[ps_name]["assists"], assists)

            # Statistics
            stats = details.get("statistics", {})
            if stats:
                match_stats = {}
                stat_fields = [
                    "shotsOnGoalHome", "shotsOnGoalAway",
                    "shotsOffGoalHome", "shotsOffGoalAway",
                    "totalShotsHome", "totalShotsAway",
                    "blockedShotsHome", "blockedShotsAway",
                    "cornerKicksHome", "cornerKicksAway",
                    "foulsHome", "foulsAway",
                    "yellowCardsHome", "yellowCardsAway",
                    "redCardsHome", "redCardsAway",
                    "ballPossessionHome", "ballPossessionAway",
                    "offsidesHome", "offsidesAway",
                    "goalkeeperSavesHome", "goalkeeperSavesAway",
                    "totalPassesHome", "totalPassesAway",
                    "passesAccurateHome", "passesAccurateAway",
                    "crossesHome", "crossesAway",
                    "interceptionsHome", "interceptionsAway",
                    "tacklesTotalHome", "tacklesTotalAway",
                    "duelsWonHome", "duelsWonAway",
                    "freeKicksHome", "freeKicksAway",
                    "throwinsHome", "throwinsAway",
                    "expectedGoalsHome", "expectedGoalsAway",
                    "bigChancesHome", "bigChancesAway",
                ]
                for field in stat_fields:
                    if field in stats and stats[field] is not None:
                        match_stats[field] = stats[field]
                match_info["stats"] = match_stats

        all_matches.append(match_info)

        # Save individual match file
        match_file = os.path.join(MATCHES_DIR, f"{game_id}.json")
        with open(match_file, "w", encoding="utf-8") as f:
            json.dump(match_info, f, ensure_ascii=False, indent=2)

        # Update teams data
        for team_name in [home_name, away_name]:
            if team_name not in teams_data:
                teams_data[team_name] = {
                    "name": team_name,
                    "matches": [],
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "scored": 0,
                    "conceded": 0,
                    "players": {},
                }

        # Home team record
        td_home = teams_data[home_name]
        td_home["matches"].append({
            "opponent": away_name,
            "score": match_info["score"],
            "date": date[:10],
            "stage": match_info["stage"],
            "home": True,
            "result": "win" if sc_h > sc_a else ("draw" if sc_h == sc_a else "loss"),
        })
        if sc_h > sc_a:
            td_home["wins"] += 1
        elif sc_h == sc_a:
            td_home["draws"] += 1
        else:
            td_home["losses"] += 1
        td_home["scored"] += sc_h
        td_home["conceded"] += sc_a

        # Away team record
        td_away = teams_data[away_name]
        td_away["matches"].append({
            "opponent": home_name,
            "score": match_info["score"],
            "date": date[:10],
            "stage": match_info["stage"],
            "home": False,
            "result": "win" if sc_a > sc_h else ("draw" if sc_h == sc_a else "loss"),
        })
        if sc_a > sc_h:
            td_away["wins"] += 1
        elif sc_h == sc_a:
            td_away["draws"] += 1
        else:
            td_away["losses"] += 1
        td_away["scored"] += sc_a
        td_away["conceded"] += sc_h

        # Update team players from lineups
        for p in match_info["lineups"]:
            team = p["team"]
            if team in teams_data:
                td = teams_data[team]
                pname = p["name"]
                if pname and pname != "?":
                    if pname not in td["players"]:
                        td["players"][pname] = {
                            "position": p["position"],
                            "matches": 0,
                            "subs": 0,
                        }
                    td["players"][pname]["matches"] += 1
                    if p["sub"]:
                        td["players"][pname]["subs"] += 1

        # Update player goals/assists from events
        for ev in match_info["events"]:
            if ev["type"] == "goal":
                scorer = ev["player"]
                team = ev["team"]
                if team in teams_data and scorer in teams_data[team]["players"]:
                    td = teams_data[team]["players"][scorer]
                    td["goals"] = td.get("goals", 0) + 1
                assist = ev.get("assist", "")
                if assist and team in teams_data and assist in teams_data[team]["players"]:
                    td = teams_data[team]["players"][assist]
                    td["assists"] = td.get("assists", 0) + 1
            elif ev["type"] in ("yellow card", "red card"):
                player = ev["player"]
                team = ev["team"]
                if team in teams_data and player in teams_data[team]["players"]:
                    td = teams_data[team]["players"][player]
                    key = "yellow" if ev["type"] == "yellow card" else "red"
                    td[key] = td.get(key, 0) + 1

    # ── Save team files ──
    for team_name, td in teams_data.items():
        safe_name = team_name.replace("/", "_").replace(" ", "_")
        team_file = os.path.join(TEAMS_DIR, f"{safe_name}.json")
        with open(team_file, "w", encoding="utf-8") as f:
            json.dump(td, f, ensure_ascii=False, indent=2)

    # ── Save all matches ──
    all_file = os.path.join(OUT_DIR, "all_matches.json")
    with open(all_file, "w", encoding="utf-8") as f:
        json.dump(all_matches, f, ensure_ascii=False, indent=2)

    # ── Save scorers ──
    scorers_file = os.path.join(OUT_DIR, "scorers.json")
    with open(scorers_file, "w", encoding="utf-8") as f:
        json.dump(sorted(all_scorers.items(), key=lambda x: x[1], reverse=True), f, ensure_ascii=False, indent=2)

    # ── Save assists ──
    assists_file = os.path.join(OUT_DIR, "assists.json")
    with open(assists_file, "w", encoding="utf-8") as f:
        json.dump(sorted(all_assists.items(), key=lambda x: x[1], reverse=True), f, ensure_ascii=False, indent=2)

    # ── Save cards ──
    cards_file = os.path.join(OUT_DIR, "cards.json")
    with open(cards_file, "w", encoding="utf-8") as f:
        json.dump(all_cards, f, ensure_ascii=False, indent=2)

    # ── Save players ──
    players_file = os.path.join(OUT_DIR, "players.json")
    with open(players_file, "w", encoding="utf-8") as f:
        json.dump(all_players, f, ensure_ascii=False, indent=2)

    # ── Save upcoming ──
    upcoming_file = os.path.join(OUT_DIR, "upcoming.json")
    with open(upcoming_file, "w", encoding="utf-8") as f:
        json.dump([
            {
                "home": (m.get("homeTeam") or {}).get("name", "?"),
                "away": (m.get("awayTeam") or {}).get("name", "?"),
                "date": (m.get("date") or "")[:19],
                "id": m.get("id"),
                "stage": m.get("stage") or "",
            }
            for m in upcoming
        ], f, ensure_ascii=False, indent=2)

    # ── Save standings ──
    standings = await api.get_standings(WC_LEAGUE_ID, WC_YEAR)
    standings_file = os.path.join(OUT_DIR, "standings.json")
    with open(standings_file, "w", encoding="utf-8") as f:
        json.dump(standings, f, ensure_ascii=False, indent=2)

    # ── Save index ──
    index = {
        "updated": datetime.now().isoformat(),
        "total_matches": len(matches),
        "finished": len(finished),
        "upcoming": len(upcoming),
        "live": len(live),
        "teams": sorted(teams_data.keys()),
        "top_scorers": sorted(all_scorers.items(), key=lambda x: x[1], reverse=True)[:20],
        "top_assists": sorted(all_assists.items(), key=lambda x: x[1], reverse=True)[:20],
    }
    index_file = os.path.join(OUT_DIR, "index.json")
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    await api.close()

    logger.info(f"=== DONE ===")
    logger.info(f"Matches: {len(all_matches)}")
    logger.info(f"Teams: {len(teams_data)}")
    logger.info(f"Scorers: {len(all_scorers)}")
    logger.info(f"Assists: {len(all_assists)}")
    logger.info(f"Players: {len(all_players)}")
    logger.info(f"Files saved to {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(download_all())
