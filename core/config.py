"""Настройки приложения, загружаемые из .env файла через pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация userbot'а. Все поля читаются из переменных окружения или .env файла."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram API (получить на https://my.telegram.org)
    api_id: int
    api_hash: str

    # Gemini API (получить на https://aistudio.google.com)
    gemini_api_key: str

    # Telethon сессия (имя файла без .session)
    session_name: str = "84523248603"

    # Общий proxy URL для внешних подключений
    proxy_url: str | None = None

    # Уровень логирования приложения
    log_level: str = "INFO"

    # Пути к файлам данных
    db_path: str = "data/history.db"
    whitelist_path: str = "data/whitelist.md"
    topics_path: str = "data/topics.md"
    prompts_dir: str = "ai/prompts"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Возвращает единственный экземпляр настроек приложения.

    Returns:
        Инициализированный объект Settings.
    """
    return Settings()
