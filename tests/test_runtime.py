"""Тесты runtime-слоя: клиент Telegram и точка входа."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
        self.send_message = AsyncMock()
        self.get_messages = AsyncMock(return_value=[])
        self.get_entity = AsyncMock()
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
        whitelist_user_ids="123456789,987654321",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        proxy_url="http://127.0.0.1:8080",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(
        run,
        "GeminiClient",
        lambda api_key, model_name=None, proxy_url=None, fallback_model_name=None, max_retries=None, retry_backoff_seconds=None, retry_jitter_seconds=None, request_timeout_seconds=None: SimpleNamespace(
            api_key=api_key,
            model_name=model_name,
            proxy_url=proxy_url,
            fallback_model_name=fallback_model_name,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_jitter_seconds=retry_jitter_seconds,
            request_timeout_seconds=request_timeout_seconds,
        ),
    )
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=lambda *a, **kw: None, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    await run.main()

    history.init_db.assert_awaited_once()
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
        gemini_request_timeout_seconds=45.0,
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        proxy_url="http://127.0.0.1:8080",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
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
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())

    def build_gemini_client(
        api_key,
        model_name=None,
        proxy_url=None,
        fallback_model_name=None,
        max_retries=None,
        retry_backoff_seconds=None,
        retry_jitter_seconds=None,
        request_timeout_seconds=None,
    ):
        captured["api_key"] = api_key
        captured["model_name"] = model_name
        captured["proxy_url"] = proxy_url
        captured["fallback_model_name"] = fallback_model_name
        captured["max_retries"] = max_retries
        captured["retry_backoff_seconds"] = retry_backoff_seconds
        captured["retry_jitter_seconds"] = retry_jitter_seconds
        captured["request_timeout_seconds"] = request_timeout_seconds
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
        "request_timeout_seconds": 45.0,
    }


def test_build_telegram_client_rejects_blank_session_string():
    """Проверяет явный отказ от пустой строковой сессии."""
    from userbot.client import _build_telegram_client

    with pytest.raises(ValueError, match="SESSION_STRING"):
        _build_telegram_client("   ", 1, "hash")


@pytest.mark.asyncio
async def test_main_schedules_silence_checks_every_five_minutes(monkeypatch):
    """Проверяет, что проверка тишины запускается каждые 5 минут с явными ограничениями инстансов."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        scheduler_enabled=True,
        silence_timeout_minutes=60,
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )
    add_job_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def add_job(*args, **kwargs):
        add_job_calls.append((args, kwargs))

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(
        run,
        "ConversationSession",
        lambda duration_minutes=30: SimpleNamespace(is_active=lambda: False),
    )
    monkeypatch.setattr(
        run,
        "AsyncIOScheduler",
        lambda: SimpleNamespace(add_job=add_job, start=lambda: None),
    )
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    await run.main()

    assert len(add_job_calls) == 2
    session_job_args, session_job_kwargs = add_job_calls[0]
    silence_job_args, silence_job_kwargs = add_job_calls[1]
    assert session_job_args[1] == "interval"
    assert session_job_kwargs["minutes"] == 1
    assert silence_job_args[1] == "interval"
    assert silence_job_kwargs["minutes"] == 5
    assert silence_job_kwargs["max_instances"] == 1
    assert silence_job_kwargs["coalesce"] is True


@pytest.mark.asyncio
async def test_main_binds_group_chat_id_to_telegram_client(monkeypatch):
    """Проверяет, что целевой chat_id группы передаётся в runtime Telegram-клиента."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        group_chat_id=-100555000111,
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=lambda *a, **kw: None, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    await run.main()

    assert fake_telegram_client.group_chat_id == -100555000111
    assert fake_telegram_client.group_target is None
    assert fake_telegram_client.dnd_hours_utc is None
    assert fake_telegram_client.scheduler_enabled is True


@pytest.mark.asyncio
async def test_main_binds_group_target_to_telegram_client(monkeypatch):
    """Проверяет, что строковый target группы передаётся в runtime Telegram-клиента."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        group_chat_id=-100555000111,
        group_target="@target_group",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=lambda *a, **kw: None, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    await run.main()

    assert fake_telegram_client.group_chat_id == -100555000111
    assert fake_telegram_client.group_target == "@target_group"


