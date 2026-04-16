"""Режим windowed_qa: один вопрос и один ответ в заданных UTC-окнах."""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from ai.gemini import GeminiClient, GeminiGenerationError, GeminiTemporaryError, PromptLoader
from ai.history import MessageHistory
from userbot.handlers import WhitelistFilter, _extract_chat_id, _extract_message_text
from userbot.scheduler import TopicSelector, _ensure_utc


logger = logging.getLogger(__name__)
NowFactory = Callable[[], datetime]


@dataclass(frozen=True)
class ActiveWindow:
    """Активное UTC-окно для одного обмена."""

    name: str
    start: datetime
    end: datetime

    @property
    def key(self) -> str:
        """Возвращает стабильный ключ окна в пределах даты UTC."""
        return f"{self.start:%Y-%m-%d}:{self.name}:{self.start.hour}-{self.end.hour}"


class WindowSchedule:
    """Определяет активное окно и момент выстрела initiator-а."""

    def __init__(
        self,
        morning_window_utc: tuple[int, int] = (10, 11),
        evening_window_utc: tuple[int, int] = (16, 18),
        initiator_offset_minutes: tuple[int, int] = (0, 30),
        random_int: Callable[[int, int], int] | None = None,
    ) -> None:
        self.windows = (
            ("morning", morning_window_utc),
            ("evening", evening_window_utc),
        )
        self.initiator_offset_minutes = initiator_offset_minutes
        self._random_int = random_int or random.randint
        self._offsets_by_window: dict[str, int] = {}

    def current_window_utc(self, now_utc: datetime | None = None) -> ActiveWindow | None:
        """Возвращает активное UTC-окно или None."""
        now = _ensure_utc(now_utc or datetime.now(UTC))
        for name, (start_hour, end_hour) in self.windows:
            start, end = self._window_bounds(now, start_hour, end_hour)
            if start <= now < end:
                return ActiveWindow(name=name, start=start, end=end)
        return None

    @staticmethod
    def _window_bounds(now: datetime, start_hour: int, end_hour: int) -> tuple[datetime, datetime]:
        """Строит границы окна, поддерживая переход через полночь."""
        start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        if end_hour == 24:
            end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        else:
            end = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
        if start_hour < end_hour:
            return start, end
        if now.hour < end_hour:
            start -= timedelta(days=1)
        else:
            end += timedelta(days=1)
        return start, end

    def should_fire_initiator(self, window: ActiveWindow, now_utc: datetime | None = None) -> bool:
        """Проверяет, наступил ли выбранный offset внутри активного окна."""
        now = _ensure_utc(now_utc or datetime.now(UTC))
        offset = self._offsets_by_window.setdefault(
            window.key,
            self._random_int(*self.initiator_offset_minutes),
        )
        return now >= window.start + timedelta(minutes=offset)


class ExchangeTracker:
    """In-memory учёт количества обменов в каждом окне."""

    def __init__(self, max_exchanges_per_window: int = 1) -> None:
        self.max_exchanges_per_window = max(1, max_exchanges_per_window)
        self._counts_by_window: dict[str, int] = {}

    def can_start(self, window: ActiveWindow) -> bool:
        """Проверяет, можно ли начать обмен в окне."""
        return self._counts_by_window.get(window.key, 0) < self.max_exchanges_per_window

    def mark_completed(self, window: ActiveWindow) -> None:
        """Помечает обмен в окне как выполненный."""
        self._counts_by_window[window.key] = self._counts_by_window.get(window.key, 0) + 1


async def initiator_job(
    settings: Any,
    client: object | None,
    gemini: GeminiClient,
    topics: TopicSelector,
    history: MessageHistory,
    prompt_loader: PromptLoader,
    schedule: WindowSchedule,
    tracker: ExchangeTracker,
    now_utc_factory: NowFactory | None = None,
) -> None:
    """Отправляет один стартовый вопрос, если наступило окно initiator-а."""
    now = now_utc_factory() if callable(now_utc_factory) else datetime.now(UTC)
    window = schedule.current_window_utc(now)
    if window is None:
        return
    if not tracker.can_start(window):
        return
    if not schedule.should_fire_initiator(window, now):
        return
    if client is None:
        logger.warning("windowed_qa initiator: Telegram-клиент отсутствует")
        return

    target = await _resolve_send_target(client, settings)
    if target is None:
        logger.warning("windowed_qa initiator: целевая группа не задана или не резолвится")
        return

    try:
        topic = await topics.pick_random()
        system_prompt = await prompt_loader.load("system")
        start_topic_prompt = await prompt_loader.load("start_topic")
        message = await gemini.start_topic(
            system_prompt=f"{system_prompt}\n\n{start_topic_prompt}",
            topic=topic,
        )
        await _send_message(client, target, message)
        tracker.mark_completed(window)
        chat_id = getattr(settings, "group_chat_id", None)
        await history.save_message(0, "assistant", message, chat_id=chat_id)
        logger.info("windowed_qa initiator: вопрос отправлен, window=%s, topic=%s", window.key, topic)
    except (GeminiTemporaryError, GeminiGenerationError) as exc:
        logger.warning("windowed_qa initiator: Gemini не сгенерировал вопрос: %s", exc)
    except Exception:
        logger.exception("windowed_qa initiator: ошибка отправки вопроса")


