"""Тесты логирования swarm-режима."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.logging import setup_logging
from core.runtime_models import SwarmBotProfile
from userbot.orchestrator import SwarmOrchestrator
from userbot.reply_router import AddressedReplyRouter


def test_setup_logging_sets_root_level():
    """Проверяет, что настройка логирования меняет уровень root logger."""
    setup_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


@pytest.mark.asyncio
async def test_reply_router_logs_ignore_reason(caplog):
    """Проверяет логирование причины ignore в reply-router."""
    router = AddressedReplyRouter(
        bot_profile=SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
        history=SimpleNamespace(get_session_history=AsyncMock(), save_message=AsyncMock()),
        prompt_composer=SimpleNamespace(compose=AsyncMock(return_value="system")),
        gemini_client=SimpleNamespace(generate_reply=AsyncMock()),
        swarm_user_ids={202, 303},
    )
    event = SimpleNamespace(sender_id=999, raw_text="Привет", is_reply=False, id=77)

    with caplog.at_level(logging.INFO):
        handled = await router.handle_event(event)

    assert handled is False
    assert any("ignore non-reply" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_orchestrator_logs_skip_on_recent_human_activity(caplog):
    """Проверяет логирование skip при недавней человеческой активности."""
    orchestrator = SwarmOrchestrator(
        bot_profiles=[
            SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
            SwarmBotProfile(id="mike", session_string="mike", persona_file="mike.md", telegram_user_id=202),
        ],
        manager=SimpleNamespace(),
        topic_selector=SimpleNamespace(),
        prompt_composer=SimpleNamespace(),
        gemini_client=SimpleNamespace(),
        history=SimpleNamespace(),
        exchange_store=SimpleNamespace(),
        group_target="@chat",
        skip_if_recent_human_activity=True,
        human_activity_checker=lambda: True,
    )

    with caplog.at_level(logging.INFO):
        started = await orchestrator.run_once()

    assert started is False
    assert any("recent human activity" in record.getMessage() for record in caplog.records)
