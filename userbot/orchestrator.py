"""Central orchestrator для scheduled swarm-обменов."""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from core.runtime_models import ExchangeDecision, SwarmBotProfile
from userbot.exchange_store import ExchangeStore, normalize_signature
from userbot.scheduler import is_within_windows_utc


logger = logging.getLogger(__name__)


class SwarmOrchestrator:
    """Выбирает пары ботов и проводит A -> B exchange."""

    def __init__(
        self,
        *,
        bot_profiles: list[SwarmBotProfile],
        manager: Any,
        topic_selector: Any,
        prompt_composer: Any,
        gemini_client: Any,
        history: Any,
        exchange_store: ExchangeStore | Any,
        group_target: object | None = None,
        group_chat_id: int | None = None,
        max_turns_per_exchange: int = 2,
        pair_cooldown_slots: int = 1,
        active_windows_utc: list[str] | None = None,
        skip_if_recent_human_activity: bool = True,
        human_activity_checker: Callable[[], bool] | None = None,
        now_provider: Callable[[], Any] | None = None,
        topic_repeat_window: timedelta = timedelta(days=1),
        question_repeat_window: timedelta = timedelta(days=2),
    ) -> None:
        self.bot_profiles = [profile for profile in bot_profiles if profile.enabled]
        self.manager = manager
        self.topic_selector = topic_selector
        self.prompt_composer = prompt_composer
        self.gemini_client = gemini_client
        self.history = history
        self.exchange_store = exchange_store
        self.group_target = group_target
        self.group_chat_id = group_chat_id
        self.max_turns_per_exchange = max_turns_per_exchange
        self.pair_cooldown_slots = max(0, pair_cooldown_slots)
        self.active_windows_utc = active_windows_utc or []
        self.skip_if_recent_human_activity = skip_if_recent_human_activity
        self.human_activity_checker = human_activity_checker or (lambda: False)
        self.now_provider = now_provider or (lambda: datetime.now(UTC))
        self.topic_repeat_window = topic_repeat_window
        self.question_repeat_window = question_repeat_window

    async def run_once(self) -> bool:
        """Выполняет один scheduled exchange, если он не заблокирован guard-условиями."""
        now = self.now_provider()
        if not is_within_windows_utc(self.active_windows_utc, now):
            logger.info("orchestrator: skip exchange outside active windows now=%s", now)
            return False
        if self.skip_if_recent_human_activity and self.human_activity_checker():
            logger.info("orchestrator: skip exchange because recent human activity detected")
            return False
        if self.group_target is None:
            logger.warning("orchestrator: skip exchange because group_target is not configured")
            return False

        decision = await self._build_exchange_decision()
        logger.info(
            "orchestrator: selected pair initiator=%s responder=%s topic_key=%s",
            decision.initiator.id,
            decision.responder.id,
            decision.topic_key,
        )
        exchange_id = await self.exchange_store.create_exchange(
            initiator_bot_id=decision.initiator.id,
            responder_bot_id=decision.responder.id,
            topic=decision.topic,
            topic_key=decision.topic_key,
        )

        async with self.manager.scheduled_slot(decision.initiator.id) as initiator_acquired:
            if not initiator_acquired:
                await self.exchange_store.mark_exchange_skipped(exchange_id, "initiator_busy")
                return False
            async with self.manager.scheduled_slot(decision.responder.id) as responder_acquired:
                if not responder_acquired:
                    await self.exchange_store.mark_exchange_skipped(exchange_id, "responder_busy")
                    return False
                return await self._run_exchange(exchange_id=exchange_id, decision=decision)

    async def _build_exchange_decision(self) -> ExchangeDecision:
        """Выбирает пару ботов и тему с persisted anti-repeat."""
        recent_pairs = await self.exchange_store.get_recent_pairs(self.pair_cooldown_slots)
        all_pairs = [
            (initiator, responder)
            for initiator in self.bot_profiles
            for responder in self.bot_profiles
            if initiator.id != responder.id
        ]
        eligible_pairs = [pair for pair in all_pairs if (pair[0].id, pair[1].id) not in set(recent_pairs)]
        chosen_initiator, chosen_responder = random.choice(eligible_pairs or all_pairs)

        topic = await self._choose_topic()
        recent_questions = await self.exchange_store.get_recent_questions(since=self.question_repeat_window)
        return ExchangeDecision(
            initiator=chosen_initiator,
            responder=chosen_responder,
            topic=topic,
            topic_key=normalize_signature(topic),
            recent_questions=recent_questions,
        )

    async def _choose_topic(self) -> str:
        """Выбирает тему, избегая recent topics при наличии альтернатив."""
        recent_topic_keys = await self.exchange_store.get_recent_topic_keys(since=self.topic_repeat_window)
        available_topics = list(getattr(self.topic_selector, "topics", []))
        if not available_topics:
            topic = await self.topic_selector.pick_random()
            logger.info("orchestrator: fallback topic pick via selector topic=%s", topic)
            return topic

        fresh_topics = [topic for topic in available_topics if normalize_signature(topic) not in recent_topic_keys]
        topic = random.choice(fresh_topics or available_topics)
        logger.info(
            "orchestrator: topic selected topic=%s fresh_pool=%s total_pool=%s",
            topic,
            len(fresh_topics),
            len(available_topics),
        )
        return topic

    async def _run_exchange(self, *, exchange_id: str, decision: ExchangeDecision) -> bool:
        """Проводит полный scheduled exchange и сохраняет его в истории."""
        recent_questions_context = ""
        if decision.recent_questions:
            recent_questions_context = "Недавние вопросы, которые не стоит повторять:\n" + "\n".join(
                f"- {item}" for item in decision.recent_questions[:5]
            )

        initiator_prompt = await self.prompt_composer.compose(
            "start_topic",
            bot_id=decision.initiator.id,
            persona_file=decision.initiator.persona_file,
            exchange_context=recent_questions_context,
        )
        initiator_text = await self._generate_non_repeating_question(
            initiator_prompt=initiator_prompt,
            topic=decision.topic,
        )
        initiator_client = self.manager.get_client(decision.initiator.id)
        initiator_message = await initiator_client.client.send_message(self.group_target, initiator_text)
        await self.exchange_store.mark_exchange_started(
            exchange_id,
            initiator_message_id=getattr(initiator_message, "id", None),
            question_text=initiator_text,
            question_signature=initiator_text,
        )
        await self.history.save_message(
            user_id=decision.initiator.telegram_user_id or 0,
            role="assistant",
            text=initiator_text,
            chat_id=self.group_chat_id,
            bot_id=decision.initiator.id,
            exchange_id=exchange_id,
            message_origin="scheduled_initiator",
            reply_to_message_id=None,
        )
        logger.info(
            "orchestrator: initiator sent exchange_id=%s bot_id=%s message_id=%s",
            exchange_id,
            decision.initiator.id,
            getattr(initiator_message, "id", None),
        )

        if self.max_turns_per_exchange > 1:
            responder_prompt = await self.prompt_composer.compose(
                "reply",
                bot_id=decision.responder.id,
                persona_file=decision.responder.persona_file,
                exchange_context=f"Тема обмена: {decision.topic}\nСообщение инициатора: {initiator_text}",
            )
            responder_history = await self.history.get_session_history(
                chat_id=self.group_chat_id,
                bot_id=decision.responder.id,
            )
            responder_text = await self.gemini_client.generate_reply(
                system_prompt=responder_prompt,
                history=responder_history,
                user_message=initiator_text,
            )
            responder_client = self.manager.get_client(decision.responder.id)
            reply_to_message_id = getattr(initiator_message, "id", None)
            await responder_client.client.send_message(self.group_target, responder_text, reply_to=reply_to_message_id)
            await self.history.save_message(
                user_id=decision.responder.telegram_user_id or 0,
                role="assistant",
                text=responder_text,
                chat_id=self.group_chat_id,
                bot_id=decision.responder.id,
                exchange_id=exchange_id,
                message_origin="scheduled_responder",
                reply_to_message_id=reply_to_message_id,
            )
            logger.info(
                "orchestrator: responder sent exchange_id=%s bot_id=%s reply_to=%s",
                exchange_id,
                decision.responder.id,
                reply_to_message_id,
            )

        await self.exchange_store.mark_exchange_completed(exchange_id)
        logger.info("orchestrator: exchange completed exchange_id=%s", exchange_id)
        return True

    async def _generate_non_repeating_question(self, *, initiator_prompt: str, topic: str) -> str:
        """Генерирует вопрос и старается избежать повтора по recent signature."""
        recent_signatures = await self.exchange_store.get_recent_question_signatures(since=self.question_repeat_window)
        prompt = initiator_prompt
        for attempt in range(1, 3):
            question_text = await self.gemini_client.start_topic(system_prompt=prompt, topic=topic)
            signature = normalize_signature(question_text)
            if signature not in recent_signatures:
                return question_text
            logger.info("orchestrator: repeated question signature detected attempt=%s topic=%s", attempt, topic)
            prompt = f"{initiator_prompt}\n\nНе повторяй недавние формулировки. Скажи по-другому и естественнее."
        return question_text
