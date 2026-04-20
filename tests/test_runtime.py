"""Тесты runtime-слоя swarm и bootstrap run.py."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.config import Settings
from userbot.client import UserBotClient, _build_proxy_settings


class FakeTelegramClient:
    """Простая подмена Telethon-клиента для unit-тестов."""

    def __init__(self, session_string: object, api_id: int, api_hash: str, proxy: object | None = None) -> None:
        self.session_string = session_string
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy = proxy
        self.start = AsyncMock()
        self.disconnect = AsyncMock()
        self.run_until_disconnected = AsyncMock()
        self.add_event_handler = Mock()
        self.send_message = AsyncMock()
        self.get_messages = AsyncMock(return_value=[])
        self.get_entity = AsyncMock(return_value="@group")
        self.get_me = AsyncMock(return_value=SimpleNamespace(id=111))
        self.joined_targets = []
        self.imported_invites = []
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
async def test_userbot_client_delegates_run_until_disconnected(monkeypatch):
    """Проверяет проксирование run_until_disconnected к Telethon-клиенту."""
    fake_client = FakeTelegramClient("session-string", 1, "hash")
    monkeypatch.setattr(
        "userbot.client._build_telegram_client",
        lambda session_string, api_id, api_hash, proxy=None: fake_client,
    )

    client = UserBotClient(session_string="session-string", api_id=1, api_hash="hash")
    await client.start()
    await client.run_until_disconnected()

    fake_client.run_until_disconnected.assert_awaited_once()


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
async def test_main_runs_swarm_mode(monkeypatch):
    """Проверяет, что main() запускает swarm-bootstrap и закрывает runtime."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="legacy-unused",
        db_path=":memory:",
        settings_path=None,
    )
    settings.mode = "swarm"
    runtime_context = SimpleNamespace(close=AsyncMock())
    scheduler = SimpleNamespace(start=Mock(), add_job=Mock(), shutdown=Mock())

    monkeypatch.setattr(run, "load_settings_or_exit", lambda: settings)
    monkeypatch.setattr(run, "_build_runtime_context", AsyncMock(return_value=runtime_context))
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: scheduler)
    monkeypatch.setattr(run, "_run_swarm_mode", AsyncMock())

    await run.main()

    scheduler.start.assert_called_once()
    run._run_swarm_mode.assert_awaited_once_with(settings, runtime_context, scheduler)
    runtime_context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_swarm_handlers_registers_handler_per_bot(monkeypatch):
    """Проверяет регистрацию addressed handlers для каждого активного бота."""
    import run

    fake_client_anna = FakeTelegramClient("anna", 1, "hash")
    fake_client_mike = FakeTelegramClient("mike", 1, "hash")
    manager = SimpleNamespace(
        active_bot_ids=["anna", "mike"],
        bot_profiles=[
            SimpleNamespace(id="anna", enabled=True, telegram_user_id=101, persona_file="anna.md"),
            SimpleNamespace(id="mike", enabled=True, telegram_user_id=202, persona_file="mike.md"),
        ],
        get_client=lambda bot_id: SimpleNamespace(client=fake_client_anna if bot_id == "anna" else fake_client_mike),
        swarm_user_ids={101, 202},
        human_slot=lambda _bot_id: _AsyncNullContext(),
    )
    runtime = SimpleNamespace(history=object(), prompt_composer=object(), gemini_client=object())
    monkeypatch.setitem(__import__("sys").modules, "telethon", SimpleNamespace(events=SimpleNamespace(NewMessage=lambda: "new-message")))

    await run._register_swarm_handlers(manager, runtime)

    assert fake_client_anna.add_event_handler.call_count == 1
    assert fake_client_mike.add_event_handler.call_count == 1


