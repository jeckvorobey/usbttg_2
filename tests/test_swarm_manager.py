"""Тесты менеджера swarm-клиентов."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.runtime_models import SwarmBotProfile
from userbot.swarm_manager import SwarmManager


@pytest.mark.asyncio
async def test_swarm_manager_starts_enabled_bots_and_collects_user_ids():
    """Проверяет запуск enabled-ботов и сбор их Telegram user_id."""
    anna_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        get_current_user=AsyncMock(return_value=SimpleNamespace(id=101)),
        run_until_disconnected=AsyncMock(),
    )
    john_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        get_current_user=AsyncMock(return_value=SimpleNamespace(id=202)),
        run_until_disconnected=AsyncMock(),
    )

    manager = SwarmManager(
        bot_profiles=[
            SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", enabled=True),
            SwarmBotProfile(id="mike", session_string="mike", persona_file="mike.md", enabled=False),
            SwarmBotProfile(id="john", session_string="john", persona_file="john.md", enabled=True),
        ],
        client_factory=lambda profile: anna_client if profile.id == "anna" else john_client,
    )

    await manager.start()

    anna_client.start.assert_awaited_once()
    john_client.start.assert_awaited_once()
    assert manager.swarm_user_ids == {101, 202}
    assert sorted(manager.active_bot_ids) == ["anna", "john"]


@pytest.mark.asyncio
async def test_swarm_manager_prioritizes_human_slot_over_scheduled():
    """Проверяет, что scheduled задача уступает human reply."""
    fake_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        get_current_user=AsyncMock(return_value=SimpleNamespace(id=101)),
        run_until_disconnected=AsyncMock(),
    )
    manager = SwarmManager(
        bot_profiles=[SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md")],
        client_factory=lambda _profile: fake_client,
    )
    await manager.start()

    human_entered = asyncio.Event()
    scheduled_result: list[bool] = []

    async def human_task():
        async with manager.human_slot("anna"):
            human_entered.set()
            await asyncio.sleep(0)

    async def scheduled_task():
        await human_entered.wait()
        async with manager.scheduled_slot("anna") as acquired:
            scheduled_result.append(acquired)

    await asyncio.gather(human_task(), scheduled_task())

    assert scheduled_result == [False]


@pytest.mark.asyncio
async def test_swarm_manager_reconnects_after_client_error():
    """Проверяет reconnect loop после ошибки клиента."""
    stop_signal = asyncio.Event()

    async def failing_run():
        if not stop_signal.is_set():
            stop_signal.set()
            raise RuntimeError("boom")
        return None

    fake_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        get_current_user=AsyncMock(return_value=SimpleNamespace(id=101)),
        run_until_disconnected=AsyncMock(side_effect=failing_run),
    )
    profile = SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md")
    manager = SwarmManager(
        bot_profiles=[profile],
        client_factory=lambda _profile: fake_client,
        reconnect_backoff_seconds=(0.0,),
    )
    await manager.start()

    supervise_task = asyncio.create_task(manager.supervise_bot("anna"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await manager.stop()
    supervise_task.cancel()
    await asyncio.gather(supervise_task, return_exceptions=True)

    assert fake_client.start.await_count >= 2
    assert manager.runtime_states["anna"].reconnect_attempts >= 1


@pytest.mark.asyncio
async def test_swarm_manager_skips_bot_when_startup_fails():
    """Проверяет, что бот с ошибкой startup не попадает в активный пул."""
    anna_client = SimpleNamespace(
        start=AsyncMock(side_effect=RuntimeError("join failed")),
        stop=AsyncMock(),
        get_current_user=AsyncMock(return_value=SimpleNamespace(id=101)),
        run_until_disconnected=AsyncMock(),
    )
    john_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        get_current_user=AsyncMock(return_value=SimpleNamespace(id=202)),
        run_until_disconnected=AsyncMock(),
    )

    manager = SwarmManager(
        bot_profiles=[
            SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", enabled=True),
            SwarmBotProfile(id="john", session_string="john", persona_file="john.md", enabled=True),
        ],
        client_factory=lambda profile: anna_client if profile.id == "anna" else john_client,
    )

    await manager.start()

    assert manager.active_bot_ids == ["john"]
    assert set(manager.clients) == {"john"}
    assert manager.swarm_user_ids == {202}
    assert manager.runtime_states["anna"].status == "error"
