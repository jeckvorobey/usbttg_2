"""Тесты persisted state для orchestrator."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from userbot.exchange_store import ExchangeStore, normalize_signature


@pytest_asyncio.fixture
async def exchange_store():
    """Создаёт in-memory exchange store."""
    store = ExchangeStore(":memory:")
    await store.init_db()
    try:
        yield store
    finally:
        await store.close()


def test_normalize_signature_compacts_text():
    """Проверяет нормализацию сигнатуры для anti-repeat."""
    assert normalize_signature("  Один   и тот же   вопрос?! ") == "один и тот же вопрос"


async def test_exchange_store_persists_recent_pairs_and_topics(exchange_store):
    """Проверяет persisted pair/topic state для orchestrator."""
    exchange_id = await exchange_store.create_exchange(
        initiator_bot_id="anna",
        responder_bot_id="mike",
        topic="Где есть суп?",
    )
    await exchange_store.mark_exchange_started(exchange_id, initiator_message_id=55, question_signature="Кто знает место с супом?")
    await exchange_store.mark_exchange_completed(exchange_id)

    pairs = await exchange_store.get_recent_pairs(1)
    topics = await exchange_store.get_recent_topic_keys(since=timedelta(days=1))
    signatures = await exchange_store.get_recent_question_signatures(since=timedelta(days=1))

    assert pairs == [("anna", "mike")]
    assert "где есть суп" in topics
    assert "кто знает место с супом" in signatures


async def test_exchange_store_marks_skipped_exchange(exchange_store):
    """Проверяет сохранение skip reason."""
    exchange_id = await exchange_store.create_exchange(
        initiator_bot_id="anna",
        responder_bot_id="mike",
        topic="Тема",
    )
    await exchange_store.mark_exchange_skipped(exchange_id, "initiator_busy")

    questions = await exchange_store.get_recent_questions(since=timedelta(days=1))

    assert questions == []


async def test_exchange_store_tracks_window_and_due_stages(exchange_store):
    """Проверяет хранение окна и отложенных стадий exchange."""
    exchange_id = await exchange_store.create_exchange(
        initiator_bot_id="anna",
        responder_bot_id="mike",
        topic="Где лучше жить у моря?",
        window_key="2026-04-20T19:10-12",
        initiator_scheduled_at=datetime(2026, 4, 20, 19, 5, tzinfo=UTC),
    )

    planned = await exchange_store.get_exchange_by_window_key("2026-04-20T19:10-12")
    due_planned = await exchange_store.get_due_planned_exchange(now=datetime(2026, 4, 20, 19, 6, tzinfo=UTC))

    assert planned is not None
    assert planned["exchange_id"] == exchange_id
    assert due_planned is not None
    assert due_planned["exchange_id"] == exchange_id

    await exchange_store.mark_exchange_started(
        exchange_id,
        initiator_message_id=55,
        question_text="Кто где сейчас живёт ближе к морю?",
        question_signature="Кто где сейчас живёт ближе к морю?",
        responder_scheduled_at=datetime(2026, 4, 20, 19, 14, tzinfo=UTC),
    )

    due_started = await exchange_store.get_due_started_exchange(now=datetime(2026, 4, 20, 19, 15, tzinfo=UTC))

    assert due_started is not None
    assert due_started["exchange_id"] == exchange_id
    assert due_started["initiator_message_id"] == 55
    assert due_started["question_text"] == "Кто где сейчас живёт ближе к морю?"
