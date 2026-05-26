"""Планировщик задач APScheduler и вспомогательные утилиты swarm-режима."""

import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path


logger = logging.getLogger(__name__)


def _ensure_utc(moment: datetime) -> datetime:
    """Нормализует datetime к timezone-aware UTC."""
    if moment.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or UTC
        moment = moment.replace(tzinfo=local_tz)
    return moment.astimezone(UTC)


def is_within_windows_utc(active_windows_utc: list[str], now_utc: datetime | None = None) -> bool:
    """Проверяет, попадает ли текущее UTC-время хотя бы в одно активное окно."""
    if not active_windows_utc:
        return True

    current_time = now_utc or datetime.now(UTC)
    current_hour = current_time.hour

    for window in active_windows_utc:
        start_hour_str, end_hour_str = window.split("-", maxsplit=1)
        start_hour = int(start_hour_str)
        end_hour = int(end_hour_str)

        if start_hour == end_hour:
            return True
        if start_hour < end_hour and start_hour <= current_hour < end_hour:
            return True
        if start_hour > end_hour and (current_hour >= start_hour or current_hour < end_hour):
            return True

    return False


def pick_random_delay(
    minute_range: tuple[int, int],
    *,
    randint_provider: Callable[[int, int], int] | None = None,
) -> timedelta:
    """Выбирает случайную задержку с точностью до секунд внутри минутного диапазона."""
    provider = randint_provider or random.randint
    start_minutes, end_minutes = minute_range
    delay_seconds = provider(start_minutes * 60, end_minutes * 60)
    return timedelta(seconds=delay_seconds)


def pick_random_datetime(
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
    randint_provider: Callable[[int, int], int] | None = None,
) -> datetime:
    """Выбирает случайный момент внутри интервала, не уходя в прошлое относительно now."""
    provider = randint_provider or random.randint
    normalized_start = _ensure_utc(start)
    normalized_end = _ensure_utc(end)
    lower_bound = normalized_start
    if now is not None:
        lower_bound = max(lower_bound, _ensure_utc(now))
    if lower_bound >= normalized_end:
        return lower_bound

    available_seconds = max(int((normalized_end - lower_bound).total_seconds()) - 1, 0)
    offset_seconds = provider(0, available_seconds)
    return lower_bound + timedelta(seconds=offset_seconds)


class TopicSelector:
    """Выбирает случайную тему для разговора из файла тем."""

    def __init__(self, topics_path: str) -> None:
        """
        Инициализирует селектор тем.

        Args:
            topics_path: Путь к файлу topics.md со списком тем.
        """
        self.topics_path = topics_path
        self.topics: list[str] = []

    async def load(self) -> None:
        """
        Загружает список тем из файла.

        Строки начинающиеся на '#' считаются комментариями и игнорируются.
        Пустые строки также игнорируются.
        """
        path = Path(self.topics_path)
        logger.info("Загрузка тем разговора из %s", path)
        content = path.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines()]

        if "---" in lines:
            lines = lines[lines.index("---") + 1 :]

        self.topics = [
            line for line in lines if line and not line.startswith("#") and line != "---"
        ]
        logger.info("Темы разговора загружены: %s", len(self.topics))

    async def pick_random(self) -> str:
        """
        Выбирает случайную тему из загруженного списка.

        Returns:
            Строка с темой разговора.

        Raises:
            ValueError: Если список тем пуст (с сообщением 'Список тем пуст').
        """
        if not self.topics:
            logger.error("Выбор темы невозможен: список тем пуст")
            raise ValueError("Список тем пуст")
        topic = random.choice(self.topics)
        logger.info("Выбрана тема разговора: %s", topic)
        return topic
