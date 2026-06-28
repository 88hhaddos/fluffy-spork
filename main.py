import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from src.config import config
from src.db_backend import IS_POSTGRES
from src.database import Database
from src.ai_providers import AIProviderManager
from src.context_manager import ContextManager
from src.personality import load_personality_from_file, load_chat_memory_from_file
from src.football_api import FootballAPI, set_api_key as set_football_key
from src.handlers.admin_panel import router as admin_router
from src.handlers.photo_gen import router as photo_router
from src.handlers.group_chat import router as group_router
from src.handlers.features import router as features_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("zakuri")


async def init_default_providers(db: Database):
    """Добавляет провайдеры из .env если они заданы и ещё не в БД."""
    existing = await db.get_providers()

    if config.DEFAULT_PROVIDER_URL and config.DEFAULT_PROVIDER_KEY and config.DEFAULT_PROVIDER_MODEL:
        already = any(
            p["base_url"] == config.DEFAULT_PROVIDER_URL
            and p["model"] == config.DEFAULT_PROVIDER_MODEL
            for p in existing
        )
        if not already:
            await db.add_provider(
                name="Default (env)",
                base_url=config.DEFAULT_PROVIDER_URL,
                api_key=config.DEFAULT_PROVIDER_KEY,
                model=config.DEFAULT_PROVIDER_MODEL,
                provider_type="text",
                priority=0,
            )
            logger.info("Добавлен text-провайдер из .env")

    if (
        config.DEFAULT_IMAGE_PROVIDER_URL
        and config.DEFAULT_IMAGE_PROVIDER_KEY
        and config.DEFAULT_IMAGE_PROVIDER_MODEL
    ):
        already_img = any(
            p["base_url"] == config.DEFAULT_IMAGE_PROVIDER_URL
            and p["model"] == config.DEFAULT_IMAGE_PROVIDER_MODEL
            for p in existing
        )
        if not already_img:
            await db.add_provider(
                name="Default Image (env)",
                base_url=config.DEFAULT_IMAGE_PROVIDER_URL,
                api_key=config.DEFAULT_IMAGE_PROVIDER_KEY,
                model=config.DEFAULT_IMAGE_PROVIDER_MODEL,
                provider_type="image",
                priority=0,
            )
            logger.info("Добавлен image-провайдер из .env")


async def init_personality(db: Database):
    """Загружает личность из PERSONALITY.md если в БД пусто."""
    current = await db.get_setting("base_personality")
    if not current:
        personality = load_personality_from_file()
        if personality:
            await db.set_setting("base_personality", personality)
            logger.info("Личность загружена из docs/PERSONALITY.md")


async def init_chat_memory(db: Database):
    """Загружает память чата из CHAT_MEMORY.md.
    Перезаписывает БД при каждом старте — файл главный источник."""
    memory = load_chat_memory_from_file()
    if memory:
        await db.set_setting("chat_memory", memory)
        logger.info(f"Память чата загружена из docs/CHAT_MEMORY.md ({len(memory):,} символов)")
    else:
        existing = await db.get_setting("chat_memory")
        if existing:
            logger.info(f"Память чата: используется из БД ({len(existing):,} символов)")


async def main():
    config.validate()

    db = Database()
    await db.init()

    backend = "PostgreSQL" if IS_POSTGRES else "SQLite"
    logger.info(f"База данных: {backend}")

    await init_default_providers(db)
    await init_personality(db)
    await init_chat_memory(db)

    sstats_key = os.getenv("SSTATS_API_KEY", "sjzgn3bbco67pk8j")
    set_football_key(sstats_key)
    football_api = FootballAPI(sstats_key)
    logger.info(f"Football API: SStats.net (key: {sstats_key[:8]}...)")

    ai_manager = AIProviderManager(db)
    context_manager = ContextManager(db, ai_manager)

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        request_timeout=300,
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.workflow_data["db"] = db
    dp.workflow_data["ai_manager"] = ai_manager
    dp.workflow_data["context_manager"] = context_manager
    dp.workflow_data["football_api"] = football_api

    dp.include_router(admin_router)
    dp.include_router(features_router)
    dp.include_router(photo_router)
    dp.include_router(group_router)

    me = await bot.get_me()
    config.BOT_ID = me.id
    config.BOT_USERNAME = me.username or ""
    bot_name = await db.get_setting("bot_name") or "Дракончик Закури"

    logger.info(
        f"Бот запущен: @{config.BOT_USERNAME} (ID: {config.BOT_ID}) — {bot_name}"
    )

    betting_enabled = os.getenv("BETTING_ENABLED", "0") == "1"
    betting_chat_id = int(os.getenv("BETTING_CHAT_ID", "0") or "0")

    if not betting_enabled:
        betting_enabled = (await db.get_setting("betting_enabled") or "0") == "1"
    if not betting_chat_id:
        betting_chat_id = int(await db.get_setting("betting_chat_id") or "0")

    if betting_enabled and betting_chat_id:
        from src.betting import auto_betting_loop
        asyncio.create_task(auto_betting_loop(bot, db, football_api, betting_chat_id, ai_manager))
        logger.info(f"Авто-ставки включены для chat {betting_chat_id}")
    elif betting_enabled:
        logger.info("Авто-ставки включены но chat_id не задан — задайте через /admin → 🎰")

    try:
        await dp.start_polling(bot)
    finally:
        await ai_manager.close()
        await football_api.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
