"""Runtime-модели для swarm-архитектуры."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(slots=True)
class SwarmBotProfile:
    """Профиль одного бота в swarm-режиме."""

    id: str
    session_string: str
    persona_file: str
    enabled: bool = True
    temperature: float = 0.9
    session_env: str | None = None
    telegram_user_id: int | None = None
    reconnect_attempts: int = 0


@dataclass(slots=True)
class BotRuntimeState:
    """Текущее runtime-состояние одного бота."""

    bot_id: str
    status: str = "created"
    last_started_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error_text: str | None = None
    reconnect_attempts: int = 0

    def mark_started(self) -> None:
        """Фиксирует успешный запуск клиента."""
        self.status = "running"
        self.last_started_at = datetime.now(UTC)
        self.last_error_at = None
        self.last_error_text = None
        self.reconnect_attempts = 0

    def mark_error(self, error_text: str) -> None:
        """Фиксирует ошибку клиента."""
        self.status = "reconnecting"
        self.last_error_at = datetime.now(UTC)
        self.last_error_text = error_text
        self.reconnect_attempts += 1

    def mark_failed(self, error_text: str) -> None:
        """Фиксирует фатальную ошибку и исключение бота из активного пула."""
        self.status = "error"
        self.last_error_at = datetime.now(UTC)
        self.last_error_text = error_text

    def mark_stopped(self) -> None:
        """Фиксирует штатную остановку клиента."""
        self.status = "stopped"


@dataclass(slots=True)
class ExchangeDecision:
    """Результат выбора exchange orchestrator-ом."""

    initiator: SwarmBotProfile
    responder: SwarmBotProfile
    topic: str
    topic_key: str
    recent_questions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ExchangePlan:
    """План одного scheduled-обмена между двумя ботами."""

    exchange_id: str
    initiator_bot_id: str
    responder_bot_id: str
    topic: str
    max_turns: int = 2