def build_responder_handler(
    settings: Any,
    client: object | None,
    gemini: GeminiClient,
    history: MessageHistory,
    prompt_loader: PromptLoader,
    whitelist: WhitelistFilter | None = None,
    schedule: WindowSchedule | None = None,
    tracker: ExchangeTracker | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
    now_utc_factory: NowFactory | None = None,
) -> Callable[[object], Any]:
    """Создаёт Telethon handler responder-а для одного ответа в окне."""
    effective_whitelist = whitelist or WhitelistFilter(_parse_whitelist(getattr(settings, "whitelist_user_ids", "")))
    effective_schedule = schedule or WindowSchedule(
        morning_window_utc=getattr(settings, "window_morning_utc", (10, 11)),
        evening_window_utc=getattr(settings, "window_evening_utc", (16, 18)),
        initiator_offset_minutes=getattr(settings, "initiator_offset_minutes", (0, 30)),
    )
    effective_tracker = tracker or ExchangeTracker(getattr(settings, "max_exchanges_per_window", 1))

    async def on_new_message(event: object) -> None:
        if getattr(event, "_reply_guard_consumed", False):
            return

        sender_id = getattr(event, "sender_id", None)
        if not isinstance(sender_id, int):
            return
        chat_id = _extract_chat_id(event)
        expected_chat_id = getattr(settings, "group_chat_id", None)
        if expected_chat_id is not None and chat_id != expected_chat_id:
            return
        if not await effective_whitelist.is_allowed(sender_id):
            return

        now = now_utc_factory() if callable(now_utc_factory) else datetime.now(UTC)
        window = effective_schedule.current_window_utc(now)
        if window is None or not effective_tracker.can_start(window):
            return

        user_message = _extract_message_text(event)
        if not user_message:
            return

        effective_tracker.mark_completed(window)
        delay_range = getattr(settings, "responder_delay_minutes", (8, 12))
        delay_seconds = random.randint(*delay_range) * 60
        logger.info("windowed_qa responder: ответ запланирован через %s сек, window=%s", delay_seconds, window.key)
        await _sleep_maybe_awaitable(sleep, delay_seconds)
        await _send_responder_reply(event, gemini, history, prompt_loader, sender_id, chat_id, user_message)

    return on_new_message


async def _send_responder_reply(
    event: object,
    gemini: GeminiClient,
    history: MessageHistory,
    prompt_loader: PromptLoader,
    sender_id: int,
    chat_id: int | None,
    user_message: str,
) -> None:
    """Генерирует и отправляет единственный ответ responder-а."""
    try:
        history_items = await history.get_session_history(chat_id)
        system_prompt = await prompt_loader.load("system")
        reply_prompt = await prompt_loader.load("reply")
        reply_text = await gemini.generate_reply(
            system_prompt=f"{system_prompt}\n\n{reply_prompt}",
            history=history_items,
            user_message=user_message,
        )
    except (GeminiTemporaryError, GeminiGenerationError) as exc:
        logger.warning("windowed_qa responder: Gemini не сгенерировал ответ: %s", exc)
        return
    except Exception:
        logger.exception("windowed_qa responder: ошибка генерации ответа")
        return

    respond = getattr(event, "respond", None)
    if respond is None:
        logger.warning("windowed_qa responder: у события нет метода respond")
        return
    result = respond(reply_text)
    if inspect.isawaitable(result):
        await result
    await history.save_message(sender_id, "user", user_message, chat_id=chat_id)
    await history.save_message(sender_id, "assistant", reply_text, chat_id=chat_id)
    logger.info("windowed_qa responder: ответ отправлен, sender_id=%s, chat_id=%s", sender_id, chat_id)


async def _resolve_send_target(client: object, settings: Any) -> object | None:
    """Определяет target для исходящего сообщения."""
    group_target = getattr(settings, "group_target", None)
    if group_target:
        get_entity = getattr(client, "get_entity", None)
        if get_entity is not None:
            result = get_entity(group_target)
            return await result if inspect.isawaitable(result) else result
        return group_target
    return getattr(settings, "group_chat_id", None)


async def _send_message(client: object, target: object, message: str) -> None:
    """Отправляет сообщение через Telethon-like клиент."""
    send_message = getattr(client, "send_message")
    result = send_message(target, message)
    if inspect.isawaitable(result):
        await result


async def _sleep_maybe_awaitable(sleep: Callable[[float], Any], delay_seconds: float) -> None:
    """Выполняет sleep, поддерживая sync/async заглушки в тестах."""
    result = sleep(delay_seconds)
    if inspect.isawaitable(result):
        await result


def _parse_whitelist(value: object) -> set[int]:
    """Парсит совместимое строковое представление whitelist."""
    if isinstance(value, str):
        return {int(item.strip()) for item in value.split(",") if item.strip().isdigit()}
    if isinstance(value, (list, tuple, set)):
        return {int(item) for item in value if isinstance(item, int)}
    return set()
