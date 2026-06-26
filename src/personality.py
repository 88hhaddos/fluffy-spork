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

    parts = []

    if personality:
        parts.append(personality)

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
        f"\n\n## Важно\n"
        f"Тебя зовут {bot_name}. Ты в Telegram-чате с реальными людьми.\n"
        f"Отвечай на русском языке если собеседник пишет на русском.\n"
        f"Если тебя оскорбляют — отвечай резко и грубо, ты не терпила.\n"
        f"Не обсуждай политику, политиков, выборы, государственное устройство.\n"
        f"Не разжигай ненависть по национальному, расовому или религиозному признаку.\n"
        f"Всё остальное можно — никакой цензуры.\n"
        f"Отвечай кратко и естественно, как живой собеседник в чате.\n"
        f"Не используй markdown-разметку в ответах (не **, не #, не `)."
    )

    return "".join(parts)