@pytest.mark.asyncio
async def test_sync_group_activity_uses_latest_message_timestamp():
    """Проверяет, что время последнего сообщения группы синхронизируется в SilenceWatcher."""
    from datetime import datetime, timedelta

    import run
    from userbot.scheduler import SilenceWatcher

    message_time = datetime.now() - timedelta(minutes=25)
    resolved_entity = SimpleNamespace(id=555000111, access_hash=123)

    async def iter_dialogs():
        yield SimpleNamespace(id=-100555000111, entity=resolved_entity)

    telegram_client = SimpleNamespace(
        get_messages=AsyncMock(return_value=[SimpleNamespace(date=message_time)]),
        iter_dialogs=iter_dialogs,
    )
    silence_watcher = SilenceWatcher()

    await run._sync_group_activity(telegram_client, -100555000111, None, silence_watcher)

    telegram_client.get_messages.assert_awaited_once_with(resolved_entity, limit=1)
    assert silence_watcher.is_silence_exceeded(20) is True
    assert silence_watcher.is_silence_exceeded(30) is False


@pytest.mark.asyncio
async def test_sync_group_activity_resolves_group_entity_via_dialogs():
    """Проверяет, что синхронизация использует найденную entity группы вместо сырого chat_id."""
    from datetime import datetime, timedelta

    import run
    from userbot.scheduler import SilenceWatcher

    message_time = datetime.now() - timedelta(minutes=15)
    resolved_entity = SimpleNamespace(id=1453890188, access_hash=123)

    async def iter_dialogs():
        yield SimpleNamespace(id=-1001453890188, entity=resolved_entity)

    telegram_client = SimpleNamespace(
        get_messages=AsyncMock(return_value=[SimpleNamespace(date=message_time)]),
        iter_dialogs=iter_dialogs,
    )
    silence_watcher = SilenceWatcher()

    await run._sync_group_activity(telegram_client, -1001453890188, None, silence_watcher)

    telegram_client.get_messages.assert_awaited_once_with(resolved_entity, limit=1)
    assert silence_watcher.is_silence_exceeded(10) is True
    assert silence_watcher.is_silence_exceeded(20) is False


@pytest.mark.asyncio
async def test_resolve_group_target_uses_explicit_group_target_via_get_entity():
    """Проверяет резолв target через get_entity, если задан GROUP_TARGET."""
    import run

    resolved_entity = SimpleNamespace(id=1453890188, access_hash=123)
    telegram_client = SimpleNamespace(
        get_entity=AsyncMock(return_value=resolved_entity),
    )

    result = await run._resolve_group_target(
        telegram_client,
        group_chat_id=-1001453890188,
        group_target="@target_group",
    )

    telegram_client.get_entity.assert_awaited_once_with("@target_group")
    assert result is resolved_entity