@pytest.mark.asyncio
async def test_register_swarm_handlers_skips_profiles_outside_active_pool(monkeypatch):
    """Проверяет, что handler не регистрируется для бота, исключённого при startup."""
    import run

    fake_client_anna = FakeTelegramClient("anna", 1, "hash")
    manager = SimpleNamespace(
        active_bot_ids=["anna"],
        bot_profiles=[
            SimpleNamespace(id="anna", enabled=True, telegram_user_id=101, persona_file="anna.md"),
            SimpleNamespace(id="vitaly", enabled=True, telegram_user_id=None, persona_file="vitaly.md"),
        ],
        get_client=lambda bot_id: SimpleNamespace(client=fake_client_anna) if bot_id == "anna" else (_ for _ in ()).throw(KeyError(bot_id)),
        swarm_user_ids={101},
        human_slot=lambda _bot_id: _AsyncNullContext(),
    )
    runtime = SimpleNamespace(history=object(), prompt_composer=object(), gemini_client=object())
    monkeypatch.setitem(__import__("sys").modules, "telethon", SimpleNamespace(events=SimpleNamespace(NewMessage=lambda: "new-message")))

    await run._register_swarm_handlers(manager, runtime)

    assert fake_client_anna.add_event_handler.call_count == 1


@pytest.mark.asyncio
async def test_run_swarm_mode_starts_manager_registers_scheduler_and_supervises(monkeypatch):
    """Интеграционно проверяет запуск swarm-режима с несколькими ботами."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="legacy-unused",
        group_target="@group",
        db_path=":memory:",
        settings_path=None,
    )
    settings.mode = "swarm"
    settings.swarm_tick_seconds = 30
    settings.swarm_bots = [
        SimpleNamespace(id="anna", session_string="anna-session", persona_file="anna.md", enabled=True, temperature=0.9, session_env="SESSION_STRING_ANNA"),
        SimpleNamespace(id="mike", session_string="mike-session", persona_file="mike.md", enabled=True, temperature=0.8, session_env="SESSION_STRING_MIKE"),
    ]

    fake_anna_client = FakeTelegramClient("anna", 1, "hash")
    fake_mike_client = FakeTelegramClient("mike", 1, "hash")
    manager = SimpleNamespace(
        active_bot_ids=["anna", "mike"],
        bot_profiles=[
            SimpleNamespace(id="anna", enabled=True, telegram_user_id=101, persona_file="anna.md"),
            SimpleNamespace(id="mike", enabled=True, telegram_user_id=202, persona_file="mike.md"),
        ],
        start=AsyncMock(),
        stop=AsyncMock(),
        supervise_bot=AsyncMock(side_effect=[None, None]),
        get_client=lambda bot_id: SimpleNamespace(client=fake_anna_client if bot_id == "anna" else fake_mike_client),
        swarm_user_ids={101, 202},
    )
    runtime = SimpleNamespace(
        topic_selector=SimpleNamespace(),
        prompt_composer=SimpleNamespace(),
        gemini_client=SimpleNamespace(),
        history=SimpleNamespace(),
        exchange_store=SimpleNamespace(),
    )
    scheduler = SimpleNamespace(add_job=Mock())

    monkeypatch.setattr(run, "SwarmManager", lambda **kwargs: manager)
    monkeypatch.setattr(run, "_register_swarm_handlers", AsyncMock())
    monkeypatch.setattr(run, "_log_resolved_group", AsyncMock())
    monkeypatch.setattr(run, "_resolve_group_target", AsyncMock(return_value="@group"))
    monkeypatch.setattr(run, "SwarmOrchestrator", lambda **kwargs: SimpleNamespace(run_once=AsyncMock()))

    await run._run_swarm_mode(settings, runtime, scheduler)

    manager.start.assert_awaited_once()
    run._register_swarm_handlers.assert_awaited_once()
    scheduler.add_job.assert_called_once()
    manager.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_swarm_mode_requires_two_active_bots_after_start(monkeypatch):
    """Проверяет отказ запуска, если после startup остался один бот."""
    import run

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_string="legacy-unused",
        group_target="@group",
        db_path=":memory:",
        settings_path=None,
    )
    settings.mode = "swarm"
    settings.swarm_bots = [
        SimpleNamespace(id="anna", session_string="anna-session", persona_file="anna.md", enabled=True, temperature=0.9, session_env="SESSION_STRING_ANNA"),
        SimpleNamespace(id="mike", session_string="mike-session", persona_file="mike.md", enabled=True, temperature=0.8, session_env="SESSION_STRING_MIKE"),
    ]
    manager = SimpleNamespace(
        active_bot_ids=["anna"],
        bot_profiles=[SimpleNamespace(id="anna", enabled=True, telegram_user_id=101, persona_file="anna.md")],
        start=AsyncMock(),
        stop=AsyncMock(),
        supervise_bot=AsyncMock(),
        get_client=lambda _bot_id: SimpleNamespace(client=FakeTelegramClient("anna", 1, "hash")),
        swarm_user_ids={101},
    )
    runtime = SimpleNamespace(
        topic_selector=SimpleNamespace(),
        prompt_composer=SimpleNamespace(),
        gemini_client=SimpleNamespace(),
        history=SimpleNamespace(),
        exchange_store=SimpleNamespace(),
    )
    scheduler = SimpleNamespace(add_job=Mock())

    monkeypatch.setattr(run, "SwarmManager", lambda **kwargs: manager)

    with pytest.raises(ValueError, match="at least two active bots"):
        await run._run_swarm_mode(settings, runtime, scheduler)

    manager.start.assert_awaited_once()
    scheduler.add_job.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_group_membership_joins_public_target(monkeypatch):
    """Проверяет автovступление в публичную группу через group_target."""
    import run

    telegram_client = FakeTelegramClient("anna", 1, "hash")
    telegram_client.get_entity = AsyncMock(side_effect=[ValueError("not joined"), "@joined"])

    join_group = AsyncMock(side_effect=lambda target: telegram_client.joined_targets.append(target))
    wrapper = SimpleNamespace(client=telegram_client, join_group=join_group, join_invite_link=AsyncMock())

    resolved = await run._ensure_group_membership(wrapper, None, "@my_group", "anna")

    assert resolved == "@joined"
    join_group.assert_awaited_once_with("@my_group")


@pytest.mark.asyncio
async def test_ensure_group_membership_imports_invite_link(monkeypatch):
    """Проверяет автovступление в приватную группу через invite link."""
    import run

    telegram_client = FakeTelegramClient("anna", 1, "hash")
    telegram_client.get_entity = AsyncMock(side_effect=[ValueError("not joined"), "@joined"])

    join_invite_link = AsyncMock(side_effect=lambda link: telegram_client.imported_invites.append(link))
    wrapper = SimpleNamespace(client=telegram_client, join_group=AsyncMock(), join_invite_link=join_invite_link)

    resolved = await run._ensure_group_membership(wrapper, None, "https://t.me/+InviteHash", "anna")

    assert resolved == "@joined"
    join_invite_link.assert_awaited_once_with("https://t.me/+InviteHash")


@pytest.mark.asyncio
async def test_resolve_group_target_skips_get_entity_for_invite_link():
    """Проверяет, что invite link не используется для прямого get_entity-резолва."""
    import run

    class DialogClient(FakeTelegramClient):
        async def iter_dialogs(self):
            if False:
                yield None

    telegram_client = DialogClient("anna", 1, "hash")

    resolved = await run._resolve_group_target(telegram_client, 123, "https://t.me/+InviteHash")

    assert resolved is None
    telegram_client.get_entity.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_group_membership_raises_clear_error_when_group_id_is_unavailable():
    """Проверяет понятную ошибку, если бот не видит группу по корректному chat_id."""
    import run

    class DialogClient(FakeTelegramClient):
        async def iter_dialogs(self):
            if False:
                yield None

    telegram_client = DialogClient("anna", 1, "hash")
    wrapper = SimpleNamespace(client=telegram_client, join_group=AsyncMock(), join_invite_link=AsyncMock())

    with pytest.raises(ValueError, match="не имеет доступа к группе с GROUP_CHAT_ID=123"):
        await run._ensure_group_membership(wrapper, 123, "https://t.me/+InviteHash", "anna")

    wrapper.join_invite_link.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_group_membership_returns_dialog_entity_without_join():
    """Проверяет, что при уже доступной группе дополнительный join не нужен."""
    import run

    entity = SimpleNamespace(id=123)

    class DialogClient(FakeTelegramClient):
        async def iter_dialogs(self):
            yield SimpleNamespace(id=123, entity=entity)

    telegram_client = DialogClient("anna", 1, "hash")
    wrapper = SimpleNamespace(client=telegram_client, join_group=AsyncMock(), join_invite_link=AsyncMock())

    resolved = await run._ensure_group_membership(wrapper, 123, None, "anna")

    assert resolved is entity
    wrapper.join_group.assert_not_called()
    wrapper.join_invite_link.assert_not_called()


class _AsyncNullContext:
    """Минимальный async context manager для тестов."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False
