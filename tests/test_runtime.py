"""Тесты runtime-слоя: клиент Telegram и точка входа."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.config import Settings
from userbot.client import UserBotClient, _build_proxy_settings


class FakeTelegramClient:
    """Простая подмена Telethon-клиента для unit-тестов."""

    def __init__(
        self,
        session_string: object,
        api_id: int,
        api_hash: str,
        proxy: object | None = None,
    ) -> None:
        self.session_string = session_string
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
    fake_client = FakeTelegramClient("session-string", 1, "hash")

    monkeypatch.setattr(
        "userbot.client._build_telegram_client",
        lambda session_string, api_id, api_hash, proxy=None: fake_client,
    )

    client = UserBotClient(session_string="session-string", api_id=1, api_hash="hash")
    await client.start()
    await client.stop()

    fake_client.start.assert_awaited_once()
    fake_client.disconnect.assert_awaited_once()
    assert client.client is fake_client


@pytest.mark.asyncio
async def test_userbot_client_passes_proxy_to_telegram_client(monkeypatch):
    """Проверяет передачу proxy в Telethon-клиент."""
    captured: dict[str, object] = {}
    fake_client = FakeTelegramClient("session-string", 1, "hash")

    def build_client(session_string: str, api_id: int, api_hash: str, proxy=None):
        captured["session_string"] = session_string
        captured["api_id"] = api_id
        captured["api_hash"] = api_hash
        captured["proxy"] = proxy
        return fake_client

    monkeypatch.setattr("userbot.client._build_telegram_client", build_client)

    client = UserBotClient(
        session_string="session-string",
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


def test_build_telegram_client_uses_string_session(monkeypatch):
    """Проверяет, что Telethon-клиент создаётся из StringSession."""
    import sys

    from userbot import client as client_module

    captured: dict[str, object] = {}

    class FakeStringSession:
        def __init__(self, value: str) -> None:
            captured["session_value"] = value
            self.value = value

    class FakeTelegramClientFactory:
        def __call__(self, session: object, api_id: int, api_hash: str, proxy=None) -> object:
            captured["session_object"] = session
            captured["api_id"] = api_id
            captured["api_hash"] = api_hash
            captured["proxy"] = proxy
            return "telegram-client"

    monkeypatch.setitem(
        sys.modules,
        "telethon",
        SimpleNamespace(TelegramClient=FakeTelegramClientFactory()),
    )
    monkeypatch.setitem(
        sys.modules,
        "telethon.sessions",
        SimpleNamespace(StringSession=FakeStringSession),
    )

    telegram_client = client_module._build_telegram_client(
        "session-string",
        42,
        "hash",
        proxy={"proxy_type": "http"},
    )

    assert telegram_client == "telegram-client"
    assert captured["session_value"] == "session-string"
    assert isinstance(captured["session_object"], FakeStringSession)
    assert captured["api_id"] == 42
    assert captured["api_hash"] == "hash"
    assert captured["proxy"] == {"proxy_type": "http"}


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
        session_string="session-string",
        db_path=":memory:",
        whitelist_path="data/whitelist.md",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        proxy_url="http://127.0.0.1:8080",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace(load=AsyncMock())
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda whitelist_path: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(
        run,
        "GeminiClient",
        lambda api_key, model_name=None, proxy_url=None, fallback_model_name=None, max_retries=None, retry_backoff_seconds=None, retry_jitter_seconds=None: SimpleNamespace(
            api_key=api_key,
            model_name=model_name,
            proxy_url=proxy_url,
            fallback_model_name=fallback_model_name,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_jitter_seconds=retry_jitter_seconds,
        ),
    )
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=lambda *a, **kw: None, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    await run.main()

    history.init_db.assert_awaited_once()
    whitelist.load.assert_awaited_once()
    topic_selector.load.assert_awaited_once()
    fake_userbot_client.start.assert_awaited_once()
    fake_telegram_client.run_until_disconnected.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_passes_gemini_resilience_settings(monkeypatch):
    """Проверяет проброс retry-параметров и резервной модели в GeminiClient."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        gemini_model="gemini-2.5-flash",
        gemini_fallback_model="gemini-2.5-flash-lite",
        gemini_max_retries=4,
        gemini_retry_backoff_seconds=2.0,
        gemini_retry_jitter_seconds=0.3,
        session_string="session-string",
        db_path=":memory:",
        whitelist_path="data/whitelist.md",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        proxy_url="http://127.0.0.1:8080",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace(load=AsyncMock())
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda whitelist_path: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())

    def build_gemini_client(
        api_key,
        model_name=None,
        proxy_url=None,
        fallback_model_name=None,
        max_retries=None,
        retry_backoff_seconds=None,
        retry_jitter_seconds=None,
    ):
        captured["api_key"] = api_key
        captured["model_name"] = model_name
        captured["proxy_url"] = proxy_url
        captured["fallback_model_name"] = fallback_model_name
        captured["max_retries"] = max_retries
        captured["retry_backoff_seconds"] = retry_backoff_seconds
        captured["retry_jitter_seconds"] = retry_jitter_seconds
        return SimpleNamespace()

    monkeypatch.setattr(run, "GeminiClient", build_gemini_client)
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=lambda *a, **kw: None, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    await run.main()

    assert captured == {
        "api_key": "gemini-key",
        "model_name": "gemini-2.5-flash",
        "proxy_url": "http://127.0.0.1:8080",
        "fallback_model_name": "gemini-2.5-flash-lite",
        "max_retries": 4,
        "retry_backoff_seconds": 2.0,
        "retry_jitter_seconds": 0.3,
    }


def test_build_telegram_client_rejects_blank_session_string():
    """Проверяет явный отказ от пустой строковой сессии."""
    from userbot.client import _build_telegram_client

    with pytest.raises(ValueError, match="SESSION_STRING"):
        _build_telegram_client("   ", 1, "hash")
