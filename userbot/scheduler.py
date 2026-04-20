"""Планировщик задач APScheduler и вспомогательные утилиты swarm-режима."""

import logging
import random
from datetime import UTC, datetime
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
