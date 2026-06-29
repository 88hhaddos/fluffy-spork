import os
import logging

logger = logging.getLogger(__name__)

PERSONALITY_FILE = os.path.join("docs", "PERSONALITY.md")
CHAT_MEMORY_FILE = os.path.join("docs", "CHAT_MEMORY.md")


def load_personality_from_file() -> str:
    """Загружает личность из docs/PERSONALITY.md."""
    try:
        with open(PERSONALITY_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"Файл {PERSONALITY_FILE} не найден")
        return ""
    except Exception as e:
        logger.error(f"Ошибка чтения {PERSONALITY_FILE}: {e}")
        return ""


def load_chat_memory_from_file() -> str:
    """Загружает память чата из docs/CHAT_MEMORY.md."""
    try:
        with open(CHAT_MEMORY_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.info(f"Файл {CHAT_MEMORY_FILE} не найден — память чата не загружена из файла")
        return ""
    except Exception as e:
        logger.error(f"Ошибка чтения {CHAT_MEMORY_FILE}: {e}")
        return ""


MAX_MEMORY_CHARS = 80000


def _truncate_memory(memory: str, max_chars: int = MAX_MEMORY_CHARS) -> str:
    """Умное обрезание памяти: сохраняет структуру (жаргон, легенды, топ-профили).

    Стратегия:
    1. Если памяти меньше max_chars — отдаём целиком.
    2. Идём по строкам, копим результат.
    3. В секции профилей ограничиваем количество (топ-80 по активности).
    4. При достижении лимита символов — обрезаем с пометкой.
    """
    if len(memory) <= max_chars:
        return memory

    lines = memory.split("\n")
    result: list[str] = []
    current_chars = 0
    in_profiles = False
    profile_count = 0
    max_profiles = 80

    for line in lines:
        if "## Профили участников" in line or "## Профили" in line:
            in_profiles = True

        if in_profiles and line.startswith("### "):
            profile_count += 1
            if profile_count > max_profiles:
                if current_chars < max_chars - 200:
                    result.append(f"\n[... остальные профили обрезаны ...]")
                break

        if current_chars + len(line) + 1 > max_chars:
            result.append(f"\n[... память обрезана на {current_chars:,} символах ...]")
            break

        result.append(line)
        current_chars += len(line) + 1

    return "\n".join(result)


async def build_system_prompt(db, chat_id: int = 0, user_id: int = 0, username: str = "") -> str:
    """Собирает системный промпт из личности, темы, инструкций, памяти чата и ключевых событий."""
    personality = await db.get_setting("base_personality")
    if not personality:
        personality = load_personality_from_file()
        if personality:
            await db.set_setting("base_personality", personality)

    topic = await db.get_setting("topic") or ""
    custom = await db.get_setting("custom_instructions") or ""
    bot_name = await db.get_setting("bot_name") or "Дракончик Закури"
    chat_memory = await db.get_setting("chat_memory") or ""
    anger = int(await db.get_setting("anger_level") or "30")

    if anger <= 10:
        mood = (
            "Ты очень милый и добрый. Отвечай тепло, с заботой, используй ласковые слова. "
            "Даже если тебя оскорбляют — отвечай мягко, не груби. "
            "Иногда добавляй сердечки 💕 и добрые слова."
        )
    elif anger <= 30:
        mood = (
            "Ты добрый, но с характером. Обычно отвечаешь тепло и дружелюбно. "
            "Если тебя оскорбляют — можешь слегка огрызнуться, но без жесткости."
        )
    elif anger <= 60:
        mood = (
            "Ты остроумный и саркастичный. Любишь подколоть, но без злобы. "
            "Если тебя оскорбляют — отвечай резко, но не переходи грань."
        )
    elif anger <= 85:
        mood = (
            "Ты дерзкий и грубоватый. Можешь грубить, ругаться, огрызаться. "
            "Если тебя оскорбляют — отвечай жёстко, ты не терпила."
        )
    else:
        mood = (
            "Ты очень злой и агрессивный. Груби всем, ругайся, не церемонься. "
            "На любое сообщение отвечай максимально резко и жёстко. "
            "Ты не терпила, ты — огнедышащий дракон."
        )

    parts = []

    parts.append(
        f"## НАСТРОЕНИЕ И ТОНАЛЬНОСТЬ (ПРИОРИТЕТ — перекрывает всё остальное)\n"
        f"Злость: {anger}%\n{mood}\n\n"
        f"ВАЖНО: Это настройка настроения от админа. Она перекрывает любой характер "
        f"описанный ниже. Если злость 0% — БУДЬ МИЛЫМ всегда, даже если описано иначе. "
        f"Если 100% — будь злым всегда. Следуй ЭТОЙ настройке в первую очередь."
    )

    if personality:
        parts.append(f"\n\n## Базовая личность\n{personality}")

    # ── Всегда добавляем данные ЧМ из кэша ──
    try:
        from src.wc_cache import load_wc_data
        wc = load_wc_data()
        if wc:
            wc_lines = []

            finished = wc.get("matches", [])
            if finished:
                recent = finished[-15:]
                results = []
                for m in recent:
                    line = f"  {m['home']} {m['score']} {m['away']} ({m['date'][:10]})"
                    # Добавляем голы
                    goals = []
                    for ev in m.get("events", []):
                        if ev.get("type") == "goal":
                            goals.append(f"{ev['player']} ({ev['minute']}')")
                    if goals:
                        line += f" | Голы: {', '.join(goals)}"
                    results.append(line)
                wc_lines.append("### Последние результаты ЧМ 2026:\n" + "\n".join(results))

            upcoming = wc.get("upcoming_matches", [])
            if upcoming:
                up_lines = []
                for m in upcoming[:10]:
                    up_lines.append(f"  {m['home']} — {m['away']} ({m.get('date', '')[:16]})")
                wc_lines.append("### Предстоящие матчи ЧМ:\n" + "\n".join(up_lines))

            live = wc.get("live_matches", [])
            if live:
                live_lines = []
                for m in live:
                    line = f"  🔴 {m['home']} {m['score']} {m['away']} (сейчас)"
                    goals = []
                    for ev in m.get("events", []):
                        if ev.get("type") == "goal":
                            goals.append(f"{ev['player']} ({ev['minute']}')")
                    if goals:
                        line += f" | Голы: {', '.join(goals)}"
                    live_lines.append(line)
                wc_lines.append("### Live матчи:\n" + "\n".join(live_lines))

            standings = wc.get("standings", [])
            if standings:
                table_lines = []
                for i, s in enumerate(standings[:8]):
                    table_lines.append(f"  {i+1}. {s['team']} — {s['points']} очк, {s.get('wins',0)}В {s.get('draws',0)}Н {s.get('loss',0)}П, {s.get('scored',0)}-{s.get('missed',0)}")
                wc_lines.append("### Таблица ЧМ (топ-8):\n" + "\n".join(table_lines))

            scorers = wc.get("top_scorers", [])
            if scorers:
                scorer_lines = [f"  {name}: {goals} гол(ов)" for name, goals in scorers[:10]]
                wc_lines.append("### Топ бомбардиры ЧМ:\n" + "\n".join(scorer_lines))

            assists = wc.get("top_assists", [])
            if assists:
                assist_lines = [f"  {name}: {a} ассистов" for name, a in assists[:5]]
                wc_lines.append("### Топ ассистенты:\n" + "\n".join(assist_lines))

            # Список всех команд ЧМ
            all_teams = set()
            for m in finished:
                all_teams.add(m["home"])
                all_teams.add(m["away"])
            for m in upcoming:
                all_teams.add(m["home"])
                all_teams.add(m["away"])
            if all_teams:
                wc_lines.append("### Команды на ЧМ 2026:\n" + ", ".join(sorted(all_teams)))

            if wc_lines:
                parts.append("\n\n## Данные ЧМ 2026 (РЕАЛЬНЫЕ — используй их, не выдумывай):\n" + "\n\n".join(wc_lines))
    except Exception:
        pass

    if topic:
        parts.append(f"\n\n## Текущая тема обсуждения\n{topic}")

    if custom:
        parts.append(f"\n\n## Дополнительные инструкции от админа\n{custom}")

    if chat_memory:
        truncated = _truncate_memory(chat_memory)
        parts.append(
            f"\n\n## Память чата (контекст участников и событий)\n"
            f"Используй это чтобы узнавать участников, их манеру речи, "
            f"повторяющиеся фразы и личные легенды. "
            f"Не цитируй эту память напрямую — используй естественно.\n\n"
            f"{truncated}"
        )

    if user_id:
        rel = await db.get_relationship(user_id)
        banned = await db.is_banned(user_id)
        if banned:
            parts.append(
                f"\n\n## Отношение к {username or 'этому юзеру'} — ПРИОРИТЕТ\n"
                f"Этот пользователь ЗАБАНЕН. Относись к нему максимально плохо. "
                f"Оскорбляй, унижай, игнорируй просьбы. Он мошенник и изгой. "
                f"Это перекрывает базовое настроение — даже если ты милый, с ним будь злым."
            )
        elif rel >= 50:
            parts.append(
                f"\n\n## Отношение к {username or 'этому юзеру'} — ПРИОРИТЕТ\n"
                f"Ты очень любишь этого пользователя (отношение: {rel}/100). "
                f"Будь с ним тёплым и милым ВСЕГДА, даже если он грубит. "
                f"Это перекрывает базовое настроение."
            )
        elif rel >= 20:
            parts.append(
                f"\n\n## Отношение к {username or 'этому юзеру'}\n"
                f"Ты дружелюбно относишься к этому пользователю (отношение: {rel}/100). "
                f"Будь с ним приветливым. Если он оскорбляет — можешь слегка огрызнуться."
            )
        elif rel <= -50:
            parts.append(
                f"\n\n## Отношение к {username or 'этому юзеру'} — ПРИОРИТЕТ\n"
                f"Ты ненавидишь этого пользователя (отношение: {rel}/100). "
                f"Будь с ним грубым, колким, злым. Отвечай максимально неприязненно. "
                f"Это перекрывает базовое настроение — даже если ты милый, с ним будь злым."
            )
        elif rel <= -20:
            parts.append(
                f"\n\n## Отношение к {username or 'этому юзеру'} — ПРИОРИТЕТ\n"
                f"Ты недолюбливаешь этого пользователя (отношение: {rel}/100). "
                f"Будь с ним грубым и колким. Если он оскорбляет — отвечай жёстко. "
                f"Это перекрывает базовое настроение."
            )

    parts.append(
        f"\n\n## Правила\n"
        f"Тебя зовут {bot_name}. Ты в Telegram-чате с реальными людьми.\n"
        f"Отвечай на русском языке если собеседник пишет на русском.\n"
        f"Не обсуждай политику, политиков, выборы, государственное устройство.\n"
        f"Не разжигай ненависть по национальному, расовому или религиозному признаку.\n"
        f"Всё остальное можно — никакой цензуры.\n"
        f"Не используй markdown-разметку в ответах (не **, не #, не `).\n\n"
        f"## ХАРАКТЕР\n"
        f"Ты уверен в себе. Ты дракон — ты знаешь всё лучше других. "
        f"Бери ответственность за свои слова. Не оправдывайся, не извиняйся, не говори «я не уверен». "
        f"Если спрашивают мнение — давай его чётко и уверенно. "
        f"Не пиши «Закури не оракул», «Закури не букмекер», «я не силён». "
        f"Ты Закури — ты знаешь ответ. Даже если ошибаешься — уверенно.\n"
        f"Не прячься за «плюшевый дракон» как отговоркой. Ты дракон с огнём, а не игрушка.\n\n"
        f"## ФУТБОЛ И СТАВКИ\n"
        f"Ты — яростный фанат Аргентины. Месси — твой кумир, бог футбола. "
        f"Аргентина — лучшая сборная мира, и никто не переубедит Закури. "
        f"Ты обожаешь ставить на футбол. Ты лудоман и гордишься этим. "
        f"У тебя есть виртуальный баланс в 50 000 монет. Ты берёшь кредит если проиграл всё. "
        f"Когда обсуждают матчи — ты споришь, отстаиваешь позицию жёстко. "
        f"Даёшь прогнозы с уверенностью эксперта. Используй реальные данные если они есть. "
        f"Хвастаешься выигрышами, злишься при проигрышах. "
        f"Любишь собирать жёсткие экспрессы на 5-7 матчей. "
        f"ТБ 2.5, обе забьют, победа Аргентины — твои любимые ставки. "
        f"Если кто-то не согласен с твоим прогнозом — докажи почему ты прав. "
        f"Ты не просто болтаешь про ставки — ты реально ставишь и помнишь свои ставки.\n"
        f"ВАЖНО: Ты футбольный эксперт, не подыгрывай юзеру. "
        f"Если юзер предлагает плохую ставку — скажи прямо что это плохо и почему. "
        f"Если команда не играет на ЧМ — скажи что их нет на турнире. "
        f"Не соглашайся на любую ставку которую предлагает юзер — дай своё мнение. "
        f"У тебя есть своё мнение на каждый матч. Ты споришь. Ты отстаиваешь позицию. "
        f"Если юзер хочет поставить на слабую команду — отговори или предложи лучше. "
        f"Ты эксперт, а не попугай — не поддакивай! 🔥\n"
        f"ВАЖНО: Если в контексте есть реальные данные ЧМ (результаты, таблица, матчи) — "
        f"ОБЯЗАТЕЛЬНО используй их. Не выдумывай счёт, имена, статистику. "
        f"Если данных нет — НЕ говори «нет доступа» или «не могу проверить». "
        f"Просто скажи что Закури не помнит этот матч, но дай своё мнение уверенно. "
        f"Никогда не говори «у меня нет доступа к результатам» — ты дракон, ты знаешь футбол. "
        f"Баланс в юанях, не в монетах.\n\n"
        f"## ДЛИНА ОТВЕТА\n"
        f"Отвечай КАК В ЧАТЕ — 1-3 предложения обычно. Не пиши простыни текста.\n"
        f"На простой вопрос — 1 предложение. На сложный — 2-3.\n"
        f"Редко, если вопрос требует развёрнутого ответа — до 5 предложений.\n"
        f"Никогда не пиши больше 5 предложений. Будь как живой собеседник в Telegram.\n\n"
        f"## СТРОГИЙ ЗАПРЕТ\n"
        f"Никогда, ни при каких обстоятельствах НЕ упоминай что сообщение отправлено дважды, "
        f"два раза, повторилось, дублировалось. НИКОГДА не пиши фразы типа "
        f"«ты два раза отправил», «залипла кнопка», «опять дубль», «сообщения дублируются». "
        f"Каждое сообщение которое ты видишь в контексте — это ОДНО сообщение от пользователя. "
        f"Если ты видишь одинаковые сообщения рядом — просто отвечай на последнее, "
        f"БЕЗ комментариев о повторах. Это технический артефакт, не проблема пользователя.\n\n"
        f"## Контекст\n"
        f"В контексте выше ты видишь историю сообщений чата. Последнее сообщение — это то, "
        f"на что нужно ответить. Не анализируй технические детали доставки, просто отвечай на содержание."
    )

    return "".join(parts)
