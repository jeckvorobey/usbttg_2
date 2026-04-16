"""Тесты event-handler-а reply_guard."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from userbot.reply_guard.handler import build_reply_guard_handler


async def test_handler_ignores_non_reply():
    """Проверяет, что обычное сообщение не попадает в reply_guard."""
    queue = SimpleNamespace(enqueue=AsyncMock())
    handler = build_reply_guard_handler(queue=queue, bot_user_id=777)
    event = SimpleNamespace(is_reply=False, sender_id=123, chat_id=1, raw_text="Привет")

    await handler(event)

    queue.enqueue.assert_not_awaited()
    assert not hasattr(event, "_reply_guard_consumed")


async def test_handler_ignores_reply_to_non_bot_message():
    """Проверяет, что reply на сообщение другого пользователя не обрабатывается."""
    queue = SimpleNamespace(enqueue=AsyncMock())
    handler = build_reply_guard_handler(queue=queue, bot_user_id=777)
    reply_message = SimpleNamespace(sender_id=555)
    event = SimpleNamespace(
        is_reply=True,
        sender_id=123,
        chat_id=1,
        raw_text="Где рынок?",
        id=42,
        get_reply_message=AsyncMock(return_value=reply_message),
    )

    await handler(event)

    queue.enqueue.assert_not_awaited()
    assert not hasattr(event, "_reply_guard_consumed")


async def test_handler_enqueues_reply_to_bot_without_whitelist():
    """Проверяет, что reply на бота ставится в очередь без whitelist-проверки."""
    queue = SimpleNamespace(enqueue=AsyncMock(return_value=5))
    handler = build_reply_guard_handler(queue=queue, bot_user_id=777)
    reply_message = SimpleNamespace(sender_id=777, text="Возьмите свежий байк в Европейском квартале.")
    event = SimpleNamespace(
        is_reply=True,
        sender_id=999,
        chat_id=1,
        raw_text="Где в Нячанге аптека?",
        id=42,
        get_reply_message=AsyncMock(return_value=reply_message),
    )

    await handler(event)

    queue.enqueue.assert_awaited_once_with(
        chat_id=1,
        user_id=999,
        user_msg_id=42,
        text="Где в Нячанге аптека?",
        reply_context="Возьмите свежий байк в Европейском квартале.",
    )
    assert event._reply_guard_consumed is True


async def test_handler_ignores_reply_to_bot_outside_allowed_chat():
    """Проверяет, что reply_guard не отвечает вне целевого чата."""
    queue = SimpleNamespace(enqueue=AsyncMock(return_value=5))
    handler = build_reply_guard_handler(queue=queue, bot_user_id=777, allowed_chat_ids={1})
    reply_message = SimpleNamespace(sender_id=777)
    event = SimpleNamespace(
        is_reply=True,
        sender_id=999,
        chat_id=2,
        raw_text="Где в Нячанге аптека?",
        id=42,
        get_reply_message=AsyncMock(return_value=reply_message),
    )

    await handler(event)

    queue.enqueue.assert_not_awaited()
    assert not hasattr(event, "_reply_guard_consumed")
