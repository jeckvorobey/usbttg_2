"""Тесты интерактивного скрипта обновления профиля Telegram."""

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import Settings
from userbot.client import UserBotClient


class FakeTelegramClient:
    """Простая подмена Telethon-клиента для операций профиля."""

    def __init__(self) -> None:
        self.start = AsyncMock()
        self.disconnect = AsyncMock()
        self.get_me = AsyncMock()
        self.upload_file = AsyncMock(return_value="uploaded-file")
        self.is_connected = lambda: True
        self.calls: list[object] = []

    async def __call__(self, request: object) -> None:
        self.calls.append(request)


def test_ask_yes_no_retries_on_invalid_answer():
    """Проверяет повторный запрос при неверном ответе да/нет."""
    from scripts.update_profile import ask_yes_no

    answers = iter(["может быть", "да"])
    asked: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        return next(answers)

    assert ask_yes_no("Обновить имя?", input_func=fake_input) is True
    assert asked == ["Обновить имя? (да/нет): ", "Обновить имя? (да/нет): "]


def test_ask_non_empty_value_retries_on_blank():
    """Проверяет повторный запрос непустого значения."""
    from scripts.update_profile import ask_non_empty_value

    answers = iter(["   ", "Иван"])

    assert ask_non_empty_value("Введите имя", input_func=lambda _: next(answers)) == "Иван"


def test_ask_avatar_path_retries_until_existing_file(tmp_path):
    """Проверяет повторный запрос пути к аватарке, пока файл не станет валидным."""
    from scripts.update_profile import ask_avatar_path

    avatar = tmp_path / "avatar.jpg"
    avatar.write_bytes(b"file")
    answers = iter([str(tmp_path / "missing.jpg"), str(avatar)])

    result = ask_avatar_path(input_func=lambda _: next(answers))

    assert result == avatar


def test_collect_profile_changes_supports_partial_update(tmp_path):
    """Проверяет сбор только выбранных пользователем изменений."""
    from scripts.update_profile import collect_profile_changes

    avatar = tmp_path / "avatar.jpg"
    avatar.write_bytes(b"file")
    answers = iter(["да", "Иван", "нет", "да", "@ivan_petrov", "да", str(avatar)])

    result = collect_profile_changes(input_func=lambda _: next(answers))

    assert result.first_name == "Иван"
    assert result.last_name is None
    assert result.username == "ivan_petrov"
    assert result.avatar_path == avatar


@pytest.mark.asyncio
async def test_userbot_client_update_profile_requests(monkeypatch):
    """Проверяет отправку запроса на обновление имени и фамилии."""
    fake_client = FakeTelegramClient()

    class FakeUpdateProfileRequest:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(
        "userbot.client._build_telegram_client",
        lambda session_name, api_id, api_hash, proxy=None: fake_client,
    )
    monkeypatch.setattr(
        "userbot.client._import_telethon_profile_requests",
        lambda: SimpleNamespace(
            UpdateProfileRequest=FakeUpdateProfileRequest,
            UpdateUsernameRequest=object,
            UploadProfilePhotoRequest=object,
        ),
    )

    client = UserBotClient(session_name="session", api_id=1, api_hash="hash")
    await client.start()
    await client.update_profile(first_name="Иван", last_name="Петров")

    assert fake_client.calls[0].kwargs == {"first_name": "Иван", "last_name": "Петров"}


@pytest.mark.asyncio
async def test_userbot_client_update_avatar_uploads_file(monkeypatch, tmp_path):
    """Проверяет загрузку файла и отправку запроса на обновление аватарки."""
    fake_client = FakeTelegramClient()
    avatar = tmp_path / "avatar.jpg"
    avatar.write_bytes(b"file")

    class FakeUploadProfilePhotoRequest:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(
        "userbot.client._build_telegram_client",
        lambda session_name, api_id, api_hash, proxy=None: fake_client,
    )
    monkeypatch.setattr(
        "userbot.client._import_telethon_profile_requests",
        lambda: SimpleNamespace(
            UpdateProfileRequest=object,
            UpdateUsernameRequest=object,
            UploadProfilePhotoRequest=FakeUploadProfilePhotoRequest,
        ),
    )

    client = UserBotClient(session_name="session", api_id=1, api_hash="hash")
    await client.start()
    await client.update_avatar(avatar)

    fake_client.upload_file.assert_awaited_once_with(str(avatar))
    assert fake_client.calls[0].kwargs == {"file": "uploaded-file"}


@pytest.mark.asyncio
async def test_main_reports_partial_failure_and_logs_final_user(monkeypatch, caplog, tmp_path):
    """Проверяет частичный успех и итоговый лог с данными текущего пользователя."""
    from scripts import update_profile

    avatar = tmp_path / "avatar.jpg"
    avatar.write_bytes(b"file")
    fake_user = SimpleNamespace(
        id=42,
        first_name="Иван",
        last_name="Петров",
        username="ivan_petrov",
        phone="79990000000",
        photo=object(),
    )
    fake_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        update_profile=AsyncMock(),
        update_username=AsyncMock(side_effect=RuntimeError("username занят")),
        update_avatar=AsyncMock(),
        get_current_user=AsyncMock(return_value=fake_user),
    )

    monkeypatch.setattr(
        update_profile,
        "get_settings",
        lambda: Settings(
            api_id=1,
            api_hash="hash",
            gemini_api_key="gemini-key",
            session_name="84523248603",
            proxy_url=None,
        ),
    )
    monkeypatch.setattr(
        update_profile,
        "collect_profile_changes",
        lambda input_func=input: update_profile.ProfileChanges(
            first_name="Иван",
            last_name=None,
            username="new_username",
            avatar_path=avatar,
        ),
    )
    monkeypatch.setattr(update_profile, "UserBotClient", lambda **kwargs: fake_client)

    with caplog.at_level(logging.INFO):
        result = await update_profile.main()

    assert result == 1
    fake_client.start.assert_awaited_once()
    fake_client.stop.assert_awaited_once()
    fake_client.update_profile.assert_awaited_once_with(first_name="Иван", last_name=None)
    fake_client.update_avatar.assert_awaited_once_with(avatar)
    messages = [record.getMessage() for record in caplog.records]
    assert any("username: ошибка" in message for message in messages)
    assert any("id=42" in message for message in messages)
    assert any("has_photo=True" in message for message in messages)


@pytest.mark.asyncio
async def test_main_returns_zero_when_nothing_selected(monkeypatch):
    """Проверяет корректное завершение без подключения при отсутствии изменений."""
    from scripts import update_profile

    fake_factory = AsyncMock()

    monkeypatch.setattr(
        update_profile,
        "get_settings",
        lambda: Settings(
            api_id=1,
            api_hash="hash",
            gemini_api_key="gemini-key",
            session_name="84523248603",
            proxy_url=None,
        ),
    )
    monkeypatch.setattr(
        update_profile,
        "collect_profile_changes",
        lambda input_func=input: update_profile.ProfileChanges(),
    )
    monkeypatch.setattr(update_profile, "UserBotClient", fake_factory)

    result = await update_profile.main()

    assert result == 0
    fake_factory.assert_not_called()
