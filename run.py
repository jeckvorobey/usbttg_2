"""Точка входа для запуска swarm userbot."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ai.gemini import GeminiClient, PromptLoader
from ai.history import MessageHistory
from ai.prompt_composer import PromptComposer
from core.config import load_settings_or_exit
from core.logging import setup_logging
from core.runtime_models import SwarmBotProfile
from userbot.client import UserBotClient
from userbot.exchange_store import ExchangeStore
from userbot.orchestrator import SwarmOrchestrator
from userbot.reply_router import AddressedReplyRouter
from userbot.scheduler import TopicSelector
from userbot.swarm_manager import SwarmManager


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeContext:
    """Переиспользуемые runtime-зависимости приложения."""

    history: MessageHistory
    prompt_loader: PromptLoader
    gemini_client: GeminiClient
    topic_selector: TopicSelector
    prompt_composer: PromptComposer
    exchange_store: ExchangeStore

    async def close(self) -> None:
        """Закрывает runtime-ресурсы с внешними соединениями."""
        for resource in (self.history, self.exchange_store):
            close = getattr(resource, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result


def _utc_now() -> datetime:
    """Возвращает текущее время в UTC."""
    return datetime.now(UTC)


def _iter_candidate_chat_ids(chat_id: int) -> set[int]:
    """Возвращает набор идентификаторов для сопоставления чата и entity Telethon."""
    candidates = {chat_id, abs(chat_id)}
    absolute_chat_id = abs(chat_id)

    if chat_id > 0:
        candidates.add(-(10**12 + chat_id))
        return candidates
    if absolute_chat_id >= 10**12:
        candidates.add(absolute_chat_id - 10**12)
    return candidates


def _chat_id_matches(expected_chat_id: int, actual_chat_id: object) -> bool:
    """Проверяет, соответствует ли найденный идентификатор настроенному chat_id."""
    return isinstance(actual_chat_id, int) and actual_chat_id in _iter_candidate_chat_ids(expected_chat_id)


def _is_invite_link(target: str | None) -> bool:
    """Определяет, является ли target приватной invite-ссылкой Telegram."""
    if not isinstance(target, str):
        return False
    normalized = target.strip()
    return normalized.startswith(("https://t.me/+", "http://t.me/+", "https://t.me/joinchat/", "http://t.me/joinchat/"))


def _normalize_public_group_target(target: str) -> str:
    """Нормализует публичный target группы до формы, совместимой с Telethon."""
    normalized = target.strip()
    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"} and parsed.netloc == "t.me":
        path = parsed.path.strip("/")
        if path and "/" not in path:
            return f"@{path}" if not path.startswith("@") else path
    return normalized


def _extract_public_target_slug(target: str | None) -> str | None:
    """Извлекает username/slug публичной группы из target."""
    if not isinstance(target, str):
        return None

    normalized = _normalize_public_group_target(target)
    if normalized.startswith("@"):
        slug = normalized.removeprefix("@").strip()
        return slug.casefold() or None
    return None


def _dialog_matches_group(dialog: object, group_chat_id: int | None, group_target: str | None) -> bool:
    """Проверяет, относится ли dialog к целевой группе по id или публичному username."""
    dialog_id = getattr(dialog, "id", None)
    entity = getattr(dialog, "entity", None)
    entity_id = getattr(entity, "id", None)
    if group_chat_id is not None and (
        _chat_id_matches(group_chat_id, dialog_id) or _chat_id_matches(group_chat_id, entity_id)
    ):
        return True

    expected_slug = _extract_public_target_slug(group_target)
    if not expected_slug:
        return False

    for candidate in (getattr(dialog, "username", None), getattr(entity, "username", None)):
        if isinstance(candidate, str) and candidate.strip().casefold() == expected_slug:
            return True
    return False


async def _resolve_joined_group_dialog(
    telegram_client: object | None,
    group_chat_id: int | None,
    group_target: str | None = None,
) -> object | None:
    """Возвращает dialog/entity только если клиент уже состоит в целевой группе."""
    if telegram_client is None:
        return None

    iter_dialogs = getattr(telegram_client, "iter_dialogs", None)
    if iter_dialogs is None:
        return None

    async for dialog in iter_dialogs():
        if _dialog_matches_group(dialog, group_chat_id, group_target):
            entity = getattr(dialog, "entity", None)
            return entity or dialog
    return None


def _extract_join_result_target(join_result: object | None) -> object | None:
    """Извлекает entity группы из результата join-запроса Telethon."""
    if join_result is None:
        return None

    chats = getattr(join_result, "chats", None)
    if isinstance(chats, list) and chats:
        return chats[0]
    return join_result


async def _resolve_group_target(
    telegram_client: object | None,
    group_chat_id: int | None,
    group_target: str | None = None,
) -> object | None:
    """Находит и кэширует entity целевой группы для вызовов Telethon."""
    if telegram_client is None:
        return None

    cached_chat_id = getattr(telegram_client, "_resolved_group_chat_id", None)
    cached_group_target = getattr(telegram_client, "_resolved_group_target", None)
    cached_target = getattr(telegram_client, "_resolved_group_chat_target", None)
    if cached_chat_id == group_chat_id and cached_group_target == group_target and cached_target is not None:
        return cached_target

    joined_group_target = await _resolve_joined_group_dialog(telegram_client, group_chat_id, group_target)
    if joined_group_target is not None:
        setattr(telegram_client, "_resolved_group_chat_id", group_chat_id)
        setattr(telegram_client, "_resolved_group_target", group_target)
        setattr(telegram_client, "_resolved_group_chat_target", joined_group_target)
        return joined_group_target

    normalized_group_target = group_target.strip() if isinstance(group_target, str) else None
    if normalized_group_target:
        if _is_invite_link(normalized_group_target):
            logger.info("Пропуск get_entity для invite link target=%s", normalized_group_target)
            return None
        get_entity = getattr(telegram_client, "get_entity", None)
        if get_entity is None:
            logger.warning("Не удалось резолвить target группы '%s': get_entity недоступен", normalized_group_target)
            return None
        try:
            resolved_target = await get_entity(normalized_group_target)
        except ValueError:
            logger.warning("Не удалось резолвить target группы '%s' через get_entity", normalized_group_target)
            return None
        setattr(telegram_client, "_resolved_group_chat_id", group_chat_id)
        setattr(telegram_client, "_resolved_group_target", normalized_group_target)
        setattr(telegram_client, "_resolved_group_chat_target", resolved_target)
        return resolved_target

    logger.warning("Не удалось найти entity целевой группы: GROUP_CHAT_ID=%s GROUP_TARGET=%s", group_chat_id, group_target)
    return None


async def _ensure_group_membership(
    client_wrapper: UserBotClient,
    group_chat_id: int | None,
    group_target: str | None,
    bot_id: str,
) -> object | None:
    """Гарантирует доступ клиента к целевой группе, при необходимости выполняя вступление."""
    telegram_client = client_wrapper.client
    resolved_target = await _resolve_joined_group_dialog(telegram_client, group_chat_id, group_target)
    if resolved_target is not None:
        logger.info("swarm: bot_id=%s уже имеет доступ к целевой группе", bot_id)
        return resolved_target

    normalized_target = group_target.strip() if isinstance(group_target, str) else None
    if not normalized_target:
        logger.warning("swarm: bot_id=%s пропускает автovступление: group_target не задан", bot_id)
        return None

    if group_chat_id is not None and _is_invite_link(normalized_target):
        raise ValueError(
            f"bot_id={bot_id} не имеет доступа к группе с GROUP_CHAT_ID={group_chat_id}; "
            "обновите bot membership вручную или задайте актуальный публичный GROUP_TARGET"
        )

    if _is_invite_link(normalized_target):
        logger.info("swarm: bot_id=%s пытается вступить в группу по invite link", bot_id)
        join_result = await client_wrapper.join_invite_link(normalized_target)
    else:
        public_target = _normalize_public_group_target(normalized_target)
        logger.info("swarm: bot_id=%s пытается вступить в публичную группу: %s", bot_id, public_target)
        join_result = await client_wrapper.join_group(public_target)

    resolved_target = await _resolve_joined_group_dialog(telegram_client, group_chat_id, group_target)
    if resolved_target is None:
        resolved_target = _extract_join_result_target(join_result)
    if resolved_target is None:
        resolved_target = await _resolve_group_target(telegram_client, group_chat_id, group_target)
    if resolved_target is not None:
        logger.info("swarm: bot_id=%s успешно получил доступ к целевой группе после автovступления", bot_id)
        return resolved_target

    logger.warning("swarm: bot_id=%s не смог получить доступ к группе после автovступления", bot_id)
    return None


async def _log_resolved_group(
    telegram_client: object | None,
    group_chat_id: int | None,
    group_target: str | None,
) -> None:
    """Логирует целевую группу, в которой будет работать swarm."""
    resolved_group_target = await _resolve_group_target(telegram_client, group_chat_id, group_target)
    if resolved_group_target is None:
        logger.warning(
            "Не удалось определить целевую группу при инициализации: GROUP_CHAT_ID=%s, GROUP_TARGET=%s",
            group_chat_id,
            group_target,
        )
        return

    logger.info(
        "Целевая группа определена: title=%s id=%s username=%s",
        getattr(resolved_group_target, "title", None) or "<без названия>",
        getattr(resolved_group_target, "id", None),
        getattr(resolved_group_target, "username", None),
    )


async def _build_runtime_context(settings: object) -> RuntimeContext:
    """Создаёт общие runtime-зависимости swarm."""
    history = MessageHistory(settings.db_path)
    await history.init_db()

    prompt_loader = PromptLoader(settings.prompts_dir)
    gemini_client = GeminiClient(
        settings.gemini_api_key,
        model_name=settings.gemini_model,
        proxy_url=settings.proxy_url,
        fallback_model_name=settings.gemini_fallback_model,
        max_retries=settings.gemini_max_retries,
        retry_backoff_seconds=settings.gemini_retry_backoff_seconds,
        retry_jitter_seconds=settings.gemini_retry_jitter_seconds,
        request_timeout_seconds=settings.gemini_request_timeout_seconds,
        temperature=settings.gemini_temperature,
    )
    topic_selector = TopicSelector(settings.topics_path)
    await topic_selector.load()
    prompt_composer = PromptComposer(prompt_loader=prompt_loader, bot_profiles_dir=settings.bot_profiles_dir)
    exchange_store = ExchangeStore(settings.db_path)
    await exchange_store.init_db()

    logger.info(
        "RuntimeContext инициализирован: db_path=%s prompts_dir=%s topics=%s",
        settings.db_path,
        settings.prompts_dir,
        len(topic_selector.topics),
    )
    return RuntimeContext(
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        topic_selector=topic_selector,
        prompt_composer=prompt_composer,
        exchange_store=exchange_store,
    )


def _build_swarm_bot_profiles(settings: object) -> list[SwarmBotProfile]:
    """Преобразует конфигурацию в runtime-профили swarm-ботов."""
    profiles = [
        SwarmBotProfile(
            id=bot.id,
            session_string=bot.session_string,
            persona_file=bot.persona_file,
            enabled=bot.enabled,
            temperature=bot.temperature,
            session_env=bot.session_env,
        )
        for bot in settings.swarm_bots
        if bot.enabled
    ]
    logger.info("Подготовлены swarm-профили: enabled_bots=%s", len(profiles))
    return profiles


async def _register_swarm_handlers(manager: SwarmManager, runtime: RuntimeContext) -> None:
    """Регистрирует addressed-reply handlers на всех клиентах swarm."""
    try:
        from telethon import events
    except ImportError:
        logger.warning("Регистрация swarm handler-ов пропущена: telethon не установлен")
        return

    active_profiles = {profile.id: profile for profile in manager.bot_profiles if profile.enabled}
    for bot_id in manager.active_bot_ids:
        profile = active_profiles.get(bot_id)
        if profile is None:
            logger.warning("Пропуск регистрации handler-а: активный bot_id=%s отсутствует в профилях", bot_id)
            continue
        client_wrapper = manager.get_client(profile.id)
        telegram_client = client_wrapper.client
        router = AddressedReplyRouter(
            bot_profile=profile,
            history=runtime.history,
            prompt_composer=runtime.prompt_composer,
            gemini_client=runtime.gemini_client,
            swarm_user_ids=manager.swarm_user_ids,
            manager=manager,
        )

        async def on_new_message(event: object, *, _router: AddressedReplyRouter = router) -> None:
            await _router.handle_event(event)

        telegram_client.add_event_handler(on_new_message, events.NewMessage())
        logger.info("Зарегистрирован addressed-reply handler: bot_id=%s", profile.id)


async def _run_swarm_mode(settings: object, runtime: RuntimeContext, scheduler: AsyncIOScheduler) -> None:
    """Запускает swarm-режим с постоянным пулом клиентов."""
    bot_profiles = _build_swarm_bot_profiles(settings)
    if len(bot_profiles) < 2:
        raise ValueError("Swarm mode requires at least two enabled bots")

    manager = SwarmManager(
        bot_profiles=bot_profiles,
        client_factory=lambda profile: UserBotClient(
            session_string=profile.session_string,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            proxy_url=settings.proxy_url,
        ),
        startup_hook=lambda profile, client: _ensure_group_membership(
            client,
            settings.group_chat_id,
            settings.group_target,
            profile.id,
        ),
    )
    await manager.start()
    if len(manager.active_bot_ids) < 2:
        raise ValueError("Swarm mode requires at least two active bots after startup")
    await _register_swarm_handlers(manager, runtime)

    first_client = manager.get_client(manager.active_bot_ids[0]).client
    await _log_resolved_group(first_client, settings.group_chat_id, settings.group_target)
    resolved_group_target = await _resolve_group_target(first_client, settings.group_chat_id, settings.group_target)
    group_target = resolved_group_target or settings.group_target or settings.group_chat_id
    if group_target is None:
        raise ValueError("Swarm mode requires GROUP_CHAT_ID or GROUP_TARGET")

    orchestrator = SwarmOrchestrator(
        bot_profiles=bot_profiles,
        manager=manager,
        topic_selector=runtime.topic_selector,
        prompt_composer=runtime.prompt_composer,
        gemini_client=runtime.gemini_client,
        history=runtime.history,
        exchange_store=runtime.exchange_store,
        group_target=group_target,
        group_chat_id=settings.group_chat_id,
        max_turns_per_exchange=settings.swarm_max_turns_per_exchange,
        pair_cooldown_slots=settings.swarm_pair_cooldown_slots,
        active_windows_utc=settings.swarm_schedule_active_windows_utc,
        initiator_offset_minutes=settings.swarm_initiator_offset_minutes,
        responder_delay_minutes=settings.swarm_responder_delay_minutes,
        skip_if_recent_human_activity=settings.swarm_skip_if_recent_human_activity,
        resolve_group_target=lambda telegram_client: _resolve_group_target(
            telegram_client,
            settings.group_chat_id,
            settings.group_target,
        ),
    )
    scheduler.add_job(
        orchestrator.run_once,
        "interval",
        seconds=settings.swarm_tick_seconds,
        max_instances=1,
        coalesce=True,
    )
    logger.info("SwarmOrchestrator зарегистрирован: tick_seconds=%s", settings.swarm_tick_seconds)

    supervise_tasks = [asyncio.create_task(manager.supervise_bot(bot_id)) for bot_id in manager.active_bot_ids]
    try:
        await asyncio.gather(*supervise_tasks)
    finally:
        for task in supervise_tasks:
            task.cancel()
        await asyncio.gather(*supervise_tasks, return_exceptions=True)
        await manager.stop()


async def main() -> None:
    """Инициализирует и запускает swarm userbot."""
    settings = load_settings_or_exit()
    setup_logging(settings.log_level)
    logger.info("Запуск swarm userbot")
    if settings.mode != "swarm":
        raise ValueError("Поддерживается только mode=swarm")

    runtime = await _build_runtime_context(settings)
    scheduler = AsyncIOScheduler()
    scheduler.start()
    logger.info("Планировщик запущен")
    try:
        await _run_swarm_mode(settings, runtime, scheduler)
    finally:
        shutdown = getattr(scheduler, "shutdown", None)
        if callable(shutdown):
            result = shutdown(wait=False)
            if inspect.isawaitable(result):
                await result
        await runtime.close()
        logger.info("Swarm userbot остановлен")


if __name__ == "__main__":
    asyncio.run(main())
