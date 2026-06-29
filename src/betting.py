"""Бот-лудоман: ставки, экспрессы, авто-постинг, проверка результатов.

Фичи:
- Ночной сон (23:00-09:00 МСК) — не постит ставки ночью
- Осмысленные сообщения через AI перед ставкой
- Неординарные ставки (гол Месси, гол конкретного игрока)
- Кредит если баланс < 500
- Отслеживание прибыли/убытков
- Результаты прошлых матчей
"""
import random
import logging
import asyncio
import json
import datetime
from typing import Optional

from src.football_api import FootballAPI

logger = logging.getLogger(__name__)

START_BALANCE = 50000

PLAYER_BETS = [
    "Гол Месси", "Гол Холанда", "Гол Мбаппе", "Гол Винисиуса",
    "Гол Дибалы", "Гол Родахо", "Гол Белингема", "Гол Фодена",
    "Гол Гарри Кейна", "Гол Левандовски", "Гол Мюллера",
    "Ассист Месси", "Ассист Мбаппе",
    "Холанд забьёт 2+", "Месси забьёт 2+",
    "Жёлтая карточка — Аргентина", "Красная карточка в матче",
    "Пенальти в матче", "Автогол в матче",
]

EXCITED_COMMENTS = [
    "Закури чувствует запах денег! Этот экспресс — золото!",
    "Драконье чутьё подсказывает — сегодня мой день!",
    "Закури ставит с уверенностью огнедышащего дракона!",
    "Кто не рискует — тот ест рис, а Закури ест победы!",
    "Месси бы одобрил эту ставку, я уверен!",
    "Закури не просто ставит — Закури ВЕРИТ!",
    "Эта ставка пахнет чемпионством! Аргентина вперёд!",
    "50 000 на кону и драконье сердце — что может пойти не так?",
    "Закури размышлял 3 секунды и решил — ставлю!",
    "Я плюшевый снаружи, но лудоман внутри! 🔥",
]


