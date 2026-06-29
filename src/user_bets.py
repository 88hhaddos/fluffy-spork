"""Ставки юзеров через бота — бот ставит за юзера, запоминает, тегает при проигрыше."""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Распознавание запросов на ставку ──

BET_REQUEST_PATTERNS = [
    # "закури поставь 700 на Португалию"
    re.compile(r'(?:поставь|ставь|заложи|кинь)\s+(\d+)\s+(?:юаней|рубл|монет)?\s*(?:на\s+)?(.+)', re.I),
    # "закури поставь 700 на П1 Аргентина Франция"
    re.compile(r'(?:поставь|ставь)\s+(\d+)\s+на\s+(П1|П2|Х|ничью|тотал|ТБ|ТМ|обе\s+забьют)', re.I),
    # "поставь на победу Аргентины 500"
    re.compile(r'(?:поставь|ставь)\s+на\s+(?:победу\s+)?(.+?)\s+(\d+)\s*(?:юаней|рубл|монет)?$', re.I),
]

BET_TYPES = {
    "п1": ("home", "П1"),
    "п2": ("away", "П2"),
    "х": ("draw", "Ничья"),
    "ничья": ("draw", "Ничья"),
    "ничью": ("draw", "Ничья"),
    "тб": ("over25", "ТБ 2.5"),
    "тм": ("under25", "ТМ 2.5"),
    "обе забьют": ("btts_yes", "Обе забьют"),
    "обе забьют да": ("btts_yes", "Обе забьют — Да"),
    "обе забьют нет": ("btts_no", "Обе забьют — Нет"),
}


def parse_bet_request(text: str) -> Optional[dict]:
    """Парсит запрос юзера на ставку.
    Возвращает {amount, team, bet_type} или None.
    """
    text_lower = text.lower().strip()

    for pattern in BET_REQUEST_PATTERNS:
        m = pattern.search(text_lower)
        if not m:
            continue

        groups = m.groups()

        # Pattern 1: "поставь 700 на Португалию"
        if len(groups) == 2 and groups[0].isdigit():
            amount = int(groups[0])
            target = groups[1].strip()

            # Проверяем bet_type
            for key, (bet_code, bet_label) in BET_TYPES.items():
                if key in target:
                    team = target.replace(key, "").replace("на", "").strip()
                    return {
                        "amount": amount,
                        "team": team if team else None,
                        "bet_type": bet_code,
                        "bet_label": bet_label,
                    }

            # Просто команда
            return {
                "amount": amount,
                "team": target,
                "bet_type": "home",  # default
                "bet_label": "П1",
            }

        # Pattern 2: "поставь 700 на П1 Аргентина"
        if len(groups) == 2 and groups[0].isdigit():
            amount = int(groups[0])
            bet_key = groups[1].strip().lower()
            for key, (bet_code, bet_label) in BET_TYPES.items():
                if key in bet_key:
                    return {
                        "amount": amount,
                        "team": None,
                        "bet_type": bet_code,
                        "bet_label": bet_label,
                    }

        # Pattern 3: "поставь на победу Аргентины 500"
        if len(groups) == 2 and groups[1].isdigit():
            amount = int(groups[1])
            target = groups[0].strip().replace("победу ", "").replace("победа ", "")
            return {
                "amount": amount,
                "team": target,
                "bet_type": "home",
                "bet_label": "П1",
            }

    return None


async def find_match_for_team(team_name: str, football_api) -> Optional[dict]:
    """Находит предстоящий матч для команды."""
    matches = await football_api.get_pari_upcoming_matches(limit=100)

    team_lower = team_name.lower().strip()

    for m in matches:
        home = (m.get("homeTeam") or {}).get("name", "").lower()
        away = (m.get("awayTeam") or {}).get("name", "").lower()

        is_home = team_lower in home
        is_away = team_lower in away

        if is_home or is_away:
            w1 = m.get("winner1", 0)
            wx = m.get("winnerX", 0)
            w2 = m.get("winner2", 0)
            if not (w1 and wx and w2):
                continue

            return {
                "match_id": str(m.get("id", "")),
                "home": (m.get("homeTeam") or {}).get("name", "?"),
                "away": (m.get("awayTeam") or {}).get("name", "?"),
                "w1": w1,
                "wx": wx,
                "w2": w2,
                "over25": m.get("over25", 0),
                "under25": m.get("under25", 0),
                "btts_yes": m.get("btts_yes", 0),
                "btts_no": m.get("btts_no", 0),
                "team_side": "home" if is_home else "away",
            }

    return None


