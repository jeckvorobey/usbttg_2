"""SQLite-очередь reply_guard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite


@dataclass(frozen=True)
class ReplyGuardJob:
    """Одна задача ответа на Telegram reply к сообщению бота."""

    id: int
    chat_id: int
    user_id: int
    user_msg_id: int
    text: str
    reply_context: str | None
    status: str
    attempts: int
    created_at: str
    updated_at: str
    verdict: str | None
    last_error: str | None
    next_attempt_at: str | None = None


class ReplyGuardQueue:
    """Минимальная FIFO-очередь на SQLite для одного воркера."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init_db(self) -> None:
        """Создаёт таблицу и индексы очереди."""
        await self._ensure_parent_dir()
        db = await aiosqlite.connect(self.db_path)
        try:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS reply_guard_jobs (
                  id            INTEGER PRIMARY KEY AUTOINCREMENT,
                  chat_id       INTEGER NOT NULL,
                  user_id       INTEGER NOT NULL,
                  user_msg_id   INTEGER NOT NULL,
                  text          TEXT    NOT NULL,
                  reply_context TEXT,
                  status        TEXT    NOT NULL,
                  attempts      INTEGER NOT NULL DEFAULT 0,
                  created_at    TEXT    NOT NULL,
                  updated_at    TEXT    NOT NULL,
                  verdict       TEXT,
                  last_error    TEXT,
                  next_attempt_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_rg_status_created
                  ON reply_guard_jobs(status, created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_rg_user_msg
                  ON reply_guard_jobs(chat_id, user_msg_id);
                """
            )
            await db.commit()
            await _ensure_column(db, "reply_guard_jobs", "next_attempt_at", "TEXT")
            await _ensure_column(db, "reply_guard_jobs", "reply_context", "TEXT")
            await db.commit()
        finally:
            await db.close()

    async def enqueue(
        self,
        chat_id: int,
        user_id: int,
        user_msg_id: int,
        text: str,
        reply_context: str | None = None,
    ) -> int | None:
        """Добавляет задачу, возвращая id или None при дубле."""
        now = _utc_iso()
        db = await aiosqlite.connect(self.db_path)
        try:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO reply_guard_jobs
                    (chat_id, user_id, user_msg_id, text, reply_context, status, attempts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                """,
                (chat_id, user_id, user_msg_id, text, reply_context, now, now),
            )
            await db.commit()
            if cursor.rowcount == 0:
                return None
            return int(cursor.lastrowid)
        finally:
            await db.close()

    async def claim_next(self) -> ReplyGuardJob | None:
        """Атомарно забирает следующую pending-задачу."""
        now = _utc_iso()
        db = await aiosqlite.connect(self.db_path)
        try:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                UPDATE reply_guard_jobs
                SET status = 'processing',
                    attempts = attempts + 1,
                    updated_at = ?
                WHERE id = (
                    SELECT id FROM reply_guard_jobs
                    WHERE status = 'pending'
                       OR (
                           status = 'failed_retry'
                           AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                       )
                    ORDER BY created_at ASC, id ASC
                    LIMIT 1
                )
                RETURNING *
                """,
                (now, now),
            )
            row = await cursor.fetchone()
            await db.commit()
            if row is None:
                return None
            return _row_to_job(row)
        finally:
            await db.close()

    async def complete(self, job_id: int, status: str, verdict: str | None = None) -> None:
        """Завершает задачу терминальным статусом."""
        await self._update_terminal(job_id, status=status, verdict=verdict, error=None)

    async def fail(self, job_id: int, error: str, retry: bool = False, backoff_seconds: float = 0.0) -> None:
        """Помечает задачу ошибкой."""
        status = "failed_retry" if retry else "failed"
        next_attempt_at = _utc_iso(timedelta(seconds=max(0.0, backoff_seconds))) if retry else None
        await self._update_terminal(
            job_id,
            status=status,
            verdict=None,
            error=error,
            next_attempt_at=next_attempt_at,
        )

    async def _update_terminal(
        self,
        job_id: int,
        status: str,
        verdict: str | None,
        error: str | None,
        next_attempt_at: str | None = None,
    ) -> None:
        now = _utc_iso()
        db = await aiosqlite.connect(self.db_path)
        try:
            await db.execute(
                """
                UPDATE reply_guard_jobs
                SET status = ?,
                    verdict = COALESCE(?, verdict),
                    last_error = ?,
                    next_attempt_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, verdict, error, next_attempt_at, now, job_id),
            )
            await db.commit()
        finally:
            await db.close()

    async def _ensure_parent_dir(self) -> None:
        """Создаёт директорию для файла БД, если это файловый путь."""
        if self.db_path == ":memory:":
            return
        parent = Path(self.db_path).parent
        if str(parent) and str(parent) != ".":
            parent.mkdir(parents=True, exist_ok=True)


def _row_to_job(row: aiosqlite.Row) -> ReplyGuardJob:
    """Преобразует строку SQLite в dataclass."""
    return ReplyGuardJob(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        user_id=int(row["user_id"]),
        user_msg_id=int(row["user_msg_id"]),
        text=str(row["text"]),
        reply_context=row["reply_context"],
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        verdict=row["verdict"],
        last_error=row["last_error"],
        next_attempt_at=row["next_attempt_at"],
    )


def _utc_iso(offset: timedelta | None = None) -> str:
    """Возвращает ISO8601 UTC timestamp."""
    value = datetime.now(UTC)
    if offset is not None:
        value += offset
    return value.isoformat()


async def _ensure_column(
    db: aiosqlite.Connection,
    table_name: str,
    column_name: str,
    column_type: str,
) -> None:
    """Добавляет колонку в существующую таблицу, если её ещё нет."""
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        rows = await cursor.fetchall()
    existing_columns = {row[1] for row in rows}
    if column_name not in existing_columns:
        await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