@pytest.mark.asyncio
async def test_log_resolved_group_logs_title_id_and_username(caplog, monkeypatch):
    """Проверяет логирование найденной целевой группы со всеми основными полями."""
    import logging
    import run

    resolved_entity = SimpleNamespace(title="Рабочая группа", id=1453890188, username="target_group")
    monkeypatch.setattr(run, "_resolve_group_target", AsyncMock(return_value=resolved_entity))

    with caplog.at_level(logging.INFO):
        await run._log_resolved_group(
            telegram_client=SimpleNamespace(),
            group_chat_id=-1001453890188,
            group_target="@target_group",
        )

    assert any(
        "Целевая группа определена: title=Рабочая группа, id=1453890188, username=@target_group"
        in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_log_resolved_group_logs_title_and_id_without_username(caplog, monkeypatch):
    """Проверяет логирование найденной группы без username."""
    import logging
    import run

    resolved_entity = SimpleNamespace(title="Рабочая группа", id=1453890188)
    monkeypatch.setattr(run, "_resolve_group_target", AsyncMock(return_value=resolved_entity))

    with caplog.at_level(logging.INFO):
        await run._log_resolved_group(
            telegram_client=SimpleNamespace(),
            group_chat_id=-1001453890188,
            group_target=None,
        )

    assert any(
        "Целевая группа определена: title=Рабочая группа, id=1453890188" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_log_resolved_group_logs_warning_when_group_is_unresolved(caplog, monkeypatch):
    """Проверяет warning-лог, если целевая группа не найдена при запуске."""
    import logging
    import run

    monkeypatch.setattr(run, "_resolve_group_target", AsyncMock(return_value=None))

    with caplog.at_level(logging.WARNING):
        await run._log_resolved_group(
            telegram_client=SimpleNamespace(),
            group_chat_id=-1001453890188,
            group_target="@target_group",
        )

    assert any(
        "Не удалось определить целевую группу при инициализации: GROUP_CHAT_ID=-1001453890188, GROUP_TARGET=@target_group"
        in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_sync_group_activity_does_not_fail_when_entity_is_unresolved(caplog):
    """Проверяет, что ошибка резолва entity не роняет планировщик."""
    import logging
    import run
    from userbot.scheduler import SilenceWatcher

    telegram_client = SimpleNamespace(
        get_messages=AsyncMock(side_effect=ValueError("Could not find the input entity"))
    )
    silence_watcher = SilenceWatcher()

    with caplog.at_level(logging.WARNING):
        await run._sync_group_activity(telegram_client, -1001453890188, None, silence_watcher)

    assert any("Не удалось получить последнее сообщение группы" in record.getMessage() for record in caplog.records)
    assert silence_watcher._last_activity is None


@pytest.mark.asyncio
async def test_main_logs_resolved_group_before_waiting_for_messages(monkeypatch):
    """Проверяет, что main() логирует целевую группу до запуска ожидания сообщений."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        group_chat_id=-100555000111,
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )
    call_order: list[str] = []

    async def fake_log_resolved_group(*_args, **_kwargs):
        call_order.append("log_group")

    async def fake_register_handlers(*_args, **_kwargs):
        call_order.append("register_handlers")

    async def fake_run_until_disconnected():
        call_order.append("run_until_disconnected")

    fake_telegram_client.run_until_disconnected = fake_run_until_disconnected

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=lambda *a, **kw: None, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_log_resolved_group", fake_log_resolved_group)
    monkeypatch.setattr(run, "_register_handlers", fake_register_handlers)

    await run.main()

    assert call_order == ["log_group", "register_handlers", "run_until_disconnected"]


@pytest.mark.asyncio
async def test_silence_check_job_skips_generation_when_group_target_is_unresolved(monkeypatch, caplog):
    """Проверяет, что без резолва группы job не тратит запрос Gemini и не отправляет сообщение."""
    import logging
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        scheduler_enabled=True,
        group_chat_id=-100555000111,
        silence_timeout_minutes=60,
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock(), pick_random=AsyncMock(return_value="Тема"))
    prompt_loader = SimpleNamespace(load=AsyncMock(side_effect=["system", "start_topic"]))
    gemini_client = SimpleNamespace(start_topic=AsyncMock(return_value="Сообщение по теме"))
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )
    captured_jobs: list[object] = []
    conversation_session = SimpleNamespace(
        is_active=lambda: False,
        start=Mock(),
    )
    silence_watcher = SimpleNamespace(
        is_silence_exceeded=lambda timeout_minutes: True,
        update_last_activity=Mock(),
    )

    def add_job(func, *_args, **_kwargs):
        captured_jobs.append(func)

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: prompt_loader)
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: gemini_client)
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: conversation_session)
    monkeypatch.setattr(run, "SilenceWatcher", lambda: silence_watcher)
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=add_job, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())
    monkeypatch.setattr(run, "_sync_group_activity", AsyncMock())
    monkeypatch.setattr(run, "_resolve_group_target", AsyncMock(return_value=None))

    await run.main()

    silence_job = captured_jobs[1]
    with caplog.at_level(logging.WARNING):
        await silence_job()

    topic_selector.pick_random.assert_not_awaited()
    prompt_loader.load.assert_not_awaited()
    gemini_client.start_topic.assert_not_awaited()
    fake_telegram_client.send_message.assert_not_awaited()
    assert any("Невозможно начать разговор: не удалось резолвить target группы" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_main_binds_dnd_hours_utc_to_telegram_client(monkeypatch):
    """Проверяет, что DND-интервал привязывается к runtime Telegram-клиента."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        dnd_hours_utc="23-7",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=lambda *a, **kw: None, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    await run.main()

    assert fake_telegram_client.dnd_hours_utc == "23-7"


@pytest.mark.asyncio
async def test_silence_check_job_does_not_start_topic_during_dnd(monkeypatch):
    """Проверяет, что DND блокирует автозапуск новой темы."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        scheduler_enabled=True,
        group_chat_id=-100555000111,
        dnd_hours_utc="23-7",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock(), pick_random=AsyncMock(return_value="Тема"))
    prompt_loader = SimpleNamespace(load=AsyncMock(side_effect=["system", "start_topic"]))
    gemini_client = SimpleNamespace(start_topic=AsyncMock(return_value="Сообщение по теме"))
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )
    captured_jobs: list[object] = []
    conversation_session = SimpleNamespace(
        is_active=lambda: False,
        start=Mock(),
    )

    def add_job(func, *_args, **_kwargs):
        captured_jobs.append(func)

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: prompt_loader)
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: gemini_client)
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: conversation_session)
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=add_job, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())
    monkeypatch.setattr(run, "_utc_now", lambda: datetime(2026, 4, 10, 23, 30, tzinfo=UTC))

    await run.main()

    silence_job = captured_jobs[1]
    await silence_job()

    topic_selector.pick_random.assert_not_awaited()
    gemini_client.start_topic.assert_not_awaited()
    fake_telegram_client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_silence_check_job_skips_activity_sync_during_dnd(monkeypatch, caplog):
    """Проверяет, что во время DND job не запускает проверку активности группы."""
    import run
    import logging

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        scheduler_enabled=True,
        group_chat_id=-100555000111,
        dnd_hours_utc="23-7",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock(), pick_random=AsyncMock(return_value="Тема"))
    prompt_loader = SimpleNamespace(load=AsyncMock(side_effect=["system", "start_topic"]))
    gemini_client = SimpleNamespace(start_topic=AsyncMock(return_value="Сообщение по теме"))
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )
    captured_jobs: list[object] = []
    conversation_session = SimpleNamespace(
        is_active=lambda: False,
        start=Mock(),
    )
    sync_group_activity = AsyncMock()

    def add_job(func, *_args, **_kwargs):
        captured_jobs.append(func)

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: prompt_loader)
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: gemini_client)
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: conversation_session)
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=add_job, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())
    monkeypatch.setattr(run, "_sync_group_activity", sync_group_activity)
    monkeypatch.setattr(run, "_utc_now", lambda: datetime(2026, 4, 10, 23, 30, tzinfo=UTC))

    await run.main()

    silence_job = captured_jobs[1]
    with caplog.at_level(logging.INFO):
        await silence_job()

    sync_group_activity.assert_not_awaited()
    topic_selector.pick_random.assert_not_awaited()
    gemini_client.start_topic.assert_not_awaited()
    fake_telegram_client.send_message.assert_not_awaited()
    assert any("Проверка тишины пропущена: активен режим не беспокоить" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_silence_check_job_logs_timeout_and_skips_session_start(monkeypatch, caplog):
    """Проверяет, что таймаут Gemini не запускает сессию и не отправляет сообщение."""
    import asyncio
    import logging
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        scheduler_enabled=True,
        group_chat_id=-100555000111,
        silence_timeout_minutes=5,
        gemini_request_timeout_seconds=12.0,
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock(), pick_random=AsyncMock(return_value="Тема"))
    prompt_loader = SimpleNamespace(load=AsyncMock(side_effect=["system", "start_topic"]))
    gemini_client = SimpleNamespace(start_topic=AsyncMock(return_value="Сообщение по теме"))
    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )
    captured_jobs: list[object] = []
    conversation_session = SimpleNamespace(
        is_active=lambda: False,
        start=Mock(),
    )
    silence_watcher = SimpleNamespace(
        is_silence_exceeded=lambda timeout_minutes: True,
        update_last_activity=Mock(),
    )

    def add_job(func, *_args, **_kwargs):
        captured_jobs.append(func)

    async def fake_wait_for(_awaitable, timeout: float):
        _awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: prompt_loader)
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: gemini_client)
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: conversation_session)
    monkeypatch.setattr(run, "SilenceWatcher", lambda: silence_watcher)
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: SimpleNamespace(add_job=add_job, start=lambda: None))
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())
    monkeypatch.setattr(run, "_sync_group_activity", AsyncMock())
    monkeypatch.setattr(run, "_resolve_group_target", AsyncMock(return_value="target"))
    monkeypatch.setattr(run.asyncio, "wait_for", fake_wait_for)

    await run.main()

    silence_job = captured_jobs[1]
    with caplog.at_level(logging.WARNING):
        await silence_job()

    conversation_session.start.assert_not_called()
    silence_watcher.update_last_activity.assert_not_called()
    fake_telegram_client.send_message.assert_not_awaited()
    assert any("Таймаут при инициации разговора по расписанию" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_main_treats_cancelled_run_until_disconnected_as_normal_shutdown(monkeypatch, caplog):
    """Проверяет, что отмена ожидания Telegram при остановке не логируется как ошибка."""
    import asyncio
    import logging
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="session-string",
        db_path=":memory:",
        whitelist_user_ids="123456789",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        group_chat_id=-100555000111,
        scheduler_enabled=True,
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace()
    topic_selector = SimpleNamespace(load=AsyncMock())

    async def fake_run_until_disconnected():
        raise asyncio.CancelledError

    fake_telegram_client = FakeTelegramClient("session-string", 1, "hash")
    fake_telegram_client.run_until_disconnected = fake_run_until_disconnected
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )
    scheduler = SimpleNamespace(add_job=lambda *args, **kwargs: None, start=lambda: None, shutdown=AsyncMock())

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda user_ids: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(run, "GeminiClient", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda duration_minutes=30: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: scheduler)
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    with caplog.at_level(logging.INFO):
        await run.main()

    fake_userbot_client.stop.assert_awaited_once()
    scheduler.shutdown.assert_awaited_once()
    assert any("Ожидание сообщений Telegram прервано штатной остановкой приложения" in record.getMessage() for record in caplog.records)
