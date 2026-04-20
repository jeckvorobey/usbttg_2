"""Тесты адресного reply-router для swarm-режима."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.runtime_models import SwarmBotProfile
from userbot.reply_router import AddressedReplyRouter


def _build_event(
    *,
    sender_id: int,
    raw_text: str = "Привет",
    is_reply: bool = True,
    reply_sender_id: int | None = 101,
    reply_message_id: int = 55,
    sender_is_bot: bool = False,
):
    reply_message = SimpleNamespace(sender_id=reply_sender_id, id=reply_message_id)
    return SimpleNamespace(
        sender_id=sender_id,
        raw_text=raw_text,
        is_reply=is_reply,
        chat_id=-100555,
        id=77,
        reply=AsyncMock(),
        get_reply_message=AsyncMock(return_value=reply_message if is_reply else None),
        get_sender=AsyncMock(return_value=SimpleNamespace(bot=sender_is_bot)),
    )


@pytest.mark.asyncio
async def test_router_ignores_non_reply_message():
    """Проверяет, что router игнорирует обычные сообщения без reply."""
    router = AddressedReplyRouter(
        bot_profile=SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
        history=SimpleNamespace(get_session_history=AsyncMock(), save_message=AsyncMock()),
        prompt_composer=SimpleNamespace(compose=AsyncMock(return_value="system")),
        gemini_client=SimpleNamespace(generate_reply=AsyncMock()),
        swarm_user_ids={202, 303},
    )

    handled = await router.handle_event(_build_event(sender_id=999, is_reply=False))

    assert handled is False


@pytest.mark.asyncio
async def test_router_ignores_reply_to_another_bot():
    """Проверяет, что бот не отвечает на reply к сообщению другого swarm-бота."""
    router = AddressedReplyRouter(
        bot_profile=SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
        history=SimpleNamespace(get_session_history=AsyncMock(), save_message=AsyncMock()),
        prompt_composer=SimpleNamespace(compose=AsyncMock(return_value="system")),
        gemini_client=SimpleNamespace(generate_reply=AsyncMock()),
        swarm_user_ids={202, 303},
    )

    handled = await router.handle_event(_build_event(sender_id=999, reply_sender_id=202))

    assert handled is False


@pytest.mark.asyncio
async def test_router_ignores_messages_from_swarm_bot():
    """Проверяет, что router игнорирует входящее сообщение от другого swarm-бота."""
    router = AddressedReplyRouter(
        bot_profile=SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
        history=SimpleNamespace(get_session_history=AsyncMock(), save_message=AsyncMock()),
        prompt_composer=SimpleNamespace(compose=AsyncMock(return_value="system")),
        gemini_client=SimpleNamespace(generate_reply=AsyncMock()),
        swarm_user_ids={202, 303},
    )

    handled = await router.handle_event(_build_event(sender_id=202, reply_sender_id=101))

    assert handled is False


@pytest.mark.asyncio
async def test_router_ignores_reply_from_telegram_bot_sender():
    """Проверяет, что бот не отвечает на reply от Telegram-бота."""
    router = AddressedReplyRouter(
        bot_profile=SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
        history=SimpleNamespace(get_session_history=AsyncMock(), save_message=AsyncMock()),
        prompt_composer=SimpleNamespace(compose=AsyncMock(return_value="system")),
        gemini_client=SimpleNamespace(generate_reply=AsyncMock()),
        swarm_user_ids={202, 303},
    )

    handled = await router.handle_event(_build_event(sender_id=999, reply_sender_id=101, sender_is_bot=True))

    assert handled is False


@pytest.mark.asyncio
async def test_router_answers_only_to_addressed_bot_and_saves_history():
    """Проверяет генерацию ответа адресованным ботом и сохранение swarm-метаданных."""
    history = SimpleNamespace(
        get_session_history=AsyncMock(return_value=[{"role": "user", "text": "Предыдущее"}]),
        save_message=AsyncMock(),
    )
    prompt_composer = SimpleNamespace(compose=AsyncMock(return_value="system+persona"))
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ Анны"))
    manager = SimpleNamespace(human_slot=lambda _bot_id: _AsyncNullContext())

    router = AddressedReplyRouter(
        bot_profile=SwarmBotProfile(id="anna", session_string="anna", persona_file="anna.md", telegram_user_id=101),
        history=history,
        prompt_composer=prompt_composer,
        gemini_client=gemini_client,
        swarm_user_ids={202, 303},
        manager=manager,
    )

    event = _build_event(sender_id=999, raw_text="Как думаешь?")

    handled = await router.handle_event(event)

    assert handled is True
    prompt_composer.compose.assert_awaited_once_with("reply", bot_id="anna", persona_file="anna.md")
    gemini_client.generate_reply.assert_awaited_once_with(
        system_prompt="system+persona",
        history=[{"role": "user", "text": "Предыдущее"}],
        user_message="Как думаешь?",
    )
    event.reply.assert_awaited_once_with("Ответ Анны")
    assert history.save_message.await_count == 2
    assert history.save_message.await_args_list[0].kwargs["message_origin"] == "human_reply"
    assert history.save_message.await_args_list[1].kwargs["bot_id"] == "anna"


class _AsyncNullContext:
    """Минимальный async context manager для тестов."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False
