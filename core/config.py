"""Настройки приложения, загружаемые из .env файла через pydantic-settings."""

import logging
from functools import lru_cache
from typing import Annotated

from pydantic import BeforeValidator
from pydantic_core import PydanticCustomError
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.logging import setup_logging


logger = logging.getLogger(__name__)


def _empty_str_to_none(v: object) -> object:
    """Преобразует пустую строку в None — для необязательных числовых полей."""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _require_non_empty_str(v: object) -> object:
    """Отклоняет пустые строки для обязательных текстовых настроек."""
    if not isinstance(v, str):
        return v

    normalized = v.strip()
    if normalized == "":
        raise PydanticCustomError(
            "empty_env_value",
            "Значение не должно быть пустым",
        )
    return normalized


OptionalInt = Annotated[int | None, BeforeValidator(_empty_str_to_none)]
RequiredStr = Annotated[str, BeforeValidator(_require_non_empty_str)]


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
    gemini_model: str = "gemini-2.5-flash"
    gemini_fallback_model: str | None = "gemini-2.5-flash-lite"
    gemini_max_retries: int = 3
    gemini_retry_backoff_seconds: float = 1.0
    gemini_retry_jitter_seconds: float = 0.3

    # Telethon строковая сессия
    session_string: RequiredStr

    # Общий proxy URL для внешних подключений
    proxy_url: str | None = None

    # Уровень логирования приложения
    log_level: str = "INFO"

    # Пути к файлам данных
    db_path: str = "data/history.db"
    whitelist_path: str = "data/whitelist.md"
    topics_path: str = "data/topics.md"
    prompts_dir: str = "ai/prompts"

    # Планировщик разговоров
    scheduler_enabled: bool = True
    silence_timeout_minutes: int = 60
    group_chat_id: OptionalInt = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Возвращает единственный экземпляр настроек приложения.

    Returns:
        Инициализированный объект Settings.
    """
    return Settings()


def load_settings_or_exit(default_log_level: str = "INFO") -> Settings:
    """Загружает настройки и завершает приложение при ошибке конфигурации."""
    try:
        return get_settings()
    except ValidationError as exc:
        setup_logging(default_log_level)
        logger.critical("Ошибка конфигурации окружения: %s", exc)
        raise SystemExit(1) from exc
