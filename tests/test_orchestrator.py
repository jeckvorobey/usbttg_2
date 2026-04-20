"""Тесты swarm-orchestrator."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.runtime_models import SwarmBotProfile
from userbot.orchestrator import SwarmOrchestrator


def _manager_with_clients(initiator_client, responder_client):
    return SimpleNamespace(
        get_client=lambda bot_id: SimpleNamespace(client=initiator_client if bot_id == "anna" else responder_client),
        scheduled_slot=lambda _bot_id: _ScheduledSlot(True),
    )


@pytest.mark.asyncio
async def test_orchestrator_skips_exchange_when_recent_human_activity_detected():
    """Проверяет отказ от scheduled exchange при недавней активности людей."""
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

    assert await orchestrator.run_once() is False


@pytest.mark.asyncio
async def test_orchestrator_skips_exchange_outside_active_windows():
    """Проверяет запрет scheduled exchange вне активных UTC-окон."""
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
        active_windows_utc=["10-12"],
        now_provider=lambda: datetime(2026, 4, 20, 13, 0, tzinfo=UTC),
    )

    assert await orchestrator.run_once() is False


@pytest.mark.asyncio
async def test_orchestrator_avoids_recent_pairs_and_topics():
    """Проверяет anti-repeat по persisted парам и темам."""
    exchange_store = SimpleNamespace(
        get_recent_pairs=AsyncMock(return_value=[("anna", "mike")]),
        get_recent_topic_keys=AsyncMock(return_value={"где есть суп"}),
        get_recent_questions=AsyncMock(return_value=[]),
    )
    topic_selector = SimpleNamespace(topics=["Где есть суп", "Куда сходить вечером"])
    orchestrator = SwarmOrchestrator(
        bot_profiles=[
            SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
            SwarmBotProfile(id="mike", session_string="mike", persona_file="mike.md", telegram_user_id=202),
            SwarmBotProfile(id="john", session_string="john", persona_file="john.md", telegram_user_id=303),
        ],
        manager=SimpleNamespace(),
        topic_selector=topic_selector,
        prompt_composer=SimpleNamespace(),
        gemini_client=SimpleNamespace(),
        history=SimpleNamespace(),
        exchange_store=exchange_store,
    )

    decision = await orchestrator._build_exchange_decision()

    assert (decision.initiator.id, decision.responder.id) != ("anna", "mike")
    assert decision.topic == "Куда сходить вечером"


@pytest.mark.asyncio
async def test_orchestrator_regenerates_repeated_question_signature():
    """Проверяет повторную генерацию вопроса при совпадении recent signature."""
    gemini = SimpleNamespace(start_topic=AsyncMock(side_effect=["Один и тот же вопрос?", "Другой вопрос?"]))
    exchange_store = SimpleNamespace(get_recent_question_signatures=AsyncMock(return_value={"один и тот же вопрос"}))
    orchestrator = SwarmOrchestrator(
        bot_profiles=[
            SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
            SwarmBotProfile(id="mike", session_string="mike", persona_file="mike.md", telegram_user_id=202),
        ],
        manager=SimpleNamespace(),
        topic_selector=SimpleNamespace(),
        prompt_composer=SimpleNamespace(),
        gemini_client=gemini,
        history=SimpleNamespace(),
        exchange_store=exchange_store,
    )

    question = await orchestrator._generate_non_repeating_question(initiator_prompt="prompt", topic="Тема")

    assert question == "Другой вопрос?"
    assert gemini.start_topic.await_count == 2


@pytest.mark.asyncio
async def test_orchestrator_runs_exchange_and_saves_history():
    """Проверяет базовый scheduled exchange A -> B и полное сохранение в историю."""
    initiator_client = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(id=501)))
    responder_client = SimpleNamespace(send_message=AsyncMock())
    exchange_store = SimpleNamespace(
        get_recent_pairs=AsyncMock(return_value=[]),
        get_recent_topic_keys=AsyncMock(return_value=set()),
        get_recent_questions=AsyncMock(return_value=[]),
        get_recent_question_signatures=AsyncMock(return_value=set()),
        create_exchange=AsyncMock(return_value="exchange-1"),
        mark_exchange_started=AsyncMock(),
        mark_exchange_completed=AsyncMock(),
        mark_exchange_skipped=AsyncMock(),
    )
    history = SimpleNamespace(
        get_session_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )

    orchestrator = SwarmOrchestrator(
        bot_profiles=[
            SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
            SwarmBotProfile(id="mike", session_string="mike", persona_file="mike.md", telegram_user_id=202),
        ],
        manager=_manager_with_clients(initiator_client, responder_client),
        topic_selector=SimpleNamespace(topics=["Где поесть суп?"]),
        prompt_composer=SimpleNamespace(compose=AsyncMock(side_effect=["system-init", "system-reply"])),
        gemini_client=SimpleNamespace(
            start_topic=AsyncMock(return_value="Кто знает место с хорошим супом?"),
            generate_reply=AsyncMock(return_value="Мне нравится Pho 54."),
        ),
        history=history,
        exchange_store=exchange_store,
        group_target="@chat",
        question_repeat_window=timedelta(days=2),
    )
    orchestrator._build_exchange_decision = AsyncMock(
        return_value=SimpleNamespace(
            initiator=orchestrator.bot_profiles[0],
            responder=orchestrator.bot_profiles[1],
            topic="Где поесть суп?",
            topic_key="где поесть суп",
            recent_questions=[],
        )
    )

    started = await orchestrator.run_once()

    assert started is True
    initiator_client.send_message.assert_awaited_once_with("@chat", "Кто знает место с хорошим супом?")
    responder_client.send_message.assert_awaited_once_with("@chat", "Мне нравится Pho 54.", reply_to=501)
    exchange_store.mark_exchange_started.assert_awaited_once()
    exchange_store.mark_exchange_completed.assert_awaited_once_with("exchange-1")
    assert history.save_message.await_count == 2
    assert history.save_message.await_args_list[0].kwargs["message_origin"] == "scheduled_initiator"
    assert history.save_message.await_args_list[1].kwargs["exchange_id"] == "exchange-1"


@pytest.mark.asyncio
async def test_orchestrator_skips_when_bot_is_busy():
    """Проверяет skip exchange, если бот не получил scheduled slot."""
    exchange_store = SimpleNamespace(
        get_recent_pairs=AsyncMock(return_value=[]),
        get_recent_topic_keys=AsyncMock(return_value=set()),
        get_recent_questions=AsyncMock(return_value=[]),
        create_exchange=AsyncMock(return_value="exchange-1"),
        mark_exchange_skipped=AsyncMock(),
    )
    orchestrator = SwarmOrchestrator(
        bot_profiles=[
            SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
            SwarmBotProfile(id="mike", session_string="mike", persona_file="mike.md", telegram_user_id=202),
        ],
        manager=SimpleNamespace(
            scheduled_slot=lambda bot_id: _ScheduledSlot(bot_id != "anna"),
            get_client=lambda _bot_id: None,
        ),
        topic_selector=SimpleNamespace(topics=["Тема"]),
        prompt_composer=SimpleNamespace(),
        gemini_client=SimpleNamespace(),
        history=SimpleNamespace(),
        exchange_store=exchange_store,
        group_target="@chat",
    )
    orchestrator._build_exchange_decision = AsyncMock(
        return_value=SimpleNamespace(
            initiator=orchestrator.bot_profiles[0],
            responder=orchestrator.bot_profiles[1],
            topic="Тема",
            topic_key="тема",
            recent_questions=[],
        )
    )

    assert await orchestrator.run_once() is False
    exchange_store.mark_exchange_skipped.assert_awaited_once_with("exchange-1", "initiator_busy")


class _ScheduledSlot:
    """Управляемый async context manager для scheduled slot."""

    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired

    async def __aenter__(self):
        return self.acquired

    async def __aexit__(self, exc_type, exc, tb):
        return False
