"""Настройки приложения: секреты из .env, несекретная конфигурация из TOML."""

from __future__ import annotations

import logging
import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.logging import setup_logging


logger = logging.getLogger(__name__)


def _empty_str_to_none(v: object) -> object:
    """Преобразует пустую строку в None для необязательных полей."""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _require_non_empty_str(v: object) -> object:
    """Отклоняет пустые строки для обязательных текстовых настроек."""
    if not isinstance(v, str):
        return v

    normalized = v.strip()
    if normalized == "":
        raise PydanticCustomError("empty_env_value", "Значение не должно быть пустым")
    return normalized


def _normalize_optional_str(v: object) -> object:
    """Обрезает пробелы и приводит пустую строку к None."""
    v = _empty_str_to_none(v)
    if isinstance(v, str):
        return v.strip()
    return v


def _normalize_optional_chat_id(v: object) -> object:
    """Считает 0 и пустую строку отсутствующим chat_id."""
    v = _empty_str_to_none(v)
    if v == 0 or v == "0":
        return None
    return v


OptionalChatId = Annotated[int | None, BeforeValidator(_normalize_optional_chat_id)]
OptionalStr = Annotated[str | None, BeforeValidator(_normalize_optional_str)]
RequiredStr = Annotated[str, BeforeValidator(_require_non_empty_str)]
MinuteRange = tuple[int, int]


class Secrets(BaseSettings):
    """Секретные настройки, которые остаются в .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_id: int
    api_hash: str
    gemini_api_key: str
    session_string: OptionalStr = None
    proxy_url: OptionalStr = None
    group_chat_id: OptionalChatId = None
    group_target: OptionalStr = None
    settings_path: str = "settings.toml"


class _StrictModel(BaseModel):
    """Базовая модель TOML-секций с запретом неизвестных ключей."""

    model_config = ConfigDict(extra="forbid")


class AppModeConfig(_StrictModel):
    """Секция режима приложения."""

    mode: Literal["swarm"] = "swarm"


class PathsConfig(_StrictModel):
    """Пути к локальным ресурсам проекта, не покрытым профильными секциями."""

    reply_rules_path: str = "ai/prompts/reply_rules.md"


class StorageConfig(_StrictModel):
    """Пути к хранилищу."""

    db_path: str = "data/history.db"


class TargetConfig(_StrictModel):
    """Целевая Telegram-группа для swarm."""

    group_chat_id: int | None = None
    group_target: OptionalStr = None


class PromptsConfig(_StrictModel):
    """Пути к промтам и профилям ботов."""

    base_dir: str = "ai/prompts"
    topics_path: str = "ai/prompts/topics.md"
    bot_profiles_dir: str = "ai/prompts/bots"


class GeminiConfig(_StrictModel):
    """Несекретные параметры Gemini."""

    model: str = "gemini-2.5-flash"
    fallback_model: str | None = "gemini-2.5-flash-lite"
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    max_retries: int = Field(default=3, ge=1)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0)
    retry_jitter_seconds: float = Field(default=0.3, ge=0.0)
    request_timeout_seconds: float = Field(default=45.0, gt=0.0)


class TelegramConfig(_StrictModel):
    """Несекретные параметры Telegram."""

    whitelist_user_ids: list[int] = Field(default_factory=list)


class LoggingConfig(_StrictModel):
    """Параметры логирования."""

    level: str = "INFO"


class SwarmBotConfig(_StrictModel):
    """Конфигурация одного userbot в swarm-режиме."""

    id: str
    session_env: str
    persona_file: str
    enabled: bool = True
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)


class SwarmBotRuntimeConfig(_StrictModel):
    """Развёрнутая runtime-конфигурация userbot с реальной строкой сессии."""

    id: str
    session_env: str
    session_string: str
    persona_file: str
    enabled: bool = True
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)


class SwarmScheduleConfig(_StrictModel):
    """Расписание swarm-обменов."""

    active_windows_utc: list[str] = Field(default_factory=list)
    initiator_offset_minutes: MinuteRange = (0, 30)
    responder_delay_minutes: MinuteRange = (3, 10)
    max_turns_per_exchange: int = Field(default=2, ge=1)
    pair_cooldown_slots: int = Field(default=1, ge=0)

    @field_validator("active_windows_utc")
    @classmethod
    def validate_active_windows_utc(cls, value: list[str]) -> list[str]:
        """Проверяет список UTC-окон в формате HH-HH."""
        validated: list[str] = []
        for item in value:
            normalized = _normalize_optional_str(item)
            if not isinstance(normalized, str):
                raise ValueError("Каждое окно active_windows_utc должно быть строкой")
            parts = normalized.split("-", maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                raise ValueError("active_windows_utc должен содержать окна в формате HH-HH")
            start_hour = int(parts[0])
            end_hour = int(parts[1])
            if not (0 <= start_hour <= 23 and 0 <= end_hour <= 24 and start_hour != end_hour):
                raise ValueError("active_windows_utc должен удовлетворять 0 <= start <= 23, 0 <= end <= 24 и start != end")
            validated.append(f"{start_hour}-{end_hour}")
        return validated

    @field_validator("initiator_offset_minutes", "responder_delay_minutes", mode="before")
    @classmethod
    def validate_minute_range(cls, value: object) -> object:
        """Проверяет диапазон минут [min, max]."""
        start, end = _read_pair(value, "Диапазон минут")
        if start < 0 or end < start:
            raise ValueError("Диапазон минут должен удовлетворять 0 <= min <= max")
        return (start, end)


class SwarmOrchestratorConfig(_StrictModel):
    """Параметры центрального orchestrator."""

    tick_seconds: int = Field(default=30, ge=1)
    silence_timeout_minutes: int = Field(default=60, ge=0)
    skip_if_recent_human_activity: bool = True


class SwarmConfig(_StrictModel):
    """Секция swarm-настроек."""

    enabled: bool = False
    max_parallel_bots: int = Field(default=20, ge=1)
    ignore_messages_from_swarm: bool = True
    reply_only_to_addressed_bot: bool = True
    schedule: SwarmScheduleConfig = Field(default_factory=SwarmScheduleConfig)
    orchestrator: SwarmOrchestratorConfig = Field(default_factory=SwarmOrchestratorConfig)
    bots: list[SwarmBotConfig] = Field(default_factory=list)

    @field_validator("bots")
    @classmethod
    def validate_unique_bot_ids(cls, value: list[SwarmBotConfig]) -> list[SwarmBotConfig]:
        """Проверяет уникальность идентификаторов ботов."""
        seen: set[str] = set()
        for bot in value:
            normalized_bot_id = bot.id.strip().lower()
            if normalized_bot_id in seen:
                raise ValueError(f"duplicate swarm bot id: {bot.id}")
            seen.add(normalized_bot_id)
        return value


class AppConfig(_StrictModel):
    """Полная несекретная TOML-конфигурация."""

    app: AppModeConfig = Field(default_factory=AppModeConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    target: TargetConfig = Field(default_factory=TargetConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    swarm: SwarmConfig = Field(default_factory=SwarmConfig)


def _read_pair(value: object, label: str) -> tuple[int, int]:
    """Читает пару целых значений из list/tuple для TOML-диапазонов."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} должен быть парой значений")
    first, second = value
    if not isinstance(first, int) or not isinstance(second, int):
        raise ValueError(f"{label} должен содержать целые числа")
    return first, second


