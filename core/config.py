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
HourWindow = tuple[int, int]
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
    session_string: RequiredStr
    proxy_url: OptionalStr = None
    group_chat_id: OptionalChatId = None
    group_target: OptionalStr = None
    settings_path: str = "settings.toml"


class _StrictModel(BaseModel):
    """Базовая модель TOML-секций с запретом неизвестных ключей."""

    model_config = ConfigDict(extra="forbid")


class ModeConfig(_StrictModel):
    """Выбранный режим работы инстанса."""

    active: Literal["legacy_session", "windowed_qa"] = "legacy_session"


class BotConfig(_StrictModel):
    """Роль инстанса в windowed_qa."""

    role: Literal["initiator", "responder"] = "initiator"


class PathsConfig(_StrictModel):
    """Пути к локальным ресурсам проекта."""

    db_path: str = "data/history.db"
    topics_path: str = "ai/prompts/topics.md"
    reply_rules_path: str = "ai/prompts/reply_rules.md"
    prompts_dir: str = "ai/prompts"


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


class LegacySessionConfig(_StrictModel):
    """Параметры старого режима 30-минутных сессий."""

    scheduler_enabled: bool = True
    silence_check_interval_minutes: int = Field(default=5, ge=1)
    silence_timeout_minutes: int = Field(default=60, ge=0)
    session_duration_minutes: int = Field(default=30, ge=1)
    dnd_hours_utc: OptionalStr = None

    @field_validator("dnd_hours_utc", mode="before")
    @classmethod
    def validate_dnd_hours_utc(cls, value: object) -> object:
        """Проверяет формат UTC-интервала режима не беспокоить."""
        value = _normalize_optional_str(value)
        if value is None:
            return None
        if not isinstance(value, str):
            return value

        parts = value.split("-", maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise PydanticCustomError(
                "invalid_dnd_hours_utc",
                "dnd_hours_utc должен быть в формате HH-HH",
            )

        start_hour = int(parts[0])
        end_hour = int(parts[1])
        if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
            raise PydanticCustomError(
                "invalid_dnd_hours_utc",
                "Часы в dnd_hours_utc должны быть в диапазоне 0..23",
            )

        return f"{start_hour}-{end_hour}"


class WindowedQAConfig(_StrictModel):
    """Параметры режима одного вопроса и одного ответа в UTC-окнах."""

    morning_window_utc: HourWindow = (10, 11)
    evening_window_utc: HourWindow = (16, 18)
    initiator_offset_minutes: MinuteRange = (0, 30)
    responder_delay_minutes: MinuteRange = (8, 12)
    max_exchanges_per_window: int = Field(default=1, ge=1)

    @field_validator("morning_window_utc", "evening_window_utc", mode="before")
    @classmethod
    def validate_hour_window(cls, value: object) -> object:
        """Проверяет пару часов UTC, включая окна через полночь."""
        start, end = _read_pair(value, "Окно UTC")
        if not (0 <= start <= 23 and 0 <= end <= 24 and start != end):
            raise ValueError("Окно UTC должно удовлетворять 0 <= start <= 23, 0 <= end <= 24 и start != end")
        return (start, end)

    @field_validator("initiator_offset_minutes", "responder_delay_minutes", mode="before")
    @classmethod
    def validate_minute_range(cls, value: object) -> object:
        """Проверяет диапазон минут [min, max]."""
        start, end = _read_pair(value, "Диапазон минут")
        if start < 0 or end < start:
            raise ValueError("Диапазон минут должен удовлетворять 0 <= min <= max")
        return (start, end)


class ReplyGuardConfig(_StrictModel):
    """Параметры изолированного reply_guard."""

    enabled: bool = False
    city: str = "Нячанг"
    refusal_text: str = "Кажется, это чуть не по теме. Уточните, пожалуйста, вопрос про Нячанг."
    classifier_model: str = "gemini-3-flash-preview"
    classifier_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_input_chars: int = Field(default=500, ge=1)
    worker_poll_interval_seconds: float = Field(default=0.5, gt=0.0)
    max_attempts: int = Field(default=3, ge=1)
    retry_backoff_seconds: list[float] = Field(default_factory=lambda: [2.0, 8.0, 30.0])
    system_prompt_path: str = "ai/prompts/reply_guard/system.md"
    classifier_prompt_path: str = "ai/prompts/reply_guard/classifier.md"


class AppConfig(_StrictModel):
    """Полная несекретная TOML-конфигурация."""

    mode: ModeConfig = Field(default_factory=ModeConfig)
    bot: BotConfig = Field(default_factory=BotConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    legacy_session: LegacySessionConfig = Field(default_factory=LegacySessionConfig)
    windowed_qa: WindowedQAConfig = Field(default_factory=WindowedQAConfig)
    reply_guard: ReplyGuardConfig = Field(default_factory=ReplyGuardConfig)


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
        required_secret_keys = {"api_id", "api_hash", "gemini_api_key", "session_string"}
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
        """Пробрасывает секции TOML в совместимые публичные поля Settings."""
        self.mode = config.mode.active
        self.bot_role = config.bot.role

        self.db_path = config.paths.db_path
        self.topics_path = config.paths.topics_path
        self.reply_rules_path = config.paths.reply_rules_path
        self.prompts_dir = config.paths.prompts_dir

        self.gemini_model = config.gemini.model
        self.gemini_fallback_model = config.gemini.fallback_model
        self.gemini_temperature = config.gemini.temperature
        self.gemini_max_retries = config.gemini.max_retries
        self.gemini_retry_backoff_seconds = config.gemini.retry_backoff_seconds
        self.gemini_retry_jitter_seconds = config.gemini.retry_jitter_seconds
        self.gemini_request_timeout_seconds = config.gemini.request_timeout_seconds

        self.whitelist_user_ids = ",".join(str(user_id) for user_id in config.telegram.whitelist_user_ids)

        self.log_level = config.logging.level

        self.scheduler_enabled = config.legacy_session.scheduler_enabled
        self.silence_check_interval_minutes = config.legacy_session.silence_check_interval_minutes
        self.silence_timeout_minutes = config.legacy_session.silence_timeout_minutes
        self.session_duration_minutes = config.legacy_session.session_duration_minutes
        self.dnd_hours_utc = config.legacy_session.dnd_hours_utc

        self.window_morning_utc = config.windowed_qa.morning_window_utc
        self.window_evening_utc = config.windowed_qa.evening_window_utc
        self.initiator_offset_minutes = config.windowed_qa.initiator_offset_minutes
        self.responder_delay_minutes = config.windowed_qa.responder_delay_minutes
        self.max_exchanges_per_window = config.windowed_qa.max_exchanges_per_window

        self.reply_guard_enabled = config.reply_guard.enabled
        self.reply_guard_city = config.reply_guard.city
        self.reply_guard_refusal_text = config.reply_guard.refusal_text
        self.reply_guard_classifier_model = config.reply_guard.classifier_model
        self.reply_guard_classifier_temperature = config.reply_guard.classifier_temperature
        self.reply_guard_max_input_chars = config.reply_guard.max_input_chars
        self.reply_guard_worker_poll_interval_seconds = config.reply_guard.worker_poll_interval_seconds
        self.reply_guard_max_attempts = config.reply_guard.max_attempts
        self.reply_guard_retry_backoff_seconds = config.reply_guard.retry_backoff_seconds
        self.reply_guard_system_prompt_path = config.reply_guard.system_prompt_path
        self.reply_guard_classifier_prompt_path = config.reply_guard.classifier_prompt_path


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
