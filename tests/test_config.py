"""Тесты для модуля настроек приложения."""

import os
from unittest.mock import patch

import pytest


# Базовый набор обязательных переменных окружения для тестов
BASE_ENV = {
    "API_ID": "12345678",
    "API_HASH": "test_api_hash_abc",
    "GEMINI_API_KEY": "test_gemini_key_xyz",
}


def test_settings_loads_required_fields():
    """Проверяет, что обязательные поля загружаются из переменных окружения."""
    with patch.dict(os.environ, BASE_ENV, clear=True):
        from core.config import Settings

        s = Settings()
        assert s.api_id == 12345678
        assert s.api_hash == "test_api_hash_abc"
        assert s.gemini_api_key == "test_gemini_key_xyz"


def test_settings_default_session_name():
    """Проверяет, что имя сессии по умолчанию равно '84523248603'."""
    with patch.dict(os.environ, BASE_ENV, clear=True):
        from core.config import Settings

        s = Settings()
        assert s.session_name == "84523248603"


def test_settings_override_session_name():
    """Проверяет, что имя сессии можно переопределить через переменную окружения."""
    env = {**BASE_ENV, "SESSION_NAME": "другая_сессия"}
    with patch.dict(os.environ, env, clear=True):
        from core.config import Settings

        s = Settings()
        assert s.session_name == "другая_сессия"


def test_settings_missing_required_field_raises():
    """Проверяет, что отсутствие обязательного поля вызывает исключение."""
    env_without_api_id = {
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
    }
    with patch.dict(os.environ, env_without_api_id, clear=True):
        from core.config import Settings

        with pytest.raises(Exception):
            Settings(_env_file=None)


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
