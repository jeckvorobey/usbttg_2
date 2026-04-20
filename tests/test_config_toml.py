"""Тесты TOML-конфигурации приложения."""

from pathlib import Path
from unittest.mock import patch

import pytest

from core.config import Settings


BASE_SECRETS = {
    "api_id": 12345678,
    "api_hash": "test_api_hash_abc",
    "gemini_api_key": "test_gemini_key_xyz",
    "session_string": "test-session-string",
    "group_chat_id": -100123,
    "group_target": "@group",
}


def write_settings(tmp_path: Path, content: str) -> Path:
    """Создаёт временный settings.toml для теста."""
    path = tmp_path / "settings.toml"
    path.write_text(content.strip(), encoding="utf-8")
    return path


def test_settings_loads_non_secret_values_from_toml(tmp_path):
    """Проверяет загрузку несекретных параметров из TOML."""
    settings_path = write_settings(
        tmp_path,
        """
        [mode]
        active = "windowed_qa"

        [bot]
        role = "responder"

        [paths]
        db_path = ":memory:"
        topics_path = "custom/topics.md"
        reply_rules_path = "custom/rules.md"
        prompts_dir = "custom/prompts"

        [gemini]
        model = "gemini-test"
        fallback_model = "gemini-lite"
        temperature = 1.2
        max_retries = 4
        retry_backoff_seconds = 2.0
        retry_jitter_seconds = 0.4
        request_timeout_seconds = 12.5

        [telegram]
        whitelist_user_ids = [111, 222]

        [logging]
        level = "DEBUG"

        [legacy_session]
        scheduler_enabled = false
        silence_check_interval_minutes = 7
        silence_timeout_minutes = 70
        session_duration_minutes = 25
        dnd_hours_utc = "23-7"

        [windowed_qa]
        morning_window_utc = [9, 10]
        evening_window_utc = [17, 19]
        initiator_offset_minutes = [3, 5]
        responder_delay_minutes = [9, 11]
        max_exchanges_per_window = 1

        [reply_guard]
        enabled = true
        city = "Нячанг"
        refusal_text = "Отказ"
        classifier_model = "gemini-classifier"
        classifier_temperature = 0.1
        max_input_chars = 400
        worker_poll_interval_seconds = 0.2
        max_attempts = 2
        retry_backoff_seconds = [1, 3]
        system_prompt_path = "custom/reply_guard/system.md"
        classifier_prompt_path = "custom/reply_guard/classifier.md"
        """,
    )

    settings = Settings(**BASE_SECRETS, settings_path=str(settings_path))

    assert settings.mode == "windowed_qa"
    assert settings.bot_role == "responder"
    assert settings.db_path == ":memory:"
    assert settings.topics_path == "custom/topics.md"
    assert settings.reply_rules_path == "custom/rules.md"
    assert settings.prompts_dir == "custom/prompts"
    assert settings.gemini_model == "gemini-test"
    assert settings.gemini_fallback_model == "gemini-lite"
    assert settings.gemini_temperature == 1.2
    assert settings.gemini_max_retries == 4
    assert settings.group_chat_id == -100123
    assert settings.group_target == "@group"
    assert settings.whitelist_user_ids == "111,222"
    assert settings.log_level == "DEBUG"
    assert settings.scheduler_enabled is False
    assert settings.silence_check_interval_minutes == 7
    assert settings.dnd_hours_utc == "23-7"
    assert settings.window_morning_utc == (9, 10)
    assert settings.window_evening_utc == (17, 19)
    assert settings.initiator_offset_minutes == (3, 5)
    assert settings.responder_delay_minutes == (9, 11)
    assert settings.reply_guard_enabled is True
    assert settings.reply_guard_refusal_text == "Отказ"
    assert settings.reply_guard_classifier_model == "gemini-classifier"
    assert settings.reply_guard_max_input_chars == 400
    assert settings.reply_guard_system_prompt_path == "custom/reply_guard/system.md"


def test_settings_with_secret_overrides_ignores_local_toml_without_settings_path(tmp_path, monkeypatch):
    """Проверяет изоляцию тестовых overrides от локального settings.toml."""
    write_settings(
        tmp_path,
        """
        [mode]
        active = "windowed_qa"
        """,
    )
    monkeypatch.chdir(tmp_path)

    settings = Settings(**BASE_SECRETS)

    assert settings.mode == "legacy_session"


@pytest.mark.parametrize(
    "window_value",
    ["[10, 10]", "[-1, 10]", "[10, 25]"],
)
def test_settings_rejects_invalid_window_ranges(tmp_path, window_value: str):
    """Проверяет валидацию некорректных UTC-окон."""
    settings_path = write_settings(
        tmp_path,
        f"""
        [windowed_qa]
        morning_window_utc = {window_value}
        """,
    )

    with pytest.raises(Exception):
        Settings(**BASE_SECRETS, settings_path=str(settings_path))


