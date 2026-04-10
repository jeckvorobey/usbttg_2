"""Тесты скрипта выгрузки информации о текущем Telegram-пользователе."""

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import Settings


class FakeUser:
    """Тестовый объект пользователя с Telethon-подобным интерфейсом."""

    def __init__(self) -> None:
        self.id = 42
        self.first_name = "Иван"
        self.last_name = "Петров"
        self.username = "ivan_petrov"
        self.phone = "79990000000"
        self.bot = False
        self.verified = True
        self.premium = True
        self.deleted = False
        self.scam = False
        self.fake = False

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "first_name": self.first_name,
            "username": self.username,
        }

    def stringify(self) -> str:
        return "User(id=42, first_name='Иван', username='ivan_petrov')"


def test_build_user_info_report_includes_summary_and_serialized_sections():
    """Проверяет наполнение отчёта краткой сводкой и полным дампом."""
    from scripts.get_info import build_user_info_report

    report = build_user_info_report(FakeUser())

    assert "Краткая сводка:" in report
    assert "id: 42" in report
    assert "username: ivan_petrov" in report
    assert "Все доступные публичные атрибуты:" in report
    assert "to_dict():" in report
    assert "stringify():" in report


def test_save_user_info_writes_file(tmp_path):
    """Проверяет сохранение текстового отчёта в файл."""
    from scripts.get_info import save_user_info

    target = tmp_path / "tg_user_info" / "info.txt"
    saved_path = save_user_info("test-report", path=target)

    assert saved_path == target
    assert target.read_text(encoding="utf-8") == "test-report"


@pytest.mark.asyncio
async def test_main_logs_report_and_saves_file(monkeypatch, caplog, tmp_path):
    """Проверяет полный сценарий получения информации о пользователе."""
    from scripts import get_info

    fake_user = FakeUser()
    fake_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        get_current_user=AsyncMock(return_value=fake_user),
    )

    monkeypatch.setattr(
        get_info,
        "load_settings_or_exit",
        lambda: Settings(
            api_id=1,
            api_hash="hash",
            gemini_api_key="gemini-key",
            session_string="session-string",
            proxy_url=None,
        ),
    )
    monkeypatch.setattr(get_info, "UserBotClient", lambda **kwargs: fake_client)
    monkeypatch.setattr(get_info, "INFO_PATH", tmp_path / "tg_user_info" / "info.txt")

    with caplog.at_level(logging.INFO):
        result = await get_info.main()

    assert result == 0
    fake_client.start.assert_awaited_once()
    fake_client.stop.assert_awaited_once()

    saved_text = (tmp_path / "tg_user_info" / "info.txt").read_text(encoding="utf-8")
    assert "Информация о текущем Telegram-пользователе" in saved_text
    assert "id: 42" in saved_text
    assert "stringify():" in saved_text

    messages = [record.getMessage() for record in caplog.records]
    assert any("Полная информация о пользователе:" in message for message in messages)
    assert any("сохранена в" in message for message in messages)
