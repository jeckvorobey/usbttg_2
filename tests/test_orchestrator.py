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
        exchange_store=SimpleNamespace(get_due_started_exchange=AsyncMock(return_value=None)),
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
        exchange_store=SimpleNamespace(get_due_started_exchange=AsyncMock(return_value=None)),
        group_target="@chat",
        active_windows_utc=["10-12"],
        now_provider=lambda: datetime(2026, 4, 20, 13, 0, tzinfo=UTC),
    )

    assert await orchestrator.run_once() is False


@pytest.mark.asyncio
async def test_orchestrator_avoids_recent_pairs_and_topics():
    """Проверяет anti-repeat по persisted парам и темам."""
    exchange_store = SimpleNamespace(
        get_due_started_exchange=AsyncMock(return_value=None),
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
    """Проверяет двухфазный scheduled exchange с отложенным ответом."""
    initiator_client = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(id=501)))
    responder_client = SimpleNamespace(send_message=AsyncMock())
    exchange_store = SimpleNamespace(
        get_due_started_exchange=AsyncMock(return_value=None),
        get_exchange_by_window_key=AsyncMock(return_value=None),
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
        active_windows_utc=["19-20"],
        now_provider=lambda: datetime(2026, 4, 20, 19, 5, tzinfo=UTC),
        initiator_offset_minutes=(5, 5),
        responder_delay_minutes=(8, 8),
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
    responder_client.send_message.assert_not_awaited()
    exchange_store.mark_exchange_started.assert_awaited_once()
    exchange_store.mark_exchange_completed.assert_not_awaited()
    assert history.save_message.await_count == 1
    assert history.save_message.await_args_list[0].kwargs["message_origin"] == "scheduled_initiator"
    assert history.save_message.await_args_list[0].kwargs["exchange_id"] == "exchange-1"
    assert exchange_store.mark_exchange_started.await_args.kwargs["responder_scheduled_at"] == datetime(
        2026,
        4,
        20,
        19,
        13,
        tzinfo=UTC,
    )


@pytest.mark.asyncio
async def test_orchestrator_sends_due_responder_and_completes_exchange():
    """Проверяет, что ответчик отвечает только после наступления due времени."""
    initiator_client = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(id=501)))
    responder_client = SimpleNamespace(send_message=AsyncMock())
    exchange_store = SimpleNamespace(
        get_due_started_exchange=AsyncMock(
            return_value={
                "exchange_id": "exchange-1",
                "initiator_bot_id": "anna",
                "responder_bot_id": "mike",
                "topic": "Где поесть суп?",
                "question_text": "Кто знает место с хорошим супом?",
                "initiator_message_id": 501,
            }
        ),
        get_exchange_by_window_key=AsyncMock(),
        mark_exchange_completed=AsyncMock(),
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
        prompt_composer=SimpleNamespace(compose=AsyncMock(return_value="system-reply")),
        gemini_client=SimpleNamespace(generate_reply=AsyncMock(return_value="Мне нравится Pho 54.")),
        history=history,
        exchange_store=exchange_store,
        group_target="@chat",
        now_provider=lambda: datetime(2026, 4, 20, 19, 14, tzinfo=UTC),
    )

    started = await orchestrator.run_once()

    assert started is True
    responder_client.send_message.assert_awaited_once_with("@chat", "Мне нравится Pho 54.", reply_to=501)
    exchange_store.mark_exchange_completed.assert_awaited_once_with("exchange-1")
    assert history.save_message.await_count == 1
    assert history.save_message.await_args.kwargs["message_origin"] == "scheduled_responder"


@pytest.mark.asyncio
async def test_orchestrator_creates_only_one_exchange_per_window():
    """Проверяет, что в одном активном окне не создаётся второй exchange."""
    exchange_store = SimpleNamespace(
        get_due_started_exchange=AsyncMock(return_value=None),
        get_exchange_by_window_key=AsyncMock(
            return_value={
                "exchange_id": "exchange-1",
                "status": "completed",
            }
        ),
        create_exchange=AsyncMock(),
    )
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
        exchange_store=exchange_store,
        group_target="@chat",
        active_windows_utc=["19-20"],
        now_provider=lambda: datetime(2026, 4, 20, 19, 10, tzinfo=UTC),
    )

    started = await orchestrator.run_once()

    assert started is False
    exchange_store.create_exchange.assert_not_called()


@pytest.mark.asyncio
async def test_orchestrator_resolves_group_target_per_sending_client():
    """Проверяет отдельный резолв entity группы для отправителя вопроса."""
    initiator_client = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(id=501)))
    responder_client = SimpleNamespace(send_message=AsyncMock())
    exchange_store = SimpleNamespace(
        get_due_started_exchange=AsyncMock(return_value=None),
        get_exchange_by_window_key=AsyncMock(return_value=None),
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
    resolved_targets: list[object] = [object(), object()]
    resolve_group_target = AsyncMock(side_effect=resolved_targets)

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
        group_chat_id=-100123,
        resolve_group_target=resolve_group_target,
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
    assert resolve_group_target.await_count == 1
    initiator_client.send_message.assert_awaited_once_with(resolved_targets[0], "Кто знает место с хорошим супом?")
    responder_client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestrator_skips_when_bot_is_busy():
    """Проверяет, что planned exchange не стартует, если инициатор занят."""
    exchange_store = SimpleNamespace(
        get_due_started_exchange=AsyncMock(return_value=None),
        get_exchange_by_window_key=AsyncMock(return_value=None),
        get_recent_pairs=AsyncMock(return_value=[]),
        get_recent_topic_keys=AsyncMock(return_value=set()),
        get_recent_questions=AsyncMock(return_value=[]),
        get_recent_question_signatures=AsyncMock(return_value=set()),
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
        active_windows_utc=["19-20"],
        now_provider=lambda: datetime(2026, 4, 20, 19, 5, tzinfo=UTC),
        initiator_offset_minutes=(5, 5),
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
    exchange_store.mark_exchange_skipped.assert_not_called()


class _ScheduledSlot:
    """Управляемый async context manager для scheduled slot."""

    def __init__(self, acquired: bool) -> None:
        self.acquired = acquired

    async def __aenter__(self):
        return self.acquired

    async def __aexit__(self, exc_type, exc, tb):
        return False
