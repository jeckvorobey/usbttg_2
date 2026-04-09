"""Модуль хранения истории диалогов в SQLite через aiosqlite."""

import logging
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
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await connection.commit()
        logger.info("Таблица истории сообщений готова")

    async def save_message(self, user_id: int, role: str, text: str) -> None:
        """
        Сохраняет сообщение в историю диалога.

        Args:
            user_id: Telegram ID пользователя.
            role: Роль отправителя — 'user' или 'assistant'.
            text: Текст сообщения.
        """
        logger.info(
            "Сохранение сообщения в историю для user_id=%s, role=%s, длина=%s",
            user_id,
            role,
            len(text),
        )
        connection = await self._get_connection()
        await connection.execute(
            "INSERT INTO messages (user_id, role, text) VALUES (?, ?, ?)",
            (user_id, role, text),
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

    async def _get_connection(self) -> aiosqlite.Connection:
        """Возвращает общее подключение к SQLite, необходимое для :memory:."""
        if self._connection is None:
            logger.info("Открытие SQLite-соединения: %s", self.db_path)
            self._connection = await aiosqlite.connect(self.db_path)
        return self._connection
