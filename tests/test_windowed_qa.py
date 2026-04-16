"""Тесты режима windowed_qa."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from userbot.handlers import WhitelistFilter
from userbot.windowed_qa import ExchangeTracker, WindowSchedule, build_responder_handler, initiator_job


def dt(hour: int, minute: int = 0) -> datetime:
    """Создаёт UTC datetime для фиксированной даты."""
    return datetime(2026, 4, 15, hour, minute, tzinfo=UTC)


def dt_day(day: int, hour: int, minute: int = 0) -> datetime:
    """Создаёт UTC datetime для выбранного дня фиксированного месяца."""
    return datetime(2026, 4, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (dt(9, 59), None),
        (dt(10, 0), "morning"),
        (dt(10, 59), "morning"),
        (dt(11, 0), None),
        (dt(15, 59), None),
        (dt(16, 0), "evening"),
        (dt(17, 59), "evening"),
        (dt(18, 0), None),
    ],
)
def test_window_schedule_current_window_boundaries(moment: datetime, expected: str | None):
    """Проверяет границы UTC-окон."""
    schedule = WindowSchedule()

    window = schedule.current_window_utc(moment)

    assert (window.name if window else None) == expected


@pytest.mark.parametrize(
    ("moment", "expected_start", "expected_end"),
    [
        (dt_day(15, 22, 59), None, None),
        (dt_day(15, 23, 0), dt_day(15, 23), dt_day(16, 3)),
        (dt_day(16, 2, 59), dt_day(15, 23), dt_day(16, 3)),
        (dt_day(16, 3, 0), None, None),
    ],
)
def test_window_schedule_supports_midnight_crossing_window(
    moment: datetime,
    expected_start: datetime | None,
    expected_end: datetime | None,
):
    """Проверяет UTC-окно, которое пересекает полночь."""
    schedule = WindowSchedule(morning_window_utc=(10, 11), evening_window_utc=(23, 3))

    window = schedule.current_window_utc(moment)

    if expected_start is None:
        assert window is None
    else:
        assert window is not None
        assert window.name == "evening"
        assert window.start == expected_start
        assert window.end == expected_end


def test_window_schedule_supports_24_as_end_of_day():
    """Проверяет значение 24 как конец UTC-суток."""
    schedule = WindowSchedule(morning_window_utc=(10, 11), evening_window_utc=(23, 24))

    window = schedule.current_window_utc(dt_day(15, 23, 30))

    assert window is not None
    assert window.name == "evening"
    assert window.start == dt_day(15, 23)
    assert window.end == dt_day(16, 0)


def test_exchange_tracker_allows_one_exchange_per_window_and_resets_next_window():
    """Проверяет лимит одного обмена и независимость окон."""
    schedule = WindowSchedule()
    tracker = ExchangeTracker(max_exchanges_per_window=1)
    morning = schedule.current_window_utc(dt(10, 0))
    evening = schedule.current_window_utc(dt(16, 0))

    assert morning is not None
    assert evening is not None
    assert tracker.can_start(morning) is True
    tracker.mark_completed(morning)
    assert tracker.can_start(morning) is False
    assert tracker.can_start(evening) is True


@pytest.mark.asyncio
async def test_initiator_job_fires_once_after_offset():
    """Проверяет, что initiator отправляет вопрос один раз после offset."""
    settings = SimpleNamespace(group_chat_id=-1001, group_target=None)
    client = SimpleNamespace(send_message=AsyncMock())
    gemini = SimpleNamespace(start_topic=AsyncMock(return_value="Вопрос?"))
    topics = SimpleNamespace(pick_random=AsyncMock(return_value="Тема"))
    history = SimpleNamespace(save_message=AsyncMock())
    prompt_loader = SimpleNamespace(load=AsyncMock(side_effect=["system", "start_topic", "system", "start_topic"]))
    schedule = WindowSchedule(initiator_offset_minutes=(10, 10))
    tracker = ExchangeTracker()

    await initiator_job(settings, client, gemini, topics, history, prompt_loader, schedule, tracker, lambda: dt(10, 9))
    await initiator_job(settings, client, gemini, topics, history, prompt_loader, schedule, tracker, lambda: dt(10, 10))
    await initiator_job(settings, client, gemini, topics, history, prompt_loader, schedule, tracker, lambda: dt(10, 11))

    client.send_message.assert_awaited_once_with(-1001, "Вопрос?")
    gemini.start_topic.assert_awaited_once()
    history.save_message.assert_awaited_once_with(0, "assistant", "Вопрос?", chat_id=-1001)


@pytest.mark.asyncio
async def test_responder_handler_reacts_only_to_whitelisted_peer_once():
    """Проверяет whitelist и одноразовый ответ responder-а в окне."""
    settings = SimpleNamespace(
        group_chat_id=-1001,
        whitelist_user_ids="123",
        responder_delay_minutes=(0, 0),
        window_morning_utc=(10, 11),
        window_evening_utc=(16, 18),
        initiator_offset_minutes=(0, 0),
        max_exchanges_per_window=1,
    )
    gemini = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ"))
    history = SimpleNamespace(get_session_history=AsyncMock(return_value=[]), save_message=AsyncMock())
    prompt_loader = SimpleNamespace(load=AsyncMock(side_effect=["system", "reply"]))
    schedule = WindowSchedule()
    tracker = ExchangeTracker()

    handler = build_responder_handler(
        settings=settings,
        client=None,
        gemini=gemini,
        history=history,
        prompt_loader=prompt_loader,
        whitelist=WhitelistFilter({123}),
        schedule=schedule,
        tracker=tracker,
        sleep=lambda _seconds: None,
        now_utc_factory=lambda: dt(10, 0),
    )

    blocked = SimpleNamespace(sender_id=999, chat_id=-1001, raw_text="Вопрос", respond=AsyncMock())
    allowed = SimpleNamespace(sender_id=123, chat_id=-1001, raw_text="Вопрос", respond=AsyncMock())
    duplicate = SimpleNamespace(sender_id=123, chat_id=-1001, raw_text="Ещё", respond=AsyncMock())

    await handler(blocked)
    await handler(allowed)
    await handler(duplicate)

    blocked.respond.assert_not_called()
    allowed.respond.assert_awaited_once_with("Ответ")
    duplicate.respond.assert_not_called()
    gemini.generate_reply.assert_awaited_once()
    assert history.save_message.await_count == 2
