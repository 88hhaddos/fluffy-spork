"""Чтение новостей из Telegram каналов через AI."""
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)


async def fetch_tg_channel_posts(channel_username: str, limit: int = 10) -> list[str]:
    """Получает последние посты из публичного TG канала через t.me/rss.
    
    Возвращает список текстов постов.
    """
    channel = channel_username.replace("@", "").replace("https://t.me/", "").replace("t.me/", "")
    
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://rsshub.app/telegram/channel/{channel}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text()
                
                import re
                items = re.findall(r"<description><!\[CDATA\[(.*?)\]\]></description>", text, re.DOTALL)
                posts = []
                for item in items[:limit]:
                    clean = re.sub(r"<[^>]+>", "", item).strip()
                    if clean and len(clean) > 20:
                        posts.append(clean[:500])
                return posts
    except Exception as e:
        logger.debug(f"TG channel fetch error: {e}")
        return []


async def get_football_news(channel: str = "footballearn") -> list[str]:
    """Получает футбольные новости из TG канала."""
    return await fetch_tg_channel_posts(channel, limit=10)


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
