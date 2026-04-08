"""Тесты runtime-слоя: клиент Telegram и точка входа."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import Settings
from userbot.client import UserBotClient, _build_proxy_settings


class FakeTelegramClient:
    """Простая подмена Telethon-клиента для unit-тестов."""

    def __init__(
        self,
        session_name: str,
        api_id: int,
        api_hash: str,
        proxy: object | None = None,
    ) -> None:
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy = proxy
        self.start = AsyncMock()
        self.disconnect = AsyncMock()
        self.run_until_disconnected = AsyncMock()
        self.add_event_handler = AsyncMock()
        self.is_connected = lambda: True


@pytest.mark.asyncio
async def test_userbot_client_start_and_stop(monkeypatch):
    """Проверяет, что обёртка делегирует запуск и остановку Telethon-клиенту."""
    fake_client = FakeTelegramClient("session", 1, "hash")

    monkeypatch.setattr(
        "userbot.client._build_telegram_client",
        lambda session_name, api_id, api_hash, proxy=None: fake_client,
    )

    client = UserBotClient(session_name="session", api_id=1, api_hash="hash")
    await client.start()
    await client.stop()

    fake_client.start.assert_awaited_once()
    fake_client.disconnect.assert_awaited_once()
    assert client.client is fake_client


@pytest.mark.asyncio
async def test_userbot_client_passes_proxy_to_telegram_client(monkeypatch):
    """Проверяет передачу proxy в Telethon-клиент."""
    captured: dict[str, object] = {}
    fake_client = FakeTelegramClient("session", 1, "hash")

    def build_client(session_name: str, api_id: int, api_hash: str, proxy=None):
        captured["session_name"] = session_name
        captured["api_id"] = api_id
        captured["api_hash"] = api_hash
        captured["proxy"] = proxy
        return fake_client

    monkeypatch.setattr("userbot.client._build_telegram_client", build_client)

    client = UserBotClient(
        session_name="session",
        api_id=1,
        api_hash="hash",
        proxy_url="http://user:pass@127.0.0.1:8080",
    )
    await client.start()

    assert captured["proxy"] == {
        "proxy_type": "http",
        "addr": "127.0.0.1",
        "port": 8080,
        "username": "user",
        "password": "pass",
        "rdns": True,
    }


def test_build_proxy_settings_for_http_proxy():
    """Проверяет преобразование HTTP proxy URL в формат Telethon."""
    proxy = _build_proxy_settings("http://user:pass@127.0.0.1:8080")

    assert proxy == {
        "proxy_type": "http",
        "addr": "127.0.0.1",
        "port": 8080,
        "username": "user",
        "password": "pass",
        "rdns": True,
    }


def test_build_proxy_settings_returns_none_when_proxy_missing():
    """Проверяет, что при отсутствии proxy возвращается None."""
    assert _build_proxy_settings(None) is None


def test_build_proxy_settings_rejects_https_proxy():
    """Проверяет явный отказ от неподдерживаемого HTTPS proxy для Telethon."""
    with pytest.raises(ValueError, match="Неподдерживаемая схема proxy"):
        _build_proxy_settings("https://127.0.0.1:8443")


@pytest.mark.asyncio
async def test_main_initializes_components(monkeypatch):
    """Проверяет, что main() инициализирует зависимости и запускает клиент."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_name="84523248603",
        db_path=":memory:",
        whitelist_path="data/whitelist.md",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        proxy_url="http://127.0.0.1:8080",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace(load=AsyncMock())
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = FakeTelegramClient("session", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )

    monkeypatch.setattr(run, "get_settings", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda whitelist_path: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(
        run,
        "GeminiClient",
        lambda api_key, proxy_url=None: SimpleNamespace(
            api_key=api_key,
            proxy_url=proxy_url,
        ),
    )
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    await run.main()

    history.init_db.assert_awaited_once()
    whitelist.load.assert_awaited_once()
    topic_selector.load.assert_awaited_once()
    fake_userbot_client.start.assert_awaited_once()
    fake_telegram_client.run_until_disconnected.assert_awaited_once()
