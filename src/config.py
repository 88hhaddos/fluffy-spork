import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _parse_admin_ids(raw: str) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


@dataclass
class Config:
    BOT_TOKEN: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    ADMIN_IDS: list[int] = field(default_factory=lambda: _parse_admin_ids(os.getenv("ADMIN_IDS", "")))
    DB_PATH: str = field(default_factory=lambda: os.getenv("DB_PATH", "bot.db"))

    DEFAULT_PROVIDER_URL: str = field(default_factory=lambda: os.getenv("DEFAULT_PROVIDER_URL", ""))
    DEFAULT_PROVIDER_KEY: str = field(default_factory=lambda: os.getenv("DEFAULT_PROVIDER_KEY", ""))
    DEFAULT_PROVIDER_MODEL: str = field(default_factory=lambda: os.getenv("DEFAULT_PROVIDER_MODEL", ""))

    DEFAULT_IMAGE_PROVIDER_URL: str = field(default_factory=lambda: os.getenv("DEFAULT_IMAGE_PROVIDER_URL", ""))
    DEFAULT_IMAGE_PROVIDER_KEY: str = field(default_factory=lambda: os.getenv("DEFAULT_IMAGE_PROVIDER_KEY", ""))
    DEFAULT_IMAGE_PROVIDER_MODEL: str = field(default_factory=lambda: os.getenv("DEFAULT_IMAGE_PROVIDER_MODEL", ""))

    BOT_ID: int = 0
    BOT_USERNAME: str = ""

    def validate(self):
        if not self.BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN не установлен. Получите токен у @BotFather.")
        if not self.ADMIN_IDS:
            raise RuntimeError("ADMIN_IDS не установлен. Узнайте свой ID у @userinfobot.")


config = Config()
