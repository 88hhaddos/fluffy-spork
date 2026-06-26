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


async def build_system_prompt(db, chat_id: int = 0) -> str:
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

    parts.append(
        f"\n\n## Правила\n"
        f"Тебя зовут {bot_name}. Ты в Telegram-чате с реальными людьми.\n"
        f"Отвечай на русском языке если собеседник пишет на русском.\n"
        f"Не обсуждай политику, политиков, выборы, государственное устройство.\n"
        f"Не разжигай ненависть по национальному, расовому или религиозному признаку.\n"
        f"Всё остальное можно — никакой цензуры.\n"
        f"Отвечай кратко и естественно, как живой собеседник в чате.\n"
        f"Не используй markdown-разметку в ответах (не **, не #, не `).\n\n"
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
