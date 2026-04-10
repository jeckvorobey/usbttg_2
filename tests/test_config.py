"""Тесты для модуля настроек приложения."""

import logging
import os
from unittest.mock import patch

import pytest


# Базовый набор обязательных переменных окружения для тестов
BASE_ENV = {
    "API_ID": "12345678",
    "API_HASH": "test_api_hash_abc",
    "GEMINI_API_KEY": "test_gemini_key_xyz",
    "SESSION_STRING": "test-session-string",
}


def test_settings_loads_required_fields():
    """Проверяет, что обязательные поля загружаются из переменных окружения."""
    with patch.dict(os.environ, BASE_ENV, clear=True):
        from core.config import Settings

        s = Settings()
        assert s.api_id == 12345678
        assert s.api_hash == "test_api_hash_abc"
        assert s.gemini_api_key == "test_gemini_key_xyz"


def test_settings_reads_session_string():
    """Проверяет, что строковая сессия загружается из переменной окружения."""
    env = {**BASE_ENV}
    with patch.dict(os.environ, env, clear=True):
        from core.config import Settings

        s = Settings()
        assert s.session_string == "test-session-string"


def test_settings_missing_required_field_raises():
    """Проверяет, что отсутствие обязательного поля вызывает исключение."""
    env_without_api_id = {
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
        "SESSION_STRING": "test-session-string",
    }
    with patch.dict(os.environ, env_without_api_id, clear=True):
        from core.config import Settings

        with pytest.raises(Exception):
            Settings(_env_file=None)


def test_settings_missing_session_string_raises():
    """Проверяет, что отсутствие строковой сессии вызывает исключение."""
    env_without_session_string = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
    }
    with patch.dict(os.environ, env_without_session_string, clear=True):
        from core.config import Settings

        with pytest.raises(Exception):
            Settings(_env_file=None)


def test_settings_rejects_empty_session_string():
    """Проверяет, что пустая строковая сессия отклоняется валидацией."""
    env = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
        "SESSION_STRING": "   ",
    }
    with patch.dict(os.environ, env, clear=True):
        from core.config import Settings

        with pytest.raises(Exception):
            Settings(_env_file=None)


def test_load_settings_or_exit_logs_validation_error(monkeypatch, caplog, tmp_path):
    """Проверяет, что ошибка конфигурации логируется перед остановкой."""
    env_without_session_string = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
    }
    with patch.dict(os.environ, env_without_session_string, clear=True):
        from core.config import get_settings, load_settings_or_exit

        monkeypatch.chdir(tmp_path)
        get_settings.cache_clear()
        with caplog.at_level(logging.CRITICAL):
            with pytest.raises(SystemExit, match="1"):
                load_settings_or_exit()

        messages = [record.getMessage() for record in caplog.records]
        assert any("Ошибка конфигурации окружения" in message for message in messages)


def test_settings_has_db_path():
    """Проверяет наличие поля пути к базе данных."""
    with patch.dict(os.environ, BASE_ENV, clear=True):
        from core.config import Settings

        s = Settings()
        assert s.db_path is not None
        assert len(s.db_path) > 0


def test_get_settings_returns_settings_instance():
    """Проверяет, что публичная фабрика возвращает объект Settings."""
    with patch.dict(os.environ, BASE_ENV, clear=True):
        from core.config import Settings, get_settings

        get_settings.cache_clear()
        settings = get_settings()

        assert isinstance(settings, Settings)
        assert settings.api_id == 12345678


def test_settings_reads_proxy_url():
    """Проверяет загрузку общего proxy URL из переменных окружения."""
    env = {**BASE_ENV, "PROXY_URL": "http://user:pass@127.0.0.1:8080"}
    with patch.dict(os.environ, env, clear=True):
        from core.config import Settings

        s = Settings()

        assert s.proxy_url == "http://user:pass@127.0.0.1:8080"


def test_settings_proxy_url_defaults_to_none():
    """Проверяет, что proxy URL по умолчанию отключён."""
    with patch.dict(os.environ, BASE_ENV, clear=True):
        from core.config import Settings

        s = Settings(_env_file=None)

        assert s.proxy_url is None


def test_settings_log_level_defaults_to_info():
    """Проверяет, что уровень логирования по умолчанию равен INFO."""
    with patch.dict(os.environ, BASE_ENV, clear=True):
        from core.config import Settings

        s = Settings(_env_file=None)

        assert s.log_level == "INFO"


def test_settings_reads_gemini_resilience_options():
    """Проверяет загрузку резервной модели и retry-параметров Gemini из окружения."""
    env = {
        **BASE_ENV,
        "GEMINI_FALLBACK_MODEL": "gemini-2.5-flash-lite",
        "GEMINI_MAX_RETRIES": "4",
        "GEMINI_RETRY_BACKOFF_SECONDS": "2.0",
        "GEMINI_RETRY_JITTER_SECONDS": "0.4",
    }
    with patch.dict(os.environ, env, clear=True):
        from core.config import Settings

        s = Settings(_env_file=None)

        assert s.gemini_fallback_model == "gemini-2.5-flash-lite"
        assert s.gemini_max_retries == 4
        assert s.gemini_retry_backoff_seconds == 2.0
        assert s.gemini_retry_jitter_seconds == 0.4


def test_settings_reads_dnd_hours_utc():
    """Проверяет загрузку UTC-интервала режима не беспокоить."""
    env = {**BASE_ENV, "DND_HOURS_UTC": "23-7"}
    with patch.dict(os.environ, env, clear=True):
        from core.config import Settings

        s = Settings(_env_file=None)

        assert s.dnd_hours_utc == "23-7"


@pytest.mark.parametrize(
    "value",
    ["24-7", "7-24", "aa-bb", "7", "7-", "-7", "7:00-8:00"],
)
def test_settings_rejects_invalid_dnd_hours_utc(value: str):
    """Проверяет валидацию некорректного DND-интервала."""
    env = {**BASE_ENV, "DND_HOURS_UTC": value}
    with patch.dict(os.environ, env, clear=True):
        from core.config import Settings

        with pytest.raises(Exception):
            Settings(_env_file=None)
