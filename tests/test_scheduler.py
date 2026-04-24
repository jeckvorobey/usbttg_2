"""Тесты для утилит планировщика swarm-режима."""

import os
import tempfile
from datetime import UTC, datetime

import pytest

from userbot.scheduler import TopicSelector, is_within_windows_utc


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


def test_is_within_windows_utc_matches_simple_window():
    """Проверяет попадание времени в одно из UTC-окон."""
    assert is_within_windows_utc(["10-12", "16-18"], datetime(2026, 4, 10, 11, 0, tzinfo=UTC)) is True
    assert is_within_windows_utc(["10-12", "16-18"], datetime(2026, 4, 10, 14, 0, tzinfo=UTC)) is False


def test_is_within_windows_utc_supports_midnight_crossing_window():
    """Проверяет UTC-окно, которое пересекает полночь."""
    assert is_within_windows_utc(["23-3"], datetime(2026, 4, 10, 1, 0, tzinfo=UTC)) is True
    assert is_within_windows_utc(["23-3"], datetime(2026, 4, 10, 12, 0, tzinfo=UTC)) is False


def test_is_within_windows_utc_empty_list_is_always_open():
    """Проверяет, что пустой список окон трактуется как круглосуточно активный."""
    assert is_within_windows_utc([], datetime(2026, 4, 10, 12, 0, tzinfo=UTC)) is True


def test_is_within_windows_utc_same_start_and_end_is_always_open():
    """Проверяет, что окно с одинаковыми start/end трактуется как круглосуточное."""
    assert is_within_windows_utc(["5-5"], datetime(2026, 4, 10, 1, 0, tzinfo=UTC)) is True
