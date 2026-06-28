"""Чтение новостей из Telegram каналов + веб-поиск футбольных фактов."""
import logging
import aiohttp
import re
from typing import Optional

logger = logging.getLogger(__name__)

CHANNELS = [
    "footballearn",
    "sportsru",
    "championat",
    "football_news",
]


async def fetch_tg_channel_posts(channel_username: str, limit: int = 10) -> list[str]:
    """Получает последние посты из публичного TG канала."""
    channel = channel_username.replace("@", "").replace("https://t.me/", "").replace("t.me/", "")

    # Способ 1: RSSHub
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://rsshub.app/telegram/channel/{channel}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    items = re.findall(r"<description><!\[CDATA\[(.*?)\]\]></description>", text, re.DOTALL)
                    posts = []
                    for item in items[:limit]:
                        clean = re.sub(r"<[^>]+>", "", item).strip()
                        if clean and len(clean) > 20:
                            posts.append(clean[:800])
                    if posts:
                        return posts
    except Exception as e:
        logger.debug(f"RSSHub fetch error for {channel}: {e}")

    # Способ 2: Прямой парсинг t.me
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://t.me/s/{channel}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    # Парсим тексты постов
                    posts = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
                    result = []
                    for post in posts[:limit]:
                        clean = re.sub(r"<[^>]+>", "", post).strip()
                        clean = clean.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
                        if clean and len(clean) > 20:
                            result.append(clean[:800])
                    if result:
                        return result
    except Exception as e:
        logger.debug(f"t.me/s fetch error for {channel}: {e}")

    return []


async def get_football_news(channel: str = "footballearn") -> list[str]:
    """Получает футбольные новости из TG каналов."""
    all_posts = []

    # Основной канал
    posts = await fetch_tg_channel_posts(channel, limit=10)
    if posts:
        all_posts.extend(posts)

    # Дополнительные каналы если основной пуст
    if not all_posts:
        for ch in CHANNELS:
            if ch == channel:
                continue
            posts = await fetch_tg_channel_posts(ch, limit=5)
            if posts:
                all_posts.extend(posts)
                break

    return all_posts[:15]


async def get_news_summary(posts: list[str], ai_manager) -> Optional[str]:
    """Суммаризует новости через AI."""
    if not posts:
        return None

    try:
        combined = "\n---\n".join(posts[:5])
        response = await ai_manager.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты дракон Закури — футбольный эксперт. "
                        "Кратко расскажи главные новости из этих постов. "
                        "2-3 предложения. С характером, с мнением. "
                        "На русском."
                    ),
                },
                {"role": "user", "content": f"Новости:\n{combined}"},
            ],
            temperature=0.7,
            max_tokens=200,
        )
        return response.strip() if response else None
    except Exception as e:
        logger.error(f"News summary error: {e}")
        return None


async def search_football_facts(query: str, ai_manager) -> Optional[str]:
    """Ищет футбольные факты через AI (новые открытия ЧМ, герои, статистика)."""
    try:
        response = await ai_manager.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты дракон Закури — футбольный эксперт. "
                        "Отвечай на вопрос о ЧМ 2026 используя свои знания. "
                        "Если не знаешь точно — скажи что не помнишь, но дай мнение. "
                        "Кратко, 1-3 предложения. С характером."
                    ),
                },
                {"role": "user", "content": query},
            ],
            temperature=0.6,
            max_tokens=200,
        )
        return response.strip() if response else None
    except Exception as e:
        logger.error(f"Fact search error: {e}")
        return None


async def get_all_football_info(ai_manager) -> str:
    """Собирает все футбольные новости и факты для контекста."""
    parts = []

    # Новости из TG
    posts = await get_football_news("footballearn")
    if posts:
        news_text = "\n".join(posts[:3])
        parts.append(f"### Последние новости футбола (из TG):\n{news_text[:1500]}")

    # Факты от AI
    facts_query = "Назови главных героев и открытия ЧМ 2026: новые звёзды, сюрпризы, лучшие матчи. Кратко."
    facts = await search_football_facts(facts_query, ai_manager)
    if facts:
        parts.append(f"### Герои и открытия ЧМ 2026:\n{facts}")

    if parts:
        return "\n\n".join(parts)
    return ""
