"""Модуль хранения истории диалогов в SQLite через aiosqlite."""

import logging
from datetime import datetime
from typing import Any

import aiosqlite


logger = logging.getLogger(__name__)


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
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await connection.commit()
        try:
            await connection.execute("ALTER TABLE messages ADD COLUMN chat_id INTEGER")
            await connection.commit()
        except Exception:
            pass  # колонка уже существует
        logger.info("Таблица истории сообщений готова")

    async def save_message(
        self, user_id: int, role: str, text: str, chat_id: int | None = None
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
        await connection.execute(
            "INSERT INTO messages (user_id, chat_id, role, text) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, role, text),
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

        if session_start is not None:
            session_start_str = session_start.strftime("%Y-%m-%d %H:%M:%S")
            async with connection.execute(
                """
                SELECT role, text FROM messages
                WHERE chat_id = ? AND created_at >= ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (chat_id, session_start_str, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with connection.execute(
                """
                SELECT role, text FROM (
                    SELECT role, text, id FROM messages
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) sub
                ORDER BY id ASC
                """,
                (chat_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()

        messages = [{"role": row[0], "text": row[1]} for row in rows]
        logger.info(
            "Загружена история сессии для chat_id=%s: %s записей", chat_id, len(messages)
        )
        return messages

    async def _get_connection(self) -> aiosqlite.Connection:
        """Возвращает общее подключение к SQLite, необходимое для :memory:."""
        if self._connection is None:
            logger.info("Открытие SQLite-соединения: %s", self.db_path)
            self._connection = await aiosqlite.connect(self.db_path)
        return self._connection
