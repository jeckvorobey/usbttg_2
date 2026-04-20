"""Адресная маршрутизация reply-сообщений в swarm-режиме."""

from __future__ import annotations

import logging
from typing import Any

from ai.gemini import GeminiClient
from ai.history import MessageHistory
from ai.prompt_composer import PromptComposer
from core.runtime_models import SwarmBotProfile
from userbot.swarm_manager import SwarmManager


logger = logging.getLogger(__name__)


class AddressedReplyRouter:
    """Обрабатывает только reply к сообщениям конкретного бота."""

    def __init__(
        self,
        *,
        bot_profile: SwarmBotProfile,
        history: MessageHistory | Any,
        prompt_composer: PromptComposer | Any,
        gemini_client: GeminiClient | Any,
        swarm_user_ids: set[int],
        manager: SwarmManager | Any | None = None,
    ) -> None:
        self.bot_profile = bot_profile
        self.history = history
        self.prompt_composer = prompt_composer
        self.gemini_client = gemini_client
        self.swarm_user_ids = swarm_user_ids
        self.manager = manager

    async def handle_event(self, event: Any) -> bool:
        """Обрабатывает входящее сообщение, если оно адресовано текущему боту."""
        sender_id = getattr(event, "sender_id", None)
        if sender_id in self.swarm_user_ids:
            logger.info("router: bot_id=%s ignore sender from swarm sender_id=%s", self.bot_profile.id, sender_id)
            return False
        if await self._is_bot_sender(event):
            logger.info("router: bot_id=%s ignore telegram-bot sender sender_id=%s", self.bot_profile.id, sender_id)
            return False

        if not getattr(event, "is_reply", False):
            logger.info("router: bot_id=%s ignore non-reply event_id=%s", self.bot_profile.id, getattr(event, "id", None))
            return False

        reply_message = await event.get_reply_message()
        if reply_message is None:
            logger.info("router: bot_id=%s ignore missing reply_message event_id=%s", self.bot_profile.id, getattr(event, "id", None))
            return False

        if getattr(reply_message, "sender_id", None) != self.bot_profile.telegram_user_id:
            logger.info(
                "router: bot_id=%s ignore reply to another bot reply_sender_id=%s",
                self.bot_profile.id,
                getattr(reply_message, "sender_id", None),
            )
            return False

        logger.info(
            "router: bot_id=%s handling addressed reply event_id=%s sender_id=%s",
            self.bot_profile.id,
            getattr(event, "id", None),
            sender_id,
        )

        if self.manager is None:
            return await self._process_reply(event=event, reply_message=reply_message)

        async with self.manager.human_slot(self.bot_profile.id):
            return await self._process_reply(event=event, reply_message=reply_message)

    async def _process_reply(self, *, event: Any, reply_message: Any) -> bool:
        """Обрабатывает уже подтверждённый addressed reply."""
        sender_id = getattr(event, "sender_id", None)
        chat_id = getattr(event, "chat_id", None)
        reply_to_message_id = getattr(reply_message, "id", None)
        user_text = getattr(event, "raw_text", "")
        history = await self.history.get_session_history(chat_id=chat_id, bot_id=self.bot_profile.id)
        system_prompt = await self.prompt_composer.compose(
            "reply",
            bot_id=self.bot_profile.id,
            persona_file=self.bot_profile.persona_file,
        )
        response_text = await self.gemini_client.generate_reply(
            system_prompt=system_prompt,
            history=history,
            user_message=user_text,
        )

        await self.history.save_message(
            user_id=sender_id,
            role="user",
            text=user_text,
            chat_id=chat_id,
            bot_id=self.bot_profile.id,
            message_origin="human_reply",
            reply_to_message_id=reply_to_message_id,
        )
        await event.reply(response_text)
        await self.history.save_message(
            user_id=sender_id,
            role="assistant",
            text=response_text,
            chat_id=chat_id,
            bot_id=self.bot_profile.id,
            message_origin="human_reply",
            reply_to_message_id=reply_to_message_id,
        )
        logger.info(
            "router: bot_id=%s sent human reply event_id=%s reply_to_message_id=%s",
            self.bot_profile.id,
            getattr(event, "id", None),
            reply_to_message_id,
        )
        return True

    async def _is_bot_sender(self, event: Any) -> bool:
        """Проверяет, что отправитель не является Telegram-ботом."""
        sender = getattr(event, "sender", None)
        if sender is None:
            get_sender = getattr(event, "get_sender", None)
            if callable(get_sender):
                sender = await get_sender()
        return bool(getattr(sender, "bot", False))
