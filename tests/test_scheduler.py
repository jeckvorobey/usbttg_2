"""Тесты для планировщика и логики сессий разговора."""

import os
import tempfile
from datetime import UTC, datetime

import pytest

from userbot.scheduler import ConversationSession, TopicSelector, is_dnd_active_utc


async def test_topic_selector_returns_topic_from_list():
    """Проверяет, что выбранная тема входит в список тем из файла."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("Любимые фильмы\nПланы на выходные\nЕда и рецепты\n")
        tmp_path = f.name

    try:
        selector = TopicSelector(topics_path=tmp_path)
        await selector.load()
        topic = await selector.pick_random()
        assert topic in ["Любимые фильмы", "Планы на выходные", "Еда и рецепты"]
    finally:
        os.unlink(tmp_path)


async def test_topic_selector_loads_all_topics():
    """Проверяет, что все темы из файла загружаются в список."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("Тема А\nТема Б\nТема В\n")
        tmp_path = f.name

    try:
        selector = TopicSelector(topics_path=tmp_path)
        await selector.load()
        assert len(selector.topics) == 3
    finally:
        os.unlink(tmp_path)


async def test_topic_selector_raises_on_empty_file():
    """Проверяет, что ValueError с понятным сообщением бросается при пустом файле."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("")
        tmp_path = f.name

    try:
        selector = TopicSelector(topics_path=tmp_path)
        await selector.load()
        with pytest.raises(ValueError, match="Список тем пуст"):
            await selector.pick_random()
    finally:
        os.unlink(tmp_path)


async def test_topic_selector_ignores_comment_lines():
    """Проверяет, что строки-комментарии не попадают в список тем."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("# Список тем\nТема 1\n# Комментарий\nТема 2\n")
        tmp_path = f.name

    try:
        selector = TopicSelector(topics_path=tmp_path)
        await selector.load()
        assert len(selector.topics) == 2
        assert "# Список тем" not in selector.topics
    finally:
        os.unlink(tmp_path)


def test_session_not_active_by_default():
    """Проверяет, что новая сессия неактивна."""
    session = ConversationSession(duration_minutes=30)
    assert session.is_active() is False


def test_session_active_after_start():
    """Проверяет, что сессия становится активной после вызова start()."""
    session = ConversationSession(duration_minutes=30)
    session.start(topic="Любимые фильмы")
    assert session.is_active() is True


def test_session_stores_current_topic():
    """Проверяет, что сессия хранит текущую тему после запуска."""
    session = ConversationSession(duration_minutes=30)
    session.start(topic="Планы на выходные")
    assert session.current_topic == "Планы на выходные"


def test_session_has_correct_duration():
    """Проверяет, что длительность сессии задаётся корректно."""
    session = ConversationSession(duration_minutes=30)
    assert session.duration_minutes == 30


def test_session_inactive_after_stop():
    """Проверяет, что сессия становится неактивной после вызова stop()."""
    session = ConversationSession(duration_minutes=30)
    session.start(topic="Тема")
    session.stop()
    assert session.is_active() is False


def test_remaining_minutes_returns_none_before_start():
    """Проверяет, что remaining_minutes возвращает None до запуска сессии."""
    session = ConversationSession(duration_minutes=30)
    assert session.remaining_minutes() is None


def test_remaining_minutes_returns_none_after_stop():
    """Проверяет, что remaining_minutes возвращает None после остановки сессии."""
    session = ConversationSession(duration_minutes=30)
    session.start(topic="Тема")
    session.stop()
    assert session.remaining_minutes() is None


def test_remaining_minutes_returns_value_after_start():
    """Проверяет, что remaining_minutes возвращает неотрицательное число сразу после запуска."""
    session = ConversationSession(duration_minutes=10)
    session.start(topic="Тема")
    remaining = session.remaining_minutes()
    assert remaining is not None
    assert 0 <= remaining <= 10


def test_remaining_minutes_returns_zero_when_expired():
    """Проверяет, что remaining_minutes возвращает 0 для просроченной сессии."""
    from datetime import timedelta

    session = ConversationSession(duration_minutes=1)
    session.start(topic="Тема")
    # Сдвигаем время старта в прошлое на 2 минуты
    session._start_time = session._start_time - timedelta(minutes=2)
    remaining = session.remaining_minutes()
    assert remaining == 0


def test_is_dnd_active_utc_for_same_day_interval():
    """Проверяет UTC-интервал DND внутри одних суток."""
    assert is_dnd_active_utc("8-12", datetime(2026, 4, 10, 9, 0, tzinfo=UTC)) is True
    assert is_dnd_active_utc("8-12", datetime(2026, 4, 10, 13, 0, tzinfo=UTC)) is False


def test_is_dnd_active_utc_for_interval_across_midnight():
    """Проверяет UTC-интервал DND с переходом через полночь."""
    assert is_dnd_active_utc("23-7", datetime(2026, 4, 10, 23, 30, tzinfo=UTC)) is True
    assert is_dnd_active_utc("23-7", datetime(2026, 4, 10, 6, 30, tzinfo=UTC)) is True
    assert is_dnd_active_utc("23-7", datetime(2026, 4, 10, 12, 0, tzinfo=UTC)) is False


def test_is_dnd_active_utc_for_full_day_interval():
    """Проверяет, что одинаковые часы означают круглосуточный DND."""
    assert is_dnd_active_utc("5-5", datetime(2026, 4, 10, 1, 0, tzinfo=UTC)) is True
    assert is_dnd_active_utc("5-5", datetime(2026, 4, 10, 18, 0, tzinfo=UTC)) is True
