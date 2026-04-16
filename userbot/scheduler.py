"""Планировщик задач APScheduler и логика сессий разговора."""

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


def is_dnd_active_utc(dnd_hours_utc: str | None, now_utc: datetime | None = None) -> bool:
    """Проверяет, попадает ли текущее UTC-время в интервал режима не беспокоить."""
    if not dnd_hours_utc:
        return False

    current_time = now_utc or datetime.now(UTC)
    start_hour_str, end_hour_str = dnd_hours_utc.split("-", maxsplit=1)
    start_hour = int(start_hour_str)
    end_hour = int(end_hour_str)
    current_hour = current_time.hour

    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= current_hour < end_hour
    return current_hour >= start_hour or current_hour < end_hour


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


class SilenceWatcher:
    """Отслеживает время последней активности в группе для обнаружения тишины."""

    def __init__(self) -> None:
        """Инициализирует трекер тишины с отдельной стартовой точкой процесса."""
        self._started_at: datetime = datetime.now(UTC)
        self._last_activity: datetime | None = None

    def update_last_activity(self, activity_at: datetime | None = None) -> None:
        """Фиксирует момент как время последней активности в группе."""
        moment = _ensure_utc(activity_at or datetime.now(UTC))
        if self._last_activity is None or moment.timestamp() >= self._last_activity.timestamp():
            self._last_activity = moment
        logger.debug("Активность в группе обновлена: %s", self._last_activity)

    def is_silence_exceeded(self, timeout_minutes: int) -> bool:
        """
        Проверяет, превышено ли время тишины.

        Args:
            timeout_minutes: Порог тишины в минутах.

        Returns:
            True если в группе нет активности дольше timeout_minutes минут
            или активности ещё не было вообще.
        """
        last_activity = self._last_activity or self._started_at
        elapsed_minutes = (datetime.now(UTC).timestamp() - last_activity.timestamp()) / 60
        exceeded = elapsed_minutes >= timeout_minutes
        logger.debug("Тишина: прошло %.1f мин из %s мин порога", elapsed_minutes, timeout_minutes)
        return exceeded


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
        self._start_time = datetime.now(UTC)
        self._active = True
        logger.info("Сессия разговора запущена: topic=%s, duration_minutes=%s", topic, self.duration_minutes)

    def stop(self) -> None:
        """Досрочно останавливает текущую сессию."""
        logger.info("Сессия разговора остановлена: topic=%s", self.current_topic)
        self.current_topic = None
        self._start_time = None
        self._active = False

    def remaining_minutes(self) -> int | None:
        """
        Возвращает оставшееся время сессии в минутах.

        Returns:
            Количество оставшихся полных минут, 0 если время истекло,
            или None если сессия не активна.
        """
        if not self._active or self._start_time is None:
            return None
        elapsed = (datetime.now(UTC) - self._start_time).total_seconds()
        remaining = self.duration_minutes * 60 - elapsed
        return max(0, int(remaining // 60))

    def is_active(self) -> bool:
        """
        Проверяет, активна ли сессия (запущена и не истекла по времени).

        Returns:
            True если сессия активна, иначе False.
        """
        if not self._active or self._start_time is None:
            return False

        elapsed_seconds = (datetime.now(UTC) - self._start_time).total_seconds()
        if elapsed_seconds >= self.duration_minutes * 60:
            logger.info("Сессия разговора истекла по времени")
            self.stop()
            return False
        return True
