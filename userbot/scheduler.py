"""Планировщик задач APScheduler и логика 30-минутных сессий разговора."""

import asyncio
import logging
import random
from datetime import datetime
from pathlib import Path


logger = logging.getLogger(__name__)


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
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
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


class ConversationSession:
    """Управляет сессией разговора на одну тему с ограниченной длительностью."""

    def __init__(self, duration_minutes: int = 30) -> None:
        """
        Инициализирует сессию разговора.

        Args:
            duration_minutes: Длительность сессии в минутах (по умолчанию 30).
        """
        self.duration_minutes = duration_minutes
        self.current_topic: str | None = None
        self._start_time: datetime | None = None
        self._active: bool = False

    def start(self, topic: str) -> None:
        """
        Запускает сессию на заданную тему.

        Args:
            topic: Тема разговора для данной сессии.
        """
        self.current_topic = topic
        self._start_time = datetime.now()
        self._active = True
        logger.info("Сессия разговора запущена: topic=%s, duration_minutes=%s", topic, self.duration_minutes)

    def stop(self) -> None:
        """Досрочно останавливает текущую сессию."""
        logger.info("Сессия разговора остановлена: topic=%s", self.current_topic)
        self.current_topic = None
        self._start_time = None
        self._active = False

    def is_active(self) -> bool:
        """
        Проверяет, активна ли сессия (запущена и не истекла по времени).

        Returns:
            True если сессия активна, иначе False.
        """
        if not self._active or self._start_time is None:
            return False

        elapsed_seconds = (datetime.now() - self._start_time).total_seconds()
        if elapsed_seconds >= self.duration_minutes * 60:
            logger.info("Сессия разговора истекла по времени")
            self.stop()
            return False
        return True
