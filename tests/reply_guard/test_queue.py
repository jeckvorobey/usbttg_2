"""Тесты SQLite-очереди reply_guard."""

from userbot.reply_guard.queue import ReplyGuardQueue


async def test_queue_enqueue_and_claim_fifo(tmp_path):
    """Проверяет FIFO-порядок выдачи задач."""
    queue = ReplyGuardQueue(str(tmp_path / "reply_guard.db"))
    await queue.init_db()

    first_id = await queue.enqueue(chat_id=1, user_id=10, user_msg_id=100, text="Первый")
    second_id = await queue.enqueue(chat_id=1, user_id=11, user_msg_id=101, text="Второй")

    first = await queue.claim_next()
    second = await queue.claim_next()

    assert first is not None
    assert second is not None
    assert first.id == first_id
    assert second.id == second_id


async def test_queue_stores_reply_context(tmp_path):
    """Проверяет сохранение контекста сообщения бота для follow-up вопросов."""
    queue = ReplyGuardQueue(str(tmp_path / "reply_guard.db"))
    await queue.init_db()

    await queue.enqueue(
        chat_id=1,
        user_id=10,
        user_msg_id=100,
        text="Какую бы посоветовал?",
        reply_context="В прокате бывают Vision и AirBlade.",
    )

    job = await queue.claim_next()

    assert job is not None
    assert job.reply_context == "В прокате бывают Vision и AirBlade."


async def test_queue_ignores_duplicate_user_message(tmp_path):
    """Проверяет идемпотентность enqueue для одного Telegram-сообщения."""
    queue = ReplyGuardQueue(str(tmp_path / "reply_guard.db"))
    await queue.init_db()

    first_id = await queue.enqueue(chat_id=1, user_id=10, user_msg_id=100, text="Первый")
    duplicate_id = await queue.enqueue(chat_id=1, user_id=10, user_msg_id=100, text="Дубль")

    assert first_id is not None
    assert duplicate_id is None


async def test_queue_claim_is_atomic(tmp_path):
    """Проверяет, что одну pending-задачу нельзя выдать дважды."""
    queue = ReplyGuardQueue(str(tmp_path / "reply_guard.db"))
    await queue.init_db()
    await queue.enqueue(chat_id=1, user_id=10, user_msg_id=100, text="Один")

    first = await queue.claim_next()
    second = await queue.claim_next()

    assert first is not None
    assert second is None


async def test_queue_reclaims_retryable_failure(tmp_path):
    """Проверяет, что retryable-ошибка возвращает задачу после backoff."""
    queue = ReplyGuardQueue(str(tmp_path / "reply_guard.db"))
    await queue.init_db()
    await queue.enqueue(chat_id=1, user_id=10, user_msg_id=100, text="Один")
    first = await queue.claim_next()
    assert first is not None

    await queue.fail(first.id, "503", retry=True, backoff_seconds=60)
    too_early = await queue.claim_next()
    assert too_early is None

    await queue.fail(first.id, "503", retry=True, backoff_seconds=0)
    second = await queue.claim_next()

    assert second is not None
    assert second.id == first.id
    assert second.attempts == 2