async def handle_user_bet_request(message, text: str, db, football_api, bot) -> bool:
    """Обрабатывает запрос юзера на ставку через бота.
    Возвращает True если это был запрос на ставку (даже если не получилось).
    """
    result = parse_bet_request(text)
    if not result:
        return False

    amount = result["amount"]
    team = result["team"]
    bet_type = result["bet_type"]
    bet_label = result["bet_label"]

    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Кто-то"
    chat_id = message.chat.id

    if amount < 10:
        await message.reply("Минимум 10 юаней на ставку, дружище!")
        return True

    if amount > 100000:
        await message.reply("Максимум 100 000 юаней за раз! Закури не банк!")
        return True

    status_msg = await message.reply(f"🎰 Закури ищет матч для твоей ставки...")

    try:
        # Если указана команда — ищем матч
        match = None
        if team:
            match = await find_match_for_team(team, football_api)

        if not match and team:
            await status_msg.edit_text(
                f"Закури не нашёл матч с «{team}» в ближайших играх 🤷\n"
                f"Попробуй /matches чтобы посмотреть какие матчи есть!"
            )
            return True

        if not match:
            await status_msg.edit_text(
                "Закури не понял на какой матч ставить! Напиши так:\n"
                "«закури поставь 700 на Аргентину»\n"
                "«закури поставь 500 на П1 Аргентина»"
            )
            return True

        # Определяем коэффициент
        odds_map = {
            "home": match["w1"],
            "away": match["w2"],
            "draw": match["wx"],
            "over25": match.get("over25", 0),
            "under25": match.get("under25", 0),
            "btts_yes": match.get("btts_yes", 0),
            "btts_no": match.get("btts_no", 0),
        }

        # Если юзер сказал "на Аргентину" — ставим на победу Аргентины
        if team and bet_type == "home":
            if match["team_side"] == "away":
                bet_type = "away"
                bet_label = "П2"

        odds = odds_map.get(bet_type, 0)
        if not odds or odds < 1.01:
            await status_msg.edit_text("Закури не нашёл коэффициент на эту ставку 😔")
            return True

        potential = round(amount * odds, 2)
        match_info = f"{match['home']} — {match['away']}"

        # Сохраняем ставку
        bet_id = await db.place_user_bet(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            match_id=match["match_id"],
            match_info=match_info,
            bet_on=f"{bet_label} ({match_info})",
            amount=amount,
            odds=odds,
        )

        bet_type_display = bet_label
        if bet_type == "home":
            bet_type_display = f"Победа {match['home']} (П1)"
        elif bet_type == "away":
            bet_type_display = f"Победа {match['away']} (П2)"

        await status_msg.edit_text(
            f"✅ Закури принял ставку!\n\n"
            f"👤 Юзер: {username}\n"
            f"⚽ Матч: {match_info}\n"
            f"🎯 Ставка: {bet_type_display}\n"
            f"💰 Коэффициент: {odds}\n"
            f"💸 Сумма: {amount} юаней\n"
            f"🎯 Возможный выигрыш: {potential:.0f} юаней\n\n"
            f"🐉 Закури поставил за тебя! Если выиграем — ты красавчик, если проиграем — возвращай деньги! 🔥"
        )

        return True

    except Exception as e:
        logger.error(f"User bet error: {e}", exc_info=True)
        try:
            await status_msg.edit_text("Закури запутался в ставке 😔 Попробуй ещё раз!")
        except Exception:
            pass
        return True