class BettingManager:
    def __init__(self, db, football_api: FootballAPI, ai_manager=None):
        self.db = db
        self.api = football_api
        self.ai = ai_manager

    def _is_night(self) -> bool:
        """Ночной сон 23:00-09:00 МСК (UTC+3)."""
        now_msk = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
        hour = now_msk.hour
        return hour >= 23 or hour < 9

    async def generate_bet(self, chat_id: int) -> Optional[dict]:
        """Генерирует ставку: одиночную, экспресс, или неординарную.
        Использует реальные коэффициенты из Pari.ru API."""
        
        # Сначала пробуем Pari.ru (реальные коэффициенты)
        pari_matches = await self.api.get_pari_upcoming_matches(limit=100)
        
        # Фильтруем матчи с коэффициентами
        available = []
        for m in pari_matches:
            w1 = m.get("winner1", 0)
            wx = m.get("winnerX", 0)
            w2 = m.get("winner2", 0)
            if w1 and wx and w2 and w1 > 1.0:
                available.append(m)

        # Fallback на SStats если Pari.ru пуст
        if not available:
            wc_matches = await self.api.get_matches_by_league(1, 2026)
            today = await self.api.get_today_matches()
            upcoming = await self.api.get_upcoming_matches(limit=30)

            wc_uid = "979a850f-d343-11f0-982f-3cecef730a49"
            wc_all = [m for m in wc_matches if m.get("status") in (1, 2)]
            
            available = today + upcoming + wc_all

            filtered = []
            for m in available:
                w1 = m.get("winner1", 0)
                wx = m.get("winnerX", 0)
                w2 = m.get("winner2", 0)
                if w1 and wx and w2 and w1 > 1.0:
                    filtered.append(m)
            available = filtered

            # Fallback: генерируем коэффициенты
            if not available:
                for m in (today + upcoming)[:10]:
                    home = (m.get("homeTeam") or {}).get("name", "?")
                    away = (m.get("awayTeam") or {}).get("name", "?")
                    if home != "?":
                        m["winner1"] = round(random.uniform(1.5, 3.5), 2)
                        m["winnerX"] = round(random.uniform(2.8, 4.0), 2)
                        m["winner2"] = round(random.uniform(1.5, 3.5), 2)
                        available.append(m)

        if not available:
            return None

        available.sort(key=lambda x: x.get("date", ""))

        r = random.randint(1, 100)
        if r <= 10:
            bet_type = "express"
            num_matches = random.randint(6, 7)
        elif r <= 25:
            bet_type = "express"
            num_matches = random.randint(4, 5)
        elif r <= 45:
            bet_type = "express"
            num_matches = random.randint(2, 3)
        elif r <= 65:
            bet_type = "single"
            num_matches = 1
        else:
            bet_type = "player_bet"
            num_matches = 1

        num_matches = min(num_matches, len(available))
        if num_matches == 0:
            bet_type = "single"
            num_matches = 1

        selected = random.sample(available, num_matches) if num_matches <= len(available) else available[:num_matches]
        selections = []
        total_odds = 1.0
        match_ids = []

        for m in selected:
            home = m.get("homeTeamName") or m.get("homeTeam", {}).get("name", "?")
            away = m.get("awayTeamName") or m.get("awayTeam", {}).get("name", "?")
            w1 = m.get("winner1", 0)
            wx = m.get("winnerX", 0)
            w2 = m.get("winner2", 0)
            match_id = m.get("id", 0)
            match_ids.append(str(match_id))

            if bet_type == "player_bet":
                pick = random.choice(PLAYER_BETS)
                odds = round(random.uniform(2.5, 6.0), 2)
            else:
                pick_type = random.randint(1, 100)
                if pick_type <= 25:
                    pick, odds = "П1", w1
                elif pick_type <= 45:
                    pick, odds = "П2", w2
                elif pick_type <= 60:
                    pick, odds = "ТБ 2.5", round(max(w1 * 1.4, 1.7), 2)
                elif pick_type <= 75:
                    pick, odds = "Обе забьют — Да", round(max(w1 * 1.15, 1.5), 2)
                elif pick_type <= 85:
                    pick, odds = "Ничья", wx
                elif pick_type <= 92:
                    pick, odds = "ТМ 2.5", round(max(min(w1, w2) * 0.9, 1.6), 2)
                else:
                    pick, odds = "Обе забьют — Нет", round(max(min(w1, w2) * 1.1, 1.8), 2)

            total_odds *= odds
            selections.append({
                "match_id": match_id,
                "home": home,
                "away": away,
                "pick": pick,
                "odds": round(odds, 2),
                "w1": w1,
                "wx": wx,
                "w2": w2,
            })

        total_odds = round(total_odds, 2)

        bal = await self.db.get_balance(chat_id)
        balance = bal["balance"]
        is_credit = False

        max_stake = min(balance * 0.15, 5000)
        if max_stake < 500:
            if balance < 500:
                credit_amount = random.randint(2000, 5000)
                await self.db.take_credit(chat_id, credit_amount)
                balance = (await self.db.get_balance(chat_id))["balance"]
                is_credit = True
            max_stake = min(balance * 0.15, 5000)

        if max_stake < 100:
            return None

        if bet_type == "express" and num_matches >= 5:
            stake = random.randint(int(max_stake * 0.2), int(max_stake * 0.5))
        elif bet_type == "express":
            stake = random.randint(int(max_stake * 0.4), int(max_stake * 0.8))
        elif bet_type == "player_bet":
            stake = random.randint(int(max_stake * 0.3), int(max_stake * 0.6))
        else:
            stake = random.randint(int(max_stake * 0.5), int(max_stake))

        stake = max(stake, 100)
        potential = round(stake * total_odds, 2)

        bet_id = await self.db.place_bet(
            chat_id=chat_id,
            bet_type=bet_type,
            match_ids=",".join(match_ids),
            selections=json.dumps(selections, ensure_ascii=False),
            odds=total_odds,
            stake=stake,
            is_credit=is_credit,
        )

        return {
            "bet_id": bet_id,
            "bet_type": bet_type,
            "num_matches": num_matches,
            "selections": selections,
            "total_odds": total_odds,
            "stake": stake,
            "potential": potential,
            "is_credit": is_credit,
            "balance_after": balance - stake,
        }

    async def generate_intro_message(self, bet: dict) -> str:
        """Генерирует осмысленное вступление через AI."""
        if not self.ai:
            return random.choice(EXCITED_COMMENTS)

        try:
            selections_text = "\n".join(
                f"  {s['home']} — {s['away']}: {s['pick']} ({s['odds']})"
                for s in bet["selections"]
            )

            response = await self.ai.chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты дракон Закури — лудоман и фанат Аргентины. "
                            "Напиши 1-2 предложения ОСМЫСЛЕННОГО комментария к своей ставке. "
                            "Почему именно эта ставка. С уверенностью. С характером. "
                            "Без эмодзи в начале. Не повторяй сами матчи. "
                            "Кратко, живо, как в чате."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Моя ставка ({bet['bet_type']}):\n{selections_text}\n"
                            f"Коэффициент: {bet['total_odds']}\n"
                            f"Ставлю: {bet['stake']} юаней"
                        ),
                    },
                ],
                temperature=0.8,
                max_tokens=100,
            )
            return response.strip() if response else random.choice(EXCITED_COMMENTS)
        except Exception:
            return random.choice(EXCITED_COMMENTS)

    def format_bet_message(self, bet: dict, intro: str = "") -> str:
        """Форматирует ставку для отправки в чат."""
        if bet["bet_type"] == "express":
            header = f"🎰 ЗАКУРИ СОБРАЛ ЭКСПРЕСС ({bet['num_matches']} матчей)!\n\n"
        elif bet["bet_type"] == "player_bet":
            header = "🎯 ЗАКУРИ СТАВИТ НА ИГРОКА!\n\n"
        else:
            header = "🎰 ЗАКУРИ СТАВИТ!\n\n"

        if intro:
            header += f"💬 {intro}\n\n"

        lines = []
        for i, s in enumerate(bet["selections"], 1):
            lines.append(f"{i}️⃣ {s['home']} — {s['away']}: {s['pick']} ({s['odds']})")

        lines.append(f"\n💰 Общий коэффициент: {bet['total_odds']}")
        lines.append(f"💸 Сумма: {bet['stake']} юаней")
        lines.append(f"🎯 Возможный выигрыш: {bet['potential']:.0f} юаней")
        lines.append(f"💳 Баланс после: {bet['balance_after']:.0f} юаней")

        if bet.get("is_credit"):
            lines.append("💳 Ставка на кредитные юанейы — Закури в долг! 🔥")

        if bet["bet_type"] == "express" and bet["num_matches"] >= 5:
            lines.append("\n🐉 ЖЕСТКИЙ ЭКСПРЕСС! Кто не рискует — тот не пьёт шампанское! 🔥")
        elif bet["bet_type"] == "player_bet":
            lines.append("\n🎯 Закури чувствует гол конкретного игрока! Неординарная ставка! 🔥")
        elif bet["bet_type"] == "express":
            lines.append("\n🐉 Закури уверен на 100%! 🔥")
        else:
            lines.append("\n🐉 Закури уверен в этой ставке! 🔥")

        return header + "\n".join(lines)

    async def check_and_settle(self, chat_id: int) -> list[dict]:
        """Проверяет pending ставки и закрывает их если матчи завершены."""
        pending = await self.db.get_pending_bets(chat_id)
        results = []

        for bet in pending:
            match_ids = bet["match_ids"].split(",")
            selections = json.loads(bet["selections"])

            all_finished = True
            all_won = True

            for sel in selections:
                match = await self.api.get_match_details(sel["match_id"])
                if not match:
                    all_finished = False
                    break

                status = match.get("status", 0)
                if status not in (8, 9, 10, 17, 18):
                    all_finished = False
                    break

                score_h = match.get("scoreHomeFT", 0) or 0
                score_a = match.get("scoreAwayFT", 0) or 0
                total = score_h + score_a

                pick = sel["pick"]
                won = self._check_bet_won(pick, score_h, score_a, total, match)

                if not won:
                    all_won = False

            if all_finished:
                if all_won:
                    actual = bet["potential_return"]
                    await self.db.settle_bet(bet["id"], "won", actual)
                    await self.db.update_balance(chat_id, actual - bet["stake"], won=actual, lost=0, bet_won=True)
                    results.append({"bet": bet, "status": "won", "return": actual})
                else:
                    await self.db.settle_bet(bet["id"], "lost", 0)
                    await self.db.update_balance(chat_id, -bet["stake"], won=0, lost=bet["stake"], bet_won=False)
                    results.append({"bet": bet, "status": "lost", "return": 0})

        return results

    def _check_bet_won(self, pick: str, score_h: int, score_a: int, total: int, match: dict) -> bool:
        if pick == "П1" and score_h > score_a:
            return True
        elif pick == "П2" and score_a > score_h:
            return True
        elif pick == "Ничья" and score_h == score_a:
            return True
        elif pick == "ТБ 2.5" and total > 2.5:
            return True
        elif pick == "ТМ 2.5" and total < 2.5:
            return True
        elif pick == "Обе забьют — Да" and score_h > 0 and score_a > 0:
            return True
        elif pick == "Обе забьют — Нет" and (score_h == 0 or score_a == 0):
            return True

        events = match.get("events", [])
        if events and any(kw in pick for kw in ["Гол", "Ассист", "забьёт", "карточк", "Пенальти", "Автогол"]):
            for ev in events:
                ev_type = ev.get("type", "")
                ev_text = ev.get("text", "") or ev.get("player", "")
                ev_lower = (ev_type + " " + str(ev_text)).lower()

                if "Гол" in pick:
                    player_name = pick.replace("Гол ", "").split(" забьёт")[0].strip()
                    if "goal" in ev_lower.lower() or "гол" in ev_lower.lower():
                        if player_name.lower() in ev_lower:
                            if "2+" in pick:
                                goals_count = sum(1 for e in events if player_name.lower() in (e.get("text", "") or "").lower() and ("goal" in (e.get("type", "")).lower() or "гол" in (e.get("type", "")).lower()))
                                return goals_count >= 2
                            return True
                elif "Ассист" in pick:
                    player_name = pick.replace("Ассист ", "").strip()
                    if "assist" in ev_lower or "ассист" in ev_lower:
                        if player_name.lower() in ev_lower:
                            return True
                elif "Пенальти" in pick:
                    if "penalty" in ev_lower or "пенальт" in ev_lower:
                        return True
                elif "Красная" in pick:
                    if "red" in ev_lower or "красн" in ev_lower:
                        return True
                elif "Жёлтая" in pick or "Желтая" in pick:
                    team_name = pick.split("—")[-1].strip() if "—" in pick else ""
                    if "yellow" in ev_lower or "жёлт" in ev_lower or "желт" in ev_lower:
                        return True
                elif "Автогол" in pick:
                    if "own goal" in ev_lower or "автогол" in ev_lower:
                        return True

        return False

    async def format_result_message_async(self, result: dict) -> str:
        bet = result["bet"]
        selections = json.loads(bet["selections"])
        status = result["status"]

        bal = await self.db.get_balance(bet["chat_id"])

        if status == "won":
            profit = result["return"] - bet["stake"]
            if bet["bet_type"] == "express":
                header = f"✅ ЗАКУРИ ВЫИГРАЛ ЭКСПРЕСС ({len(selections)} матчей)!\n\n"
            else:
                header = "✅ ЗАКУРИ ВЫИГРАЛ СТАВКУ!\n\n"
            lines = []
            for s in selections:
                lines.append(f"✅ {s['home']} — {s['away']}: {s['pick']} — зашло!")
            lines.append(f"\n💰 Выигрыш: +{profit:.0f} юаней")
            lines.append(f"💸 Коэффициент: {bet['odds']}")
            lines.append(f"💰 Баланс: {bal['balance']:.0f} юаней")
            lines.append("\n🐉 Закури опять прав! Кто сомневался — тот лох! 🔥")
        else:
            header = "❌ ЗАКУРИ ПРОИГРАЛ СТАВКУ!\n\n"
            lines = []
            for s in selections:
                lines.append(f"❌ {s['home']} — {s['away']}: {s['pick']} — не зашло")
            lines.append(f"\n💸 Потеря: -{bet['stake']:.0f} юаней")
            lines.append(f"💰 Баланс: {bal['balance']:.0f} юаней")
            if bal["credit"] > 0:
                lines.append(f"💳 Долг: {bal['credit']:.0f} юаней")
            lines.append("\n🐉 Ну бывает... Закури не унывает! Следующая ставка будет верной! 💪")

        return header + "\n".join(lines)

    async def get_stats(self, chat_id: int) -> str:
        bal = await self.db.get_balance(chat_id)
        recent = await self.db.get_recent_bets(chat_id, 20)
        pending = await self.db.get_pending_bets(chat_id)

        won = bal["wins_count"]
        total = bal["bets_count"]
        roi = ((bal["total_won"] - bal["total_lost"]) / bal["total_lost"] * 100) if bal["total_lost"] > 0 else 0
        profit = bal["total_won"] - bal["total_lost"]

        lines = [
            f"🎰 <b>Статистика Закури-лудомана</b>\n",
            f"💰 Баланс: {bal['balance']:.0f} юаней",
        ]
        if bal["credit"] > 0:
            lines.append(f"💳 Долг: {bal['credit']:.0f} юаней")
        lines.extend([
            f"📈 Прибыль/убыток: {profit:+.0f} юаней",
            f"📊 Всего ставок: {total}",
            f"✅ Выиграно: {won}",
            f"❌ Проиграно: {total - won}",
            f"🎯 Винрейт: {(won/total*100):.0f}%" if total > 0 else "🎯 Винрейт: —",
            f"📈 ROI: {roi:+.1f}%",
            f"💵 Всего выиграно: {bal['total_won']:.0f}",
            f"💸 Всего проиграно: {bal['total_lost']:.0f}",
            f"\n⏳ Ожидают результата: {len(pending)}",
        ])

        if recent:
            lines.append("\n<b>Последние ставки:</b>")
            for b in recent[:5]:
                emoji = "✅" if b["status"] == "won" else ("❌" if b["status"] == "lost" else "⏳")
                sels = json.loads(b["selections"])
                first = sels[0] if sels else {}
                match_name = f"{first.get('home', '?')} — {first.get('away', '?')}"
                if len(sels) > 1:
                    match_name += f" +{len(sels)-1}"
                lines.append(f"  {emoji} {match_name} — {b['stake']:.0f} @ {b['odds']} → {b['status']}")

        return "\n".join(lines)