def test_settings_accepts_midnight_crossing_window(tmp_path):
    """Проверяет поддержку UTC-окна, которое пересекает полночь."""
    settings_path = write_settings(
        tmp_path,
        """
        [windowed_qa]
        evening_window_utc = [23, 3]
        """,
    )

    settings = Settings(**BASE_SECRETS, settings_path=str(settings_path))

    assert settings.window_evening_utc == (23, 3)


def test_settings_accepts_24_as_window_end(tmp_path):
    """Проверяет поддержку 24 как конца UTC-суток."""
    settings_path = write_settings(
        tmp_path,
        """
        [windowed_qa]
        evening_window_utc = [23, 24]
        """,
    )

    settings = Settings(**BASE_SECRETS, settings_path=str(settings_path))

    assert settings.window_evening_utc == (23, 24)


@pytest.mark.parametrize("value", ["[-1, 5]", "[5, 4]"])
def test_settings_rejects_invalid_minute_ranges(tmp_path, value: str):
    """Проверяет валидацию некорректных диапазонов минут."""
    settings_path = write_settings(
        tmp_path,
        f"""
        [windowed_qa]
        responder_delay_minutes = {value}
        """,
    )

    with pytest.raises(Exception):
        Settings(**BASE_SECRETS, settings_path=str(settings_path))


def test_settings_path_can_come_from_env(tmp_path):
    """Проверяет, что SETTINGS_PATH выбирает TOML-файл."""
    settings_path = write_settings(
        tmp_path,
        """
        [mode]
        active = "windowed_qa"
        """,
    )
    env = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
        "SESSION_STRING": "test-session-string",
        "SETTINGS_PATH": str(settings_path),
    }

    with patch.dict("os.environ", env, clear=True):
        settings = Settings(_env_file=None)

    assert settings.mode == "windowed_qa"


def test_settings_rejects_group_target_in_toml(tmp_path):
    """Проверяет, что Telegram-цель больше не читается из TOML."""
    settings_path = write_settings(
        tmp_path,
        """
        [telegram]
        group_chat_id = -100123
        group_target = "@group"
        """,
    )

    with pytest.raises(Exception):
        Settings(**BASE_SECRETS, settings_path=str(settings_path))


def test_settings_reads_target_section_from_toml(tmp_path):
    """Проверяет чтение целевой группы из новой секции [target]."""
    settings_path = write_settings(
        tmp_path,
        """
        [app]
        mode = "swarm"

        [target]
        group_chat_id = -100987654321
        group_target = "@swarm_group"

        [[swarm.bots]]
        id = "anna"
        session_env = "SESSION_STRING_ANNA"
        persona_file = "anna.md"

        [[swarm.bots]]
        id = "mike"
        session_env = "SESSION_STRING_MIKE"
        persona_file = "mike.md"
        """,
    )

    env = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
        "SESSION_STRING": "legacy-session",
        "SESSION_STRING_ANNA": "anna-session",
        "SESSION_STRING_MIKE": "mike-session",
        "SETTINGS_PATH": str(settings_path),
    }

    with patch.dict("os.environ", env, clear=True):
        settings = Settings(_env_file=None)

    assert settings.group_chat_id == -100987654321
    assert settings.group_target == "@swarm_group"


def test_settings_rejects_missing_explicit_settings_path(tmp_path):
    """Проверяет ошибку при отсутствующем явно переданном TOML-файле."""
    missing_path = tmp_path / "missing-settings.toml"

    with pytest.raises(FileNotFoundError, match="Файл настроек не найден"):
        Settings(**BASE_SECRETS, settings_path=str(missing_path))


def test_settings_rejects_missing_settings_path_from_env(tmp_path):
    """Проверяет ошибку при отсутствующем SETTINGS_PATH из окружения."""
    missing_path = tmp_path / "missing-settings.toml"
    env = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
        "SESSION_STRING": "test-session-string",
        "SETTINGS_PATH": str(missing_path),
    }

    with patch.dict("os.environ", env, clear=True):
        with pytest.raises(FileNotFoundError, match="Файл настроек не найден"):
            Settings(_env_file=None)


def test_settings_rejects_missing_settings_path_from_env_file(tmp_path):
    """Проверяет ошибку при отсутствующем SETTINGS_PATH из .env."""
    missing_path = tmp_path / "missing-settings.toml"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "API_ID=12345678",
                "API_HASH=test_hash",
                "GEMINI_API_KEY=test_key",
                "SESSION_STRING=test-session-string",
                f"SETTINGS_PATH={missing_path}",
            ],
        ),
        encoding="utf-8",
    )

    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(FileNotFoundError, match="Файл настроек не найден"):
            Settings(_env_file=str(env_path))


