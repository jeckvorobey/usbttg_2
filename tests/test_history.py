"""Тесты для модуля хранения истории диалогов."""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from ai.history import MessageHistory


@pytest_asyncio.fixture
async def history():
    """Создаёт экземпляр истории с in-memory базой данных."""
    h = MessageHistory(db_path=":memory:")
    await h.init_db()
    return h


async def test_save_and_get_message(history):
    """Проверяет сохранение одного сообщения и его получение по user_id."""
    await history.save_message(user_id=123, role="user", text="Привет!")
    messages = await history.get_history(user_id=123)

    assert len(messages) == 1
    assert messages[0]["text"] == "Привет!"
    assert messages[0]["role"] == "user"


async def test_get_history_isolation_by_user_id(history):
    """Проверяет, что история одного пользователя не смешивается с другим."""
    await history.save_message(user_id=111, role="user", text="Сообщение пользователя 111")
    await history.save_message(user_id=222, role="user", text="Сообщение пользователя 222")

    messages_111 = await history.get_history(user_id=111)
    messages_222 = await history.get_history(user_id=222)

    assert len(messages_111) == 1
    assert messages_111[0]["text"] == "Сообщение пользователя 111"
    assert len(messages_222) == 1
    assert messages_222[0]["text"] == "Сообщение пользователя 222"


async def test_history_limit(history):
    """Проверяет, что параметр limit ограничивает количество возвращаемых сообщений."""
    for i in range(10):
        await history.save_message(user_id=999, role="user", text=f"Сообщение {i}")

    messages = await history.get_history(user_id=999, limit=5)
    assert len(messages) == 5


async def test_history_save_assistant_role(history):
    """Проверяет сохранение сообщения с ролью 'assistant'."""
    await history.save_message(user_id=42, role="user", text="Вопрос")
    await history.save_message(user_id=42, role="assistant", text="Ответ")

    messages = await history.get_history(user_id=42)
    assert len(messages) == 2
    roles = [m["role"] for m in messages]
    assert "user" in roles
    assert "assistant" in roles


async def test_empty_history_returns_empty_list(history):
    """Проверяет, что запрос истории несуществующего пользователя возвращает пустой список."""
    messages = await history.get_history(user_id=99999)
    assert messages == []


async def test_get_session_history_returns_messages_for_chat(history):
    """Проверяет, что get_session_history возвращает сообщения по chat_id."""
    await history.save_message(user_id=111, role="user", text="Привет из чата", chat_id=-100555)
    messages = await history.get_session_history(chat_id=-100555)

    assert len(messages) == 1
    assert messages[0]["text"] == "Привет из чата"
    assert messages[0]["role"] == "user"


async def test_get_session_history_returns_messages_from_multiple_users(history):
    """Проверяет, что get_session_history возвращает сообщения всех участников чата."""
    await history.save_message(user_id=111, role="user", text="Сообщение от 111", chat_id=-100555)
    await history.save_message(user_id=222, role="user", text="Ответ от 222", chat_id=-100555)
    await history.save_message(user_id=111, role="assistant", text="Ответ бота 111", chat_id=-100555)

    messages = await history.get_session_history(chat_id=-100555)

    assert len(messages) == 3
    texts = [m["text"] for m in messages]
    assert "Сообщение от 111" in texts
    assert "Ответ от 222" in texts
    assert "Ответ бота 111" in texts


async def test_get_session_history_isolates_different_chats(history):
    """Проверяет, что сообщения из разных чатов не перемешиваются."""
    await history.save_message(user_id=1, role="user", text="Чат А", chat_id=-100111)
    await history.save_message(user_id=2, role="user", text="Чат Б", chat_id=-100222)

    messages_a = await history.get_session_history(chat_id=-100111)
    messages_b = await history.get_session_history(chat_id=-100222)

    assert len(messages_a) == 1
    assert messages_a[0]["text"] == "Чат А"
    assert len(messages_b) == 1
    assert messages_b[0]["text"] == "Чат Б"


async def test_get_session_history_filters_by_session_start(history):
    """Проверяет, что session_start исключает сообщения до начала сессии."""
    conn = await history._get_connection()
    await conn.execute(
        "INSERT INTO messages (user_id, chat_id, role, text, created_at) VALUES (?, ?, ?, ?, ?)",
        (1, -100555, "user", "Старое", "2020-01-01 00:00:00"),
    )
    await conn.commit()

    session_start = datetime(2023, 1, 1, 0, 0, 0)

    await history.save_message(user_id=1, role="user", text="Новое", chat_id=-100555)

    messages = await history.get_session_history(chat_id=-100555, session_start=session_start)

    texts = [m["text"] for m in messages]
    assert "Новое" in texts
    assert "Старое" not in texts


async def test_get_session_history_returns_empty_for_none_chat_id(history):
    """Проверяет, что get_session_history с chat_id=None возвращает пустой список."""
    await history.save_message(user_id=1, role="user", text="Что-то", chat_id=-100555)
    messages = await history.get_session_history(chat_id=None)
    assert messages == []


async def test_init_db_is_idempotent(history):
    """Проверяет, что повторный вызов init_db не вызывает ошибку (включая миграцию)."""
    await history.init_db()  # вызов второй раз не должен упасть