async def auto_betting_loop(bot, db, football_api: FootballAPI, chat_id: int, ai_manager=None):
    """Background loop: ставки + факты + статистика.
    
    Интервалы:
    - Ставки: 30-120 мин (днём, не ночью)
    - Факты/статистика: 60-180 мин (днём, не ночью)
    - Результаты: проверяются всегда (даже ночью)
    
    Бот ставит ТОЛЬКО на ЧМ (league ID 1).
    """
    betting = BettingManager(db, football_api, ai_manager)
    last_bet_time = 0
    last_fact_time = 0

    while True:
        await asyncio.sleep(60)

        try:
            results = await betting.check_and_settle(chat_id)
            for r in results:
                msg = await betting.format_result_message_async(r)
                await bot.send_message(chat_id, msg)
                await asyncio.sleep(2)

            now = datetime.datetime.utcnow().timestamp()
            is_night = betting._is_night()

            if not is_night and now - last_bet_time > random.randint(1800, 7200):
                bet = await betting.generate_bet(chat_id)
                if bet:
                    intro = await betting.generate_intro_message(bet)
                    msg = betting.format_bet_message(bet, intro)
                    await bot.send_message(chat_id, msg)
                    last_bet_time = now

            if not is_night and now - last_fact_time > random.randint(3600, 10800):
                fact = await _generate_football_fact(db, football_api, ai_manager)
                if fact:
                    await bot.send_message(chat_id, fact)
                    last_fact_time = now

        except Exception as e:
            logger.error(f"Auto loop error: {e}")


