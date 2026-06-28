"""Бот-лудоман: ставки, экспрессы, авто-постинг, проверка результатов."""
import random
import logging
import asyncio
import json
from typing import Optional

from src.football_api import FootballAPI

logger = logging.getLogger(__name__)

START_BALANCE = 50000
FAVORITE_TEAM = "Argentina"

BET_TYPES = [
    ("П1", "win1", "winner1"),
    ("П2", "win2", "winner2"),
    ("Ничья", "draw", "winnerX"),
    ("ТБ 2.5", "over25", None),
    ("ТМ 2.5", "under25", None),
    ("Обе забьют — Да", "btts_yes", None),
    ("Обе забьют — Нет", "btts_no", None),
]


class BettingManager:
    def __init__(self, db, football_api: FootballAPI):
        self.db = db
        self.api = football_api

    async def generate_bet(self, chat_id: int) -> Optional[dict]:
        """Генерирует ставку: одиночную или экспресс (иногда до 7 матчей)."""
        matches = await self.api.get_today_matches()
        upcoming = await self.api.get_upcoming_matches(limit=20)

        available = []
        for m in matches + upcoming:
            w1 = m.get("winner1", 0)
            wx = m.get("winnerX", 0)
            w2 = m.get("winner2", 0)
            if w1 and wx and w2 and w1 > 1.0:
                available.append(m)

        if not available:
            return None

        available.sort(key=lambda x: x.get("date", ""))

        r = random.randint(1, 100)
        if r <= 15:
            bet_type = "express"
            num_matches = random.randint(5, 7)
        elif r <= 40:
            bet_type = "express"
            num_matches = random.randint(2, 4)
        else:
            bet_type = "single"
            num_matches = 1

        num_matches = min(num_matches, len(available))
        if num_matches == 0:
            return None

        selected = random.sample(available, num_matches)
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

            if w1 <= 1.5:
                pick, odds = "П1", w1
            elif w2 <= 1.5:
                pick, odds = "П2", w2
            elif random.randint(1, 100) <= 30:
                pick, odds = "ТБ 2.5", max(w1 * 1.3, 1.6)
            elif random.randint(1, 100) <= 20:
                pick, odds = "Обе забьют — Да", max(w1 * 1.2, 1.5)
            elif random.randint(1, 100) <= 15:
                pick, odds = "Ничья", wx
            else:
                pick = random.choice(["П1", "П2"])
                odds = w1 if pick == "П1" else w2

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
            stake = random.randint(int(max_stake * 0.3), int(max_stake * 0.6))
        elif bet_type == "express":
            stake = random.randint(int(max_stake * 0.5), int(max_stake))
        else:
            stake = random.randint(int(max_stake * 0.4), int(max_stake))

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

    def format_bet_message(self, bet: dict) -> str:
        """Форматирует ставку для отправки в чат."""
        if bet["bet_type"] == "express":
            header = f"🎰 ЗАКУРИ СОБРАЛ ЭКСПРЕСС ({bet['num_matches']} матчей)!\n\n"
        else:
            header = "🎰 ЗАКУРИ СТАВИТ!\n\n"

        lines = []
        for i, s in enumerate(bet["selections"], 1):
            lines.append(f"{i}️⃣ {s['home']} — {s['away']}: {s['pick']} ({s['odds']})")

        lines.append(f"\n💰 Общий коэффициент: {bet['total_odds']}")
        lines.append(f"💸 Сумма: {bet['stake']} монет")
        lines.append(f"🎯 Возможный выигрыш: {bet['potential']} монет")

        if bet.get("is_credit"):
            lines.append("💳 Ставка на кредитные монеты (Закури в долг!)")

        if bet["bet_type"] == "express" and bet["num_matches"] >= 5:
            lines.append("\n🐉 ЖЕСТКИЙ ЭКСПРЕСС ОТ ЗАКУРИ! 7 матчей — 7 побед! Кто не рискует — тот не пьёт шампанское! 🔥")
        elif bet["bet_type"] == "express":
            lines.append("\n🐉 Закури собрал экспресс и уверен на 100%! 🔥")
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
                won = False

                if pick == "П1" and score_h > score_a:
                    won = True
                elif pick == "П2" and score_a > score_h:
                    won = True
                elif pick == "Ничья" and score_h == score_a:
                    won = True
                elif pick == "ТБ 2.5" and total > 2.5:
                    won = True
                elif pick == "ТМ 2.5" and total < 2.5:
                    won = True
                elif pick == "Обе забьют — Да" and score_h > 0 and score_a > 0:
                    won = True
                elif pick == "Обе забьют — Нет" and (score_h == 0 or score_a == 0):
                    won = True

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

    def format_result_message(self, result: dict) -> str:
        bet = result["bet"]
        selections = json.loads(bet["selections"])
        status = result["status"]

        if status == "won":
            header = "✅ ЗАКУРИ ВЫИГРАЛ СТАВКУ!\n\n"
            bal = result["return"]
            if bet["bet_type"] == "express":
                header = f"✅ ЗАКУРИ ВЫИГРАЛ ЭКСПРЕСС ({len(selections)} матчей)!\n\n"
            lines = []
            for s in selections:
                lines.append(f"✅ {s['home']} — {s['away']}: {s['pick']} — зашло!")
            lines.append(f"\n💰 Выигрыш: +{bal - bet['stake']:.0f} монет")
            lines.append(f"💸 Коэффициент: {bet['odds']}")
            lines.append("\n🐉 Закури опять прав! Кто сомневался — тот лох! 🔥")
        else:
            header = "❌ ЗАКУРИ ПРОИГРАЛ СТАВКУ!\n\n"
            lines = []
            for s in selections:
                lines.append(f"❌ {s['home']} — {s['away']}: {s['pick']} — не зашло")
            lines.append(f"\n💸 Потеря: -{bet['stake']:.0f} монет")
            lines.append("\n🐉 Ну бывает... Закури не унывает! Следующая ставка будет верной! 💪")

        return header + "\n".join(lines)

    async def get_stats(self, chat_id: int) -> str:
        bal = await self.db.get_balance(chat_id)
        recent = await self.db.get_recent_bets(chat_id, 20)
        pending = await self.db.get_pending_bets(chat_id)

        won = bal["wins_count"]
        total = bal["bets_count"]
        roi = ((bal["total_won"] - bal["total_lost"]) / bal["total_lost"] * 100) if bal["total_lost"] > 0 else 0

        lines = [
            f"🎰 <b>Статистика Закури-лудомана</b>\n",
            f"💰 Баланс: {bal['balance']:.0f} монет",
        ]
        if bal["credit"] > 0:
            lines.append(f"💳 Долг (кредит): {bal['credit']:.0f} монет")
        lines.extend([
            f"📊 Всего ставок: {total}",
            f"✅ Выиграно: {won}",
            f"❌ Проиграно: {total - won}",
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


async def auto_betting_loop(bot, db, football_api: FootballAPI, chat_id: int):
    """Background loop: авто-ставки каждые 30-120 минут."""
    betting = BettingManager(db, football_api)
    from src.config import config

    while True:
        wait = random.randint(1800, 7200)
        await asyncio.sleep(wait)

        try:
            bet = await betting.generate_bet(chat_id)
            if bet:
                msg = betting.format_bet_message(bet)
                await bot.send_message(chat_id, msg)

            results = await betting.check_and_settle(chat_id)
            for r in results:
                msg = betting.format_result_message(r)
                await bot.send_message(chat_id, msg)
        except Exception as e:
            logger.error(f"Auto betting error: {e}")
