"""Модуль хранения истории диалогов в SQLite через aiosqlite."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite


logger = logging.getLogger(__name__)


def _to_utc_sqlite_timestamp(value: datetime) -> str:
    """Приводит datetime к UTC-строке в формате SQLite."""
    if value.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or UTC
        value = value.replace(tzinfo=local_tz)
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


class MessageHistory:
    """Хранит историю сообщений каждого пользователя в SQLite базе данных."""

    def __init__(self, db_path: str) -> None:
        """
        Инициализирует хранилище истории.

        Args:
            db_path: Путь к файлу SQLite или ':memory:' для in-memory базы данных.
        """
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def init_db(self) -> None:
        """Создаёт таблицу messages в базе данных, если она не существует."""
        logger.info("Инициализация базы истории сообщений")
        connection = await self._get_connection()
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER,
                bot_id TEXT,
                exchange_id TEXT,
                message_origin TEXT,
                reply_to_message_id INTEGER,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await connection.commit()
        await self._ensure_column(connection, "chat_id", "INTEGER")
        await self._ensure_column(connection, "bot_id", "TEXT")
        await self._ensure_column(connection, "exchange_id", "TEXT")
        await self._ensure_column(connection, "message_origin", "TEXT")
        await self._ensure_column(connection, "reply_to_message_id", "INTEGER")
        logger.info("Таблица истории сообщений готова")

    async def save_message(
        self,
        user_id: int,
        role: str,
        text: str,
        chat_id: int | None = None,
        bot_id: str | None = None,
        exchange_id: str | None = None,
        message_origin: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> None:
        """
        Сохраняет сообщение в историю диалога.

        Args:
            user_id: Telegram ID пользователя.
            role: Роль отправителя — 'user' или 'assistant'.
            text: Текст сообщения.
            chat_id: Telegram ID чата (группы).
        """
        logger.info(
            "Сохранение сообщения в историю для user_id=%s, role=%s, длина=%s",
            user_id,
            role,
            len(text),
        )
        connection = await self._get_connection()
        created_at = _to_utc_sqlite_timestamp(datetime.now(UTC))
        await connection.execute(
            """
            INSERT INTO messages (
                user_id,
                chat_id,
                bot_id,
                exchange_id,
                message_origin,
                reply_to_message_id,
                role,
                text,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                chat_id,
                bot_id,
                exchange_id,
                message_origin,
                reply_to_message_id,
                role,
                text,
                created_at,
            ),
        )
        await connection.commit()
        logger.info("Сообщение сохранено в историю для user_id=%s", user_id)

    async def get_history(
        self, user_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        """
        Возвращает историю сообщений для указанного пользователя.

        Args:
            user_id: Telegram ID пользователя.
            limit: Максимальное количество возвращаемых сообщений (от новых к старым).

        Returns:
            Список словарей с ключами 'role' и 'text', упорядоченных по времени.
        """
        logger.info("Загрузка истории сообщений для user_id=%s с limit=%s", user_id, limit)
        connection = await self._get_connection()
        async with connection.execute(
            """
            SELECT role, text
            FROM (
                SELECT role, text, id
                FROM messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) recent_messages
            ORDER BY id ASC
            """,
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        messages = [{"role": row[0], "text": row[1]} for row in rows]
        logger.info("Загружена история сообщений для user_id=%s: %s записей", user_id, len(messages))
        return messages

    async def get_session_history(
        self,
        chat_id: int | None,
        session_start: datetime | None = None,
        limit: int = 50,
        bot_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Возвращает историю сообщений всех участников чата за текущую сессию.

        Args:
            chat_id: Telegram ID чата. Если None — возвращает пустой список.
            session_start: Начало сессии; если задано, фильтрует сообщения по времени.
            limit: Максимальное количество возвращаемых сообщений.

        Returns:
            Список словарей с ключами 'role' и 'text', упорядоченных по времени.
        """
        if chat_id is None:
            return []

        logger.info(
            "Загрузка истории сессии для chat_id=%s, session_start=%s, limit=%s",
            chat_id,
            session_start,
            limit,
        )
        connection = await self._get_connection()

        params: list[Any] = [chat_id]
        where_parts = ["chat_id = ?"]
        if bot_id is not None:
            where_parts.append("bot_id = ?")
            params.append(bot_id)

        if session_start is not None:
            session_start_str = _to_utc_sqlite_timestamp(session_start)
            params.append(session_start_str)
            params.append(limit)
            async with connection.execute(
                f"""
                SELECT role, text, bot_id, exchange_id, message_origin, reply_to_message_id
                FROM messages
                WHERE {' AND '.join(where_parts)} AND datetime(created_at) >= datetime(?)
                ORDER BY id ASC
                LIMIT ?
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            params.append(limit)
            async with connection.execute(
                f"""
                SELECT role, text, bot_id, exchange_id, message_origin, reply_to_message_id FROM (
                    SELECT role, text, bot_id, exchange_id, message_origin, reply_to_message_id, id FROM messages
                    WHERE {' AND '.join(where_parts)}
                    ORDER BY id DESC
                    LIMIT ?
                ) sub
                ORDER BY id ASC
                """,
                params,
            ) as cursor:
                rows = await cursor.fetchall()

        messages = [
            {
                "role": row[0],
                "text": row[1],
                "bot_id": row[2],
                "exchange_id": row[3],
                "message_origin": row[4],
                "reply_to_message_id": row[5],
            }
            for row in rows
        ]
        logger.info(
            "Загружена история сессии для chat_id=%s: %s записей", chat_id, len(messages)
        )
        return messages

    async def close(self) -> None:
        """Закрывает открытое SQLite-соединение."""
        if self._connection is None:
            return

        await self._connection.close()
        self._connection = None

    async def _get_connection(self) -> aiosqlite.Connection:
        """Возвращает общее подключение к SQLite, необходимое для :memory:."""
        if self._connection is None:
            self._ensure_parent_dir()
            logger.info("Открытие SQLite-соединения: %s", self.db_path)
            self._connection = await aiosqlite.connect(self.db_path)
        return self._connection

    def _ensure_parent_dir(self) -> None:
        """Создаёт директорию для файла БД, если это файловый путь."""
        if self.db_path == ":memory:":
            return

        parent = Path(self.db_path).parent
        if str(parent) and str(parent) != ".":
            parent.mkdir(parents=True, exist_ok=True)

    async def _ensure_column(self, connection: aiosqlite.Connection, column_name: str, column_type: str) -> None:
        """Добавляет колонку в messages; пропускает только duplicate-column."""
        try:
            await connection.execute(f"ALTER TABLE messages ADD COLUMN {column_name} {column_type}")
            await connection.commit()
        except aiosqlite.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