async def _generate_football_fact(db, football_api: FootballAPI, ai_manager) -> Optional[str]:
    """Генерирует факт или статистику по ЧМ через AI."""
    try:
        wc_matches = await football_api.get_matches_by_league(1, 2026)
        finished = [m for m in wc_matches if m.get("status") in (8, 9, 10, 17, 18)]
        upcoming = [m for m in wc_matches if m.get("status") in (1, 2)]

        context_parts = []
        if finished:
            recent = finished[-10:]
            for m in recent:
                home = (m.get("homeTeam") or {}).get("name", "?")
                away = (m.get("awayTeam") or {}).get("name", "?")
                sc_h = m.get("homeResult", 0)
                sc_a = m.get("awayResult", 0)
                context_parts.append(f"{home} {sc_h}:{sc_a} {away}")

        if upcoming:
            next_matches = upcoming[:5]
            for m in next_matches:
                home = (m.get("homeTeam") or {}).get("name", "?")
                away = (m.get("awayTeam") or {}).get("name", "?")
                date = (m.get("date") or "")[:16]
                context_parts.append(f"Предстоящий: {home} — {away} ({date})")

        if not context_parts:
            return None

        standings = await football_api.get_standings(1, 2026)
        if standings:
            top5 = standings[:5]
            table_str = ", ".join(
                f"{t.get('teamName', '?')} ({t.get('points', 0)} очк, {t.get('goalsScored', 0)} голов)"
                for t in top5
            )
            context_parts.append(f"Топ-5 таблицы: {table_str}")

        context = "\n".join(context_parts)

        response = await ai_manager.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты дракон Закури — футбольный эксперт и лудоман. "
                        "Поделись интересным фактом, статистикой или наблюдением по ЧМ 2026. "
                        "Используй РЕАЛЬНЫЕ данные из контекста. "
                        "1-3 предложения. С характером, с мнением. "
                        "Можешь вспомнить исторический факт о ЧМ. "
                        "Не используй эмодзи в начале. "
                        "Не повторяй просто данные — добавь мнение Закури."
                    ),
                },
                {"role": "user", "content": f"Данные ЧМ 2026:\n{context}"},
            ],
            temperature=0.8,
            max_tokens=200,
        )

        if response and response.strip():
            return f"🐉 {response.strip()}"

        return None
    except Exception as e:
        logger.error(f"Fact generation error: {e}")
        return None