_UNSET = object()


def _load_toml_config(settings_path: str | Path | None, *, require_exists: bool = False) -> AppConfig:
    """Загружает TOML-конфигурацию или возвращает дефолты, если файл не задан."""
    if settings_path is None:
        return AppConfig()

    path = Path(settings_path)
    if not path.exists():
        if require_exists:
            raise FileNotFoundError(f"Файл настроек не найден: {path}")
        return AppConfig()

    with path.open("rb") as file_obj:
        data = tomllib.load(file_obj)
    return AppConfig.model_validate(data)


class Settings:
    """Фасад конфигурации с прежними публичными именами полей."""

    def __init__(self, _env_file: str | None | object = ".env", **overrides: object) -> None:
        settings_path_override = overrides.pop("settings_path", _UNSET)
        secret_keys = {
            "api_id",
            "api_hash",
            "gemini_api_key",
            "session_string",
            "proxy_url",
            "group_chat_id",
            "group_target",
        }
        required_secret_keys = {"api_id", "api_hash", "gemini_api_key"}
        secret_overrides = {key: overrides.pop(key) for key in list(overrides) if key in secret_keys}

        if required_secret_keys - secret_overrides.keys():
            secrets = Secrets(_env_file=_env_file)
            for key in secret_keys:
                setattr(self, key, secret_overrides.get(key, getattr(secrets, key)))
            if settings_path_override is _UNSET:
                settings_path = getattr(secrets, "settings_path", "settings.toml")
                settings_path_required = "settings_path" in secrets.model_fields_set
            else:
                settings_path = settings_path_override
                settings_path_required = settings_path is not None
        else:
            for key in secret_keys:
                setattr(self, key, secret_overrides.get(key))
            if settings_path_override is _UNSET:
                settings_path = os.environ.get("SETTINGS_PATH")
                settings_path_required = settings_path is not None
            else:
                settings_path = settings_path_override
                settings_path_required = settings_path is not None

        app_config = _load_toml_config(settings_path, require_exists=settings_path_required)
        self.settings_path = str(settings_path or "settings.toml")
        self._apply_app_config(app_config)

        for key, value in overrides.items():
            if not hasattr(self, key):
                raise ValueError(f"Неизвестная настройка: {key}")
            setattr(self, key, value)

    def _apply_app_config(self, config: AppConfig) -> None:
        """Пробрасывает секции TOML в публичные поля Settings."""
        self.mode = config.app.mode

        self.db_path = config.storage.db_path
        self.topics_path = config.prompts.topics_path
        self.reply_rules_path = config.paths.reply_rules_path
        self.prompts_dir = config.prompts.base_dir
        self.bot_profiles_dir = config.prompts.bot_profiles_dir
        if self.group_chat_id is None and config.target.group_chat_id is not None:
            self.group_chat_id = config.target.group_chat_id
        if self.group_target is None and config.target.group_target is not None:
            self.group_target = config.target.group_target

        self.gemini_model = config.gemini.model
        self.gemini_fallback_model = config.gemini.fallback_model
        self.gemini_temperature = config.gemini.temperature
        self.gemini_max_retries = config.gemini.max_retries
        self.gemini_retry_backoff_seconds = config.gemini.retry_backoff_seconds
        self.gemini_retry_jitter_seconds = config.gemini.retry_jitter_seconds
        self.gemini_request_timeout_seconds = config.gemini.request_timeout_seconds

        self.whitelist_user_ids = ",".join(str(user_id) for user_id in config.telegram.whitelist_user_ids)

        self.log_level = config.logging.level

        self.swarm_enabled = config.swarm.enabled or self.mode == "swarm"
        self.swarm_max_parallel_bots = config.swarm.max_parallel_bots
        self.swarm_ignore_messages_from_swarm = config.swarm.ignore_messages_from_swarm
        self.swarm_reply_only_to_addressed_bot = config.swarm.reply_only_to_addressed_bot
        self.swarm_schedule_active_windows_utc = list(config.swarm.schedule.active_windows_utc)
        self.swarm_initiator_offset_minutes = config.swarm.schedule.initiator_offset_minutes
        self.swarm_responder_delay_minutes = config.swarm.schedule.responder_delay_minutes
        self.swarm_max_turns_per_exchange = config.swarm.schedule.max_turns_per_exchange
        self.swarm_pair_cooldown_slots = config.swarm.schedule.pair_cooldown_slots
        self.swarm_tick_seconds = config.swarm.orchestrator.tick_seconds
        self.swarm_silence_timeout_minutes = config.swarm.orchestrator.silence_timeout_minutes
        self.swarm_skip_if_recent_human_activity = config.swarm.orchestrator.skip_if_recent_human_activity
        self.swarm_bots = self._resolve_swarm_bots(config.swarm.bots)
        self.swarm_bot_ids = [bot.id for bot in self.swarm_bots]

        if self.mode == "swarm":
            self.whitelist_user_ids = ""

    def _resolve_swarm_bots(self, bots: list[SwarmBotConfig]) -> list[SwarmBotRuntimeConfig]:
        """Разворачивает session_env каждого swarm-бота в фактическую строку сессии."""
        resolved_bots: list[SwarmBotRuntimeConfig] = []
        for bot in bots:
            session_string = os.environ.get(bot.session_env)
            if session_string is None or session_string.strip() == "":
                raise ValueError(f"Swarm bot session env is missing or empty: {bot.session_env}")
            resolved_bots.append(
                SwarmBotRuntimeConfig.model_validate(
                    {
                        "id": bot.id,
                        "session_env": bot.session_env,
                        "session_string": session_string.strip(),
                        "persona_file": bot.persona_file,
                        "enabled": bot.enabled,
                        "temperature": bot.temperature,
                    },
                )
            )
        return resolved_bots


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
    except (ValidationError, OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        setup_logging(default_log_level)
        logger.critical("Ошибка конфигурации: %s", exc)
        raise SystemExit(1) from exc
