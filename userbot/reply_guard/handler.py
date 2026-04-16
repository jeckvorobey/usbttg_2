"""Telethon event-handler для reply_guard."""

from __future__ import annotations

import hashlib
import inspect
import logging
from collections.abc import Callable
from typing import Any

from userbot.handlers import _extract_chat_id, _extract_message_text
from userbot.reply_guard.queue import ReplyGuardQueue


logger = logging.getLogger(__name__)


def build_reply_guard_handler(
    queue: ReplyGuardQueue,
    bot_user_id: int,
    allowed_chat_ids: set[int] | None = None,
) -> Callable[[object], Any]:
    """Создаёт handler, который ставит reply-to-bot сообщения в очередь."""
    effective_allowed_chat_ids = allowed_chat_ids or set()

    async def on_new_message(event: object) -> None:
        if not getattr(event, "is_reply", False):
            return

        reply_message = await _get_reply_message(event)
        if getattr(reply_message, "sender_id", None) != bot_user_id:
            return

        sender_id = getattr(event, "sender_id", None)
        chat_id = _extract_chat_id(event)
        if effective_allowed_chat_ids and chat_id not in effective_allowed_chat_ids:
            logger.info("reply_guard handler: chat_id=%s пропущен вне целевой группы", chat_id)
            return

        user_msg_id = getattr(event, "id", None) or getattr(getattr(event, "message", None), "id", None)
        if not isinstance(sender_id, int) or not isinstance(chat_id, int) or not isinstance(user_msg_id, int):
            logger.warning("reply_guard handler: событие пропущено из-за неполных идентификаторов")
            return

        text = _extract_message_text(event)
        if not text:
            logger.info(
                "reply_guard handler: пустой reply пропущен chat_id=%s user_id=%s msg_id=%s",
                chat_id,
                sender_id,
                user_msg_id,
            )
            return

        logger.info(
            "reply_guard handler: reply_detected chat_id=%s user_id=%s msg_id=%s len=%s sha1=%s",
            chat_id,
            sender_id,
            user_msg_id,
            len(text),
            _short_hash(text),
        )
        job_id = await queue.enqueue(
            chat_id=chat_id,
            user_id=sender_id,
            user_msg_id=user_msg_id,
            text=text,
            reply_context=_extract_message_text(reply_message),
        )
        setattr(event, "_reply_guard_consumed", True)
        if job_id is None:
            logger.warning(
                "reply_guard handler: duplicate_enqueue_ignored chat_id=%s msg_id=%s",
                chat_id,
                user_msg_id,
            )
            return
        logger.info("reply_guard handler: enqueued job_id=%s", job_id)

    return on_new_message


async def _get_reply_message(event: object) -> object | None:
    """Возвращает сообщение, на которое ответил пользователь."""
    get_reply_message = getattr(event, "get_reply_message", None)
    if get_reply_message is None:
        return None
    result = get_reply_message()
    if inspect.isawaitable(result):
        return await result
    return result


def _short_hash(text: str) -> str:
    """Возвращает короткий hash для логов без раскрытия текста."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
