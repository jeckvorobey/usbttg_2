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
        initiator_offset_minutes: tuple[int, int] = (0, 30),
        responder_delay_minutes: tuple[int, int] = (3, 10),
        skip_if_recent_human_activity: bool = True,
        human_activity_checker: Callable[[], bool] | None = None,
        now_provider: Callable[[], Any] | None = None,
        topic_repeat_window: timedelta = timedelta(days=1),
        question_repeat_window: timedelta = timedelta(days=2),
        resolve_group_target: Callable[[object], Any] | None = None,
        randint_provider: Callable[[int, int], int] | None = None,
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
        self.initiator_offset_minutes = initiator_offset_minutes
        self.responder_delay_minutes = responder_delay_minutes
        self.skip_if_recent_human_activity = skip_if_recent_human_activity
        self.human_activity_checker = human_activity_checker or (lambda: False)
        self.now_provider = now_provider or (lambda: datetime.now(UTC))
        self.topic_repeat_window = topic_repeat_window
        self.question_repeat_window = question_repeat_window
        self.resolve_group_target = resolve_group_target
        self.randint_provider = randint_provider or random.randint

    async def run_once(self) -> bool:
        """Выполняет одну due-стадию scheduled exchange."""
        now = self.now_provider()
        due_responder_getter = getattr(self.exchange_store, "get_due_started_exchange", None)
        due_responder = await due_responder_getter(now=now) if callable(due_responder_getter) else None
        if due_responder is not None:
            return await self._run_due_responder_exchange(exchange=due_responder)

        if not is_within_windows_utc(self.active_windows_utc, now):
            logger.info("orchestrator: skip exchange outside active windows now=%s", now)
            return False
        if self.skip_if_recent_human_activity and self.human_activity_checker():
            logger.info("orchestrator: skip exchange because recent human activity detected")
            return False
        if self.group_target is None:
            logger.warning("orchestrator: skip exchange because group_target is not configured")
            return False

        window_key, window_start = self._build_window_key(now)
        get_exchange_by_window_key = getattr(self.exchange_store, "get_exchange_by_window_key", None)
        current_window_exchange = await get_exchange_by_window_key(window_key) if callable(get_exchange_by_window_key) else None
        if current_window_exchange is not None:
            status = current_window_exchange.get("status")
            if status == "planned":
                return await self._run_due_planned_exchange(exchange=current_window_exchange, now=now)
            logger.info(
                "orchestrator: skip new exchange because window already has status=%s window_key=%s",
                status,
                window_key,
            )
            return False

        decision = await self._build_exchange_decision()
        logger.info(
            "orchestrator: selected pair initiator=%s responder=%s topic_key=%s",
            decision.initiator.id,
            decision.responder.id,
            decision.topic_key,
        )
        initiator_scheduled_at = self._pick_initiator_due_at(window_start=window_start)
        exchange_id = await self.exchange_store.create_exchange(
            initiator_bot_id=decision.initiator.id,
            responder_bot_id=decision.responder.id,
            topic=decision.topic,
            topic_key=decision.topic_key,
            window_key=window_key,
            initiator_scheduled_at=initiator_scheduled_at,
        )
        planned_exchange = {
            "exchange_id": exchange_id,
            "initiator_bot_id": decision.initiator.id,
            "responder_bot_id": decision.responder.id,
            "topic": decision.topic,
            "window_key": window_key,
            "initiator_scheduled_at": self._serialize_timestamp(initiator_scheduled_at),
        }
        return await self._run_due_planned_exchange(exchange=planned_exchange, now=now)

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

    async def _run_due_planned_exchange(self, *, exchange: dict[str, object], now: datetime) -> bool:
        """Отправляет вопрос инициатора, когда пришло его окно."""
        if not self._is_due(exchange.get("initiator_scheduled_at"), now):
            logger.info(
                "orchestrator: planned exchange is waiting for initiator_due exchange_id=%s due_at=%s",
                exchange.get("exchange_id"),
                exchange.get("initiator_scheduled_at"),
            )
            return False

        initiator_id = str(exchange["initiator_bot_id"])
        responder_id = str(exchange["responder_bot_id"])
        decision = await self._build_exchange_decision_from_record(
            exchange_id=str(exchange["exchange_id"]),
            initiator_id=initiator_id,
            responder_id=responder_id,
            topic=str(exchange["topic"]),
        )

        async with self.manager.scheduled_slot(decision.initiator.id) as initiator_acquired:
            if not initiator_acquired:
                logger.info("orchestrator: initiator busy, planned exchange will retry exchange_id=%s", exchange["exchange_id"])
                return False
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
            initiator_group_target = await self._resolve_group_target_for_client(initiator_client.client)
            initiator_message = await initiator_client.client.send_message(initiator_group_target, initiator_text)
            responder_due_at = now + timedelta(minutes=self.randint_provider(*self.responder_delay_minutes))
            await self.exchange_store.mark_exchange_started(
                str(exchange["exchange_id"]),
                initiator_message_id=getattr(initiator_message, "id", None),
                question_text=initiator_text,
                question_signature=initiator_text,
                responder_scheduled_at=responder_due_at,
            )
            await self.history.save_message(
                user_id=decision.initiator.telegram_user_id or 0,
                role="assistant",
                text=initiator_text,
                chat_id=self.group_chat_id,
                bot_id=decision.initiator.id,
                exchange_id=str(exchange["exchange_id"]),
                message_origin="scheduled_initiator",
                reply_to_message_id=None,
            )
            logger.info(
                "orchestrator: initiator sent exchange_id=%s bot_id=%s message_id=%s responder_due_at=%s",
                exchange["exchange_id"],
                decision.initiator.id,
                getattr(initiator_message, "id", None),
                responder_due_at,
            )

            if self.max_turns_per_exchange <= 1:
                await self.exchange_store.mark_exchange_completed(str(exchange["exchange_id"]))
                logger.info("orchestrator: exchange completed without responder exchange_id=%s", exchange["exchange_id"])
        return True

    async def _run_due_responder_exchange(self, *, exchange: dict[str, object]) -> bool:
        """Отправляет отложенный ответ второго бота."""
        if self.max_turns_per_exchange <= 1:
            await self.exchange_store.mark_exchange_completed(str(exchange["exchange_id"]))
            logger.info("orchestrator: completed stale started exchange without responder exchange_id=%s", exchange["exchange_id"])
            return True

        responder_id = str(exchange["responder_bot_id"])
        initiator_id = str(exchange["initiator_bot_id"])
        async with self.manager.scheduled_slot(responder_id) as responder_acquired:
            if not responder_acquired:
                logger.info("orchestrator: responder busy, due reply will retry exchange_id=%s", exchange["exchange_id"])
                return False

            responder = self._get_bot_profile(responder_id)
            responder_prompt = await self.prompt_composer.compose(
                "reply",
                bot_id=responder.id,
                persona_file=responder.persona_file,
                exchange_context=f"Тема обмена: {exchange['topic']}\nСообщение инициатора: {exchange['question_text']}",
            )
            responder_history = await self.history.get_session_history(
                chat_id=self.group_chat_id,
                bot_id=responder.id,
            )
            responder_text = await self.gemini_client.generate_reply(
                system_prompt=responder_prompt,
                history=responder_history,
                user_message=str(exchange["question_text"]),
            )
            responder_client = self.manager.get_client(responder.id)
            reply_to_message_id = exchange.get("initiator_message_id")
            responder_group_target = await self._resolve_group_target_for_client(responder_client.client)
            await responder_client.client.send_message(
                responder_group_target,
                responder_text,
                reply_to=reply_to_message_id,
            )
            await self.history.save_message(
                user_id=responder.telegram_user_id or 0,
                role="assistant",
                text=responder_text,
                chat_id=self.group_chat_id,
                bot_id=responder.id,
                exchange_id=str(exchange["exchange_id"]),
                message_origin="scheduled_responder",
                reply_to_message_id=reply_to_message_id,
            )
            logger.info(
                "orchestrator: responder sent exchange_id=%s bot_id=%s reply_to=%s initiator=%s",
                exchange["exchange_id"],
                responder.id,
                reply_to_message_id,
                initiator_id,
            )

        await self.exchange_store.mark_exchange_completed(str(exchange["exchange_id"]))
        logger.info("orchestrator: exchange completed exchange_id=%s", exchange["exchange_id"])
        return True

    async def _resolve_group_target_for_client(self, telegram_client: object) -> object:
        """Резолвит entity группы отдельно для каждого Telethon-клиента."""
        if self.resolve_group_target is None:
            return self.group_target

        resolved_target = await self.resolve_group_target(telegram_client)
        if resolved_target is None:
            logger.warning("orchestrator: fallback to shared group_target because per-client resolve returned None")
            return self.group_target
        return resolved_target

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

    async def _build_exchange_decision_from_record(
        self,
        *,
        exchange_id: str,
        initiator_id: str,
        responder_id: str,
        topic: str,
    ) -> ExchangeDecision:
        """Восстанавливает ExchangeDecision из persisted exchange."""
        recent_questions = await self.exchange_store.get_recent_questions(since=self.question_repeat_window)
        logger.info(
            "orchestrator: restoring persisted exchange exchange_id=%s initiator=%s responder=%s",
            exchange_id,
            initiator_id,
            responder_id,
        )
        return ExchangeDecision(
            initiator=self._get_bot_profile(initiator_id),
            responder=self._get_bot_profile(responder_id),
            topic=topic,
            topic_key=normalize_signature(topic),
            recent_questions=recent_questions,
        )

    def _get_bot_profile(self, bot_id: str) -> SwarmBotProfile:
        """Возвращает профиль активного бота по id."""
        for profile in self.bot_profiles:
            if profile.id == bot_id:
                return profile
        raise KeyError(bot_id)

    def _pick_initiator_due_at(self, *, window_start: datetime) -> datetime:
        """Выбирает момент первого сообщения внутри активного окна."""
        if not self.active_windows_utc:
            return self.now_provider()
        offset_minutes = self.randint_provider(*self.initiator_offset_minutes)
        return window_start + timedelta(minutes=offset_minutes)

    def _build_window_key(self, now: datetime) -> tuple[str, datetime]:
        """Строит persisted ключ текущего активного окна."""
        if not self.active_windows_utc:
            start = now.replace(minute=0, second=0, microsecond=0)
            return f"{start.strftime('%Y-%m-%dT%H')}:always-open", start

        for window in self.active_windows_utc:
            start_hour, end_hour = (int(part) for part in window.split("-", maxsplit=1))
            if self._hour_is_within_window(now.hour, start_hour, end_hour):
                start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
                if start_hour > end_hour and now.hour < end_hour:
                    start -= timedelta(days=1)
                return f"{start.strftime('%Y-%m-%dT%H')}:{window}", start
        start = now.replace(minute=0, second=0, microsecond=0)
        return f"{start.strftime('%Y-%m-%dT%H')}:fallback", start

    @staticmethod
    def _hour_is_within_window(current_hour: int, start_hour: int, end_hour: int) -> bool:
        """Проверяет попадание часа в UTC-окно."""
        if start_hour == end_hour:
            return True
        if start_hour < end_hour:
            return start_hour <= current_hour < end_hour
        return current_hour >= start_hour or current_hour < end_hour

    @staticmethod
    def _is_due(raw_timestamp: object, now: datetime) -> bool:
        """Проверяет, наступил ли due timestamp из SQLite."""
        if raw_timestamp is None:
            return True
        if isinstance(raw_timestamp, datetime):
            due_at = raw_timestamp
        else:
            due_at = datetime.strptime(str(raw_timestamp), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        return due_at <= now.astimezone(UTC)

    @staticmethod
    def _serialize_timestamp(value: datetime | None) -> str | None:
        """Преобразует datetime в строку SQLite-формата."""
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