async def check_and_settle_user_bets(db, football_api, bot, chat_id: int):
    """Проверяет pending ставки юзеров и закрывает их.
    При проигрыше — тегает юзера.
    """
    pending = await db.get_pending_user_bets(chat_id)
    results = []

    for bet in pending:
        match_id = bet["match_id"]
        if not match_id:
            continue

        try:
            details = await football_api.get_pari_match_details(int(match_id))
            if not details:
                continue

            status = details.get("status", "")
            score_h = details.get("homeResult", 0) or 0
            score_a = details.get("awayResult", 0) or 0

            if status not in ("Finished", "Ended", "AfterPenalties", "AfterExtraTime"):
                continue

            bet_on = bet["bet_on"].lower()
            won = False

            if "п1" in bet_on and score_h > score_a:
                won = True
            elif "п2" in bet_on and score_a > score_h:
                won = True
            elif "ничь" in bet_on and score_h == score_a:
                won = True
            elif "тб" in bet_on and (score_h + score_a) > 2.5:
                won = True
            elif "тм" in bet_on and (score_h + score_a) < 2.5:
                won = True
            elif "обе забьют — да" in bet_on and score_h > 0 and score_a > 0:
                won = True
            elif "обе забьют — нет" in bet_on and (score_h == 0 or score_a == 0):
                won = True

            username = bet.get("username", "Кто-то")
            match_info = bet.get("match_info", "?")
            amount = bet.get("amount", 0)
            odds = bet.get("odds", 0)
            potential = bet.get("potential_return", 0)

            if won:
                await db.settle_user_bet(bet["id"], "won")
                results.append({
                    "type": "win",
                    "username": username,
                    "match": match_info,
                    "amount": amount,
                    "odds": odds,
                    "winnings": potential,
                    "user_id": bet["user_id"],
                })
            else:
                await db.settle_user_bet(bet["id"], "lost")
                results.append({
                    "type": "loss",
                    "username": username,
                    "match": match_info,
                    "amount": amount,
                    "odds": odds,
                    "user_id": bet["user_id"],
                })

        except Exception as e:
            logger.error(f"Settle user bet error: {e}")

    # Отправляем результаты
    for r in results:
        username = r["username"]
        user_id = r["user_id"]

        if r["type"] == "win":
            profit = r["winnings"] - r["amount"]
            msg = (
                f"🎉 ЗАКУРИ ПОЗДОРОВЛЯЕТ!\n\n"
                f"👤 @{username or user_id}\n"
                f"⚽ {r['match']}\n"
                f"🎯 Ставка зашла! Выигрыш: +{profit:.0f} юаней\n"
                f"💰 Коэффициент: {r['odds']}\n\n"
                f"🐉 Красавчик! Закури рад за тебя! Заходи ещё, поставим! 🔥"
            )
        else:
            LOSS_MESSAGES = [
                f"😈 @{username or user_id}! Ставка проиграла! {r['amount']:.0f} юаней канули в лету! "
                f"Закури предупреждал — драконье чутьё не подводит, но форс-мажоры бывают! "
                f"Долг растёт, дружище, возвращай! 🔥",

                f"🐉 @{username or user_id}, та-ак, и где твои {r['amount']:.0f} юаней? "
                f"Проиграли! Закури ставил от чистого сердца, а ты... Ну ничего, "
                f"Закури добрый, подождёт. Но не обманывай дракона! 💰",

                f"😤 @{username or user_id}, ставка не зашла! {r['match']} подвёл! "
                f"Закури расстроен, хвост дёргается от злости! "
                f"{r['amount']:.0f} юаней долга — не забудь! 🔥",

                f"🔥 @{username or user_id}, слушай сюда! Проиграли мы, {r['amount']:.0f} юаней коту под хвост! "
                f"Закури не злой, но память у дракона как у слона! Верни, а то дыхну! 🐉",
            ]
            import random
            msg = random.choice(LOSS_MESSAGES)

        try:
            await bot.send_message(chat_id, msg)
        except Exception as e:
            logger.error(f"Send bet result error: {e}")

    return results
