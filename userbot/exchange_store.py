"""SQLite-хранилище scheduled exchange и persisted state orchestrator."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite


logger = logging.getLogger(__name__)


def normalize_signature(value: str) -> str:
    """Нормализует текст для дедупликации тем и вопросов."""
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    normalized = re.sub(r"[^\w\s]+", "", normalized)
    return normalized.strip()


class ExchangeStore:
    """Хранит exchange, их статусы и persisted state для anti-repeat."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def init_db(self) -> None:
        """Создаёт таблицу scheduled_exchanges, если она ещё не существует."""
        connection = await self._get_connection()
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_exchanges (
                exchange_id TEXT PRIMARY KEY,
                initiator_bot_id TEXT NOT NULL,
                responder_bot_id TEXT NOT NULL,
                pair_key TEXT NOT NULL,
                topic TEXT NOT NULL,
                topic_key TEXT NOT NULL,
                question_text TEXT,
                question_signature TEXT,
                initiator_message_id INTEGER,
                status TEXT NOT NULL DEFAULT 'planned',
                skip_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP
            )
            """
        )
        await connection.commit()
        await self._ensure_column(connection, "pair_key", "TEXT")
        await self._ensure_column(connection, "topic_key", "TEXT")
        await self._ensure_column(connection, "question_text", "TEXT")
        await self._ensure_column(connection, "question_signature", "TEXT")
        await self._ensure_column(connection, "initiator_message_id", "INTEGER")
        await self._ensure_column(connection, "skip_reason", "TEXT")
        await self._ensure_column(connection, "started_at", "TIMESTAMP")
        await self._ensure_column(connection, "completed_at", "TIMESTAMP")
        logger.info("Таблица scheduled_exchanges готова")

    async def create_exchange(
        self,
        *,
        initiator_bot_id: str,
        responder_bot_id: str,
        topic: str,
        topic_key: str | None = None,
    ) -> str:
        """Создаёт запись planned exchange и возвращает её идентификатор."""
        exchange_id = str(uuid.uuid4())
        normalized_topic_key = topic_key or normalize_signature(topic)
        pair_key = self.build_pair_key(initiator_bot_id, responder_bot_id)
        connection = await self._get_connection()
        await connection.execute(
            """
            INSERT INTO scheduled_exchanges (
                exchange_id,
                initiator_bot_id,
                responder_bot_id,
                pair_key,
                topic,
                topic_key,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'planned')
            """,
            (exchange_id, initiator_bot_id, responder_bot_id, pair_key, topic, normalized_topic_key),
        )
        await connection.commit()
        logger.info(
            "Создан planned exchange: exchange_id=%s initiator=%s responder=%s topic_key=%s",
            exchange_id,
            initiator_bot_id,
            responder_bot_id,
            normalized_topic_key,
        )
        return exchange_id

    async def mark_exchange_started(
        self,
        exchange_id: str,
        *,
        initiator_message_id: int | None = None,
        question_text: str | None = None,
        question_signature: str | None = None,
    ) -> None:
        """Помечает exchange как начатый."""
        connection = await self._get_connection()
        await connection.execute(
            """
            UPDATE scheduled_exchanges
            SET status = 'started',
                initiator_message_id = ?,
                question_text = ?,
                question_signature = ?,
                started_at = CURRENT_TIMESTAMP
            WHERE exchange_id = ?
            """,
            (
                initiator_message_id,
                question_text,
                normalize_signature(question_signature) if question_signature else None,
                exchange_id,
            ),
        )
        await connection.commit()
        logger.info("Exchange помечен как started: exchange_id=%s", exchange_id)

    async def mark_exchange_completed(self, exchange_id: str) -> None:
        """Помечает exchange как завершённый."""
        connection = await self._get_connection()
        await connection.execute(
            """
            UPDATE scheduled_exchanges
            SET status = 'completed',
                completed_at = CURRENT_TIMESTAMP
            WHERE exchange_id = ?
            """,
            (exchange_id,),
        )
        await connection.commit()
        logger.info("Exchange помечен как completed: exchange_id=%s", exchange_id)

    async def mark_exchange_skipped(self, exchange_id: str, skip_reason: str) -> None:
        """Помечает exchange как пропущенный."""
        connection = await self._get_connection()
        await connection.execute(
            """
            UPDATE scheduled_exchanges
            SET status = 'skipped',
                skip_reason = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE exchange_id = ?
            """,
            (skip_reason, exchange_id),
        )
        await connection.commit()
        logger.info("Exchange пропущен: exchange_id=%s reason=%s", exchange_id, skip_reason)

    async def get_recent_pairs(self, limit: int) -> list[tuple[str, str]]:
        """Возвращает последние completed/started пары из persisted state."""
        if limit <= 0:
            return []
        connection = await self._get_connection()
        async with connection.execute(
            """
            SELECT initiator_bot_id, responder_bot_id
            FROM scheduled_exchanges
            WHERE status IN ('started', 'completed')
            ORDER BY COALESCE(completed_at, started_at, created_at) DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        pairs = [(row[0], row[1]) for row in rows]
        logger.info("Загружены последние пары exchange: count=%s", len(pairs))
        return pairs

    async def get_recent_topic_keys(self, *, since: timedelta) -> set[str]:
        """Возвращает недавно использованные topic_key."""
        threshold = self._threshold_timestamp(since)
        connection = await self._get_connection()
        async with connection.execute(
            """
            SELECT DISTINCT topic_key
            FROM scheduled_exchanges
            WHERE status IN ('started', 'completed') AND datetime(COALESCE(started_at, created_at)) >= datetime(?)
            """,
            (threshold,),
        ) as cursor:
            rows = await cursor.fetchall()
        topic_keys = {row[0] for row in rows if isinstance(row[0], str)}
        logger.info("Загружены recent topic_key: count=%s since=%s", len(topic_keys), threshold)
        return topic_keys

    async def get_recent_question_signatures(self, *, since: timedelta) -> set[str]:
        """Возвращает сигнатуры недавно использованных вопросов."""
        threshold = self._threshold_timestamp(since)
        connection = await self._get_connection()
        async with connection.execute(
            """
            SELECT DISTINCT question_signature
            FROM scheduled_exchanges
            WHERE question_signature IS NOT NULL
              AND status IN ('started', 'completed')
              AND datetime(COALESCE(started_at, created_at)) >= datetime(?)
            """,
            (threshold,),
        ) as cursor:
            rows = await cursor.fetchall()
        signatures = {row[0] for row in rows if isinstance(row[0], str)}
        logger.info("Загружены recent question_signature: count=%s since=%s", len(signatures), threshold)
        return signatures

    async def get_recent_questions(self, *, since: timedelta, limit: int = 10) -> list[str]:
        """Возвращает последние вопросы для prompt context."""
        threshold = self._threshold_timestamp(since)
        connection = await self._get_connection()
        async with connection.execute(
            """
            SELECT COALESCE(question_text, topic)
            FROM scheduled_exchanges
            WHERE status IN ('started', 'completed')
              AND datetime(COALESCE(started_at, created_at)) >= datetime(?)
            ORDER BY COALESCE(started_at, created_at) DESC
            LIMIT ?
            """,
            (threshold, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        questions = [row[0] for row in rows if isinstance(row[0], str)]
        logger.info("Загружены recent questions для контекста: count=%s", len(questions))
        return questions

    async def close(self) -> None:
        """Закрывает SQLite-соединение."""
        if self._connection is None:
            return
        await self._connection.close()
        self._connection = None

    async def _get_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            self._ensure_parent_dir()
            self._connection = await aiosqlite.connect(self.db_path)
        return self._connection

    def _ensure_parent_dir(self) -> None:
        """Создаёт директорию для файловой SQLite базы."""
        if self.db_path == ":memory:":
            return
        parent = Path(self.db_path).parent
        if str(parent) and str(parent) != ".":
            parent.mkdir(parents=True, exist_ok=True)

    async def _ensure_column(self, connection: aiosqlite.Connection, column_name: str, column_type: str) -> None:
        """Добавляет колонку, если её ещё нет; пропускает только duplicate-column."""
        try:
            await connection.execute(f"ALTER TABLE scheduled_exchanges ADD COLUMN {column_name} {column_type}")
            await connection.commit()
        except aiosqlite.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    @staticmethod
    def build_pair_key(initiator_bot_id: str, responder_bot_id: str) -> str:
        """Строит persisted ключ пары A->B."""
        return f"{initiator_bot_id}->{responder_bot_id}"

    @staticmethod
    def _threshold_timestamp(since: timedelta) -> str:
        """Возвращает UTC timestamp для SQL-фильтра recent-запросов."""
        return (datetime.now(UTC) - since).strftime("%Y-%m-%d %H:%M:%S")