def test_settings_loads_swarm_mode_and_bots(tmp_path):
    """Проверяет загрузку swarm-режима и списка ботов из TOML."""
    settings_path = write_settings(
        tmp_path,
        """
        [app]
        mode = "swarm"

        [storage]
        db_path = ":memory:"

        [prompts]
        base_dir = "custom/prompts"
        topics_path = "custom/topics.md"
        bot_profiles_dir = "custom/bots"

        [swarm]
        enabled = true
        max_parallel_bots = 12
        ignore_messages_from_swarm = true
        reply_only_to_addressed_bot = true

        [swarm.schedule]
        active_windows_utc = ["10-11", "16-18"]
        initiator_offset_minutes = [0, 30]
        responder_delay_minutes = [3, 10]
        max_turns_per_exchange = 2
        pair_cooldown_slots = 1

        [swarm.orchestrator]
        tick_seconds = 30
        silence_timeout_minutes = 60
        skip_if_recent_human_activity = true

        [[swarm.bots]]
        id = "anna"
        session_env = "SESSION_STRING_ANNA"
        persona_file = "anna.md"
        enabled = true
        temperature = 0.9

        [[swarm.bots]]
        id = "mike"
        session_env = "SESSION_STRING_MIKE"
        persona_file = "mike.md"
        enabled = false
        temperature = 0.8
        """,
    )

    env = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
        "SESSION_STRING": "legacy-session",
        "SESSION_STRING_ANNA": "anna-session",
        "SESSION_STRING_MIKE": "mike-session",
        "SETTINGS_PATH": str(settings_path),
    }

    with patch.dict("os.environ", env, clear=True):
        settings = Settings(_env_file=None)

    assert settings.mode == "swarm"
    assert settings.db_path == ":memory:"
    assert settings.prompts_dir == "custom/prompts"
    assert settings.topics_path == "custom/topics.md"
    assert settings.swarm_enabled is True
    assert settings.swarm_max_parallel_bots == 12
    assert settings.swarm_ignore_messages_from_swarm is True
    assert settings.swarm_reply_only_to_addressed_bot is True
    assert settings.swarm_schedule_active_windows_utc == ["10-11", "16-18"]
    assert settings.swarm_initiator_offset_minutes == (0, 30)
    assert settings.swarm_responder_delay_minutes == (3, 10)
    assert settings.swarm_max_turns_per_exchange == 2
    assert settings.swarm_pair_cooldown_slots == 1
    assert settings.swarm_tick_seconds == 30
    assert settings.swarm_silence_timeout_minutes == 60
    assert settings.swarm_skip_if_recent_human_activity is True
    assert settings.swarm_bot_ids == ["anna", "mike"]
    assert settings.swarm_bots[0].session_string == "anna-session"
    assert settings.swarm_bots[1].session_string == "mike-session"
    assert settings.whitelist_user_ids == ""


def test_settings_rejects_duplicate_swarm_bot_ids(tmp_path):
    """Проверяет запрет дублирующихся bot.id в swarm-конфигурации."""
    settings_path = write_settings(
        tmp_path,
        """
        [app]
        mode = "swarm"

        [[swarm.bots]]
        id = "anna"
        session_env = "SESSION_STRING_ANNA"
        persona_file = "anna.md"

        [[swarm.bots]]
        id = "anna"
        session_env = "SESSION_STRING_ANNA_2"
        persona_file = "anna-2.md"
        """,
    )

    env = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
        "SESSION_STRING": "legacy-session",
        "SESSION_STRING_ANNA": "anna-session",
        "SESSION_STRING_ANNA_2": "anna-session-2",
        "SETTINGS_PATH": str(settings_path),
    }

    with patch.dict("os.environ", env, clear=True):
        with pytest.raises(Exception, match="bot id"):
            Settings(_env_file=None)


def test_settings_rejects_missing_swarm_session_env(tmp_path):
    """Проверяет ошибку, если session_env бота не найден в окружении."""
    settings_path = write_settings(
        tmp_path,
        """
        [app]
        mode = "swarm"

        [[swarm.bots]]
        id = "anna"
        session_env = "SESSION_STRING_ANNA"
        persona_file = "anna.md"
        """,
    )

    env = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
        "SESSION_STRING": "legacy-session",
        "SETTINGS_PATH": str(settings_path),
    }

    with patch.dict("os.environ", env, clear=True):
        with pytest.raises(Exception, match="SESSION_STRING_ANNA"):
            Settings(_env_file=None)


def test_settings_ignores_whitelist_in_swarm_mode(tmp_path):
    """Проверяет, что whitelist_user_ids отключается в swarm-режиме."""
    settings_path = write_settings(
        tmp_path,
        """
        [app]
        mode = "swarm"

        [telegram]
        whitelist_user_ids = [111, 222]

        [[swarm.bots]]
        id = "anna"
        session_env = "SESSION_STRING_ANNA"
        persona_file = "anna.md"
        """,
    )

    env = {
        "API_ID": "12345678",
        "API_HASH": "test_hash",
        "GEMINI_API_KEY": "test_key",
        "SESSION_STRING": "legacy-session",
        "SESSION_STRING_ANNA": "anna-session",
        "SETTINGS_PATH": str(settings_path),
    }

    with patch.dict("os.environ", env, clear=True):
        settings = Settings(_env_file=None)

    assert settings.mode == "swarm"
    assert settings.whitelist_user_ids == ""
