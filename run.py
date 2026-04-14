"""Точка входа для запуска Telegram userbot'а."""

import asyncio
import inspect
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ai.gemini import GeminiClient, PromptLoader
from ai.history import MessageHistory
from ai.reply_rules import ReplyRulesLoader
from core.config import load_settings_or_exit
from core.logging import setup_logging
from userbot.client import UserBotClient
from userbot.handlers import WhitelistFilter, handle_new_message
from userbot.scheduler import ConversationSession, SilenceWatcher, TopicSelector, is_dnd_active_utc


logger = logging.getLogger(__name__)
SILENCE_CHECK_INTERVAL_MINUTES = 10


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

    iter_dialogs = getattr(telegram_client, "iter_dialogs", None)
    if iter_dialogs is not None and group_chat_id is not None:
        async for dialog in iter_dialogs():
            dialog_id = getattr(dialog, "id", None)
            entity = getattr(dialog, "entity", None)
            entity_id = getattr(entity, "id", None)
            if _chat_id_matches(group_chat_id, dialog_id) or _chat_id_matches(group_chat_id, entity_id):
                setattr(telegram_client, "_resolved_group_chat_id", group_chat_id)
                setattr(telegram_client, "_resolved_group_target", group_target)
                setattr(telegram_client, "_resolved_group_chat_target", entity or dialog)
                return getattr(telegram_client, "_resolved_group_chat_target")

    normalized_group_target = group_target.strip() if isinstance(group_target, str) else None
    if normalized_group_target:
        get_entity = getattr(telegram_client, "get_entity", None)
        if get_entity is None:
            logger.warning(
                "Не удалось резолвить target группы '%s': клиент не поддерживает get_entity",
                normalized_group_target,
            )
            return None
        try:
            resolved_target = await get_entity(normalized_group_target)
        except ValueError:
            logger.warning(
                "Не удалось резолвить target группы '%s' через get_entity",
                normalized_group_target,
            )
            return None
        setattr(telegram_client, "_resolved_group_chat_id", group_chat_id)
        setattr(telegram_client, "_resolved_group_target", normalized_group_target)
        setattr(telegram_client, "_resolved_group_chat_target", resolved_target)
        return resolved_target

    if group_chat_id is not None:
        logger.warning("Не удалось найти entity целевой группы для group_chat_id=%s среди диалогов клиента", group_chat_id)
    else:
        logger.warning("Не удалось найти entity целевой группы: не заданы GROUP_CHAT_ID и GROUP_TARGET")
    return None


async def _sync_group_activity(
    telegram_client: object | None,
    group_chat_id: int | None,
    group_target: str | None,
    silence_watcher: SilenceWatcher,
) -> None:
    """Синхронизирует время последней активности по последнему сообщению в группе."""
    if telegram_client is None or group_chat_id is None:
        return

    get_messages = getattr(telegram_client, "get_messages", None)
    if get_messages is None:
        return

    resolved_group_target = await _resolve_group_target(telegram_client, group_chat_id, group_target)
    if resolved_group_target is None:
        logger.warning(
            "Не удалось получить последнее сообщение группы для group_chat_id=%s: entity не резолвится клиентом",
            group_chat_id,
        )
        return

    try:
        result = get_messages(resolved_group_target, limit=1)
        if inspect.isawaitable(result):
            result = await result
    except ValueError:
        logger.warning(
            "Не удалось получить последнее сообщение группы для group_chat_id=%s: entity не резолвится клиентом",
            group_chat_id,
        )
        return

    last_message = None
    if isinstance(result, list):
        if result:
            last_message = result[0]
    else:
        last_message = result

    message_date = getattr(last_message, "date", None)
    if isinstance(message_date, datetime):
        silence_watcher.update_last_activity(message_date)


async def _log_resolved_group(
    telegram_client: object | None,
    group_chat_id: int | None,
    group_target: str | None,
) -> None:
    """Логирует целевую группу, в которой будет работать бот."""
    resolved_group_target = await _resolve_group_target(telegram_client, group_chat_id, group_target)
    if resolved_group_target is None:
        logger.warning(
            "Не удалось определить целевую группу при инициализации: GROUP_CHAT_ID=%s, GROUP_TARGET=%s",
            group_chat_id,
            group_target,
        )
        return

    group_title = getattr(resolved_group_target, "title", None) or "<без названия>"
    group_id = getattr(resolved_group_target, "id", None)
    group_username = getattr(resolved_group_target, "username", None)

    if group_username:
        logger.info(
            "Целевая группа определена: title=%s, id=%s, username=@%s",
            group_title,
            group_id,
            group_username,
        )
        return

    logger.info(
        "Целевая группа определена: title=%s, id=%s",
        group_title,
        group_id,
    )


async def _register_handlers(userbot_client: UserBotClient, whitelist: WhitelistFilter) -> None:
    """Регистрирует обработчики Telethon, если библиотека доступна."""
    telegram_client = userbot_client.client
    if telegram_client is None:
        logger.warning("Регистрация обработчиков пропущена: Telegram-клиент отсутствует")
        return

    try:
        from telethon import events
    except ImportError:
        logger.warning("Регистрация обработчиков пропущена: telethon не установлен")
        return

    async def on_new_message(event: object) -> None:
        await handle_new_message(event=event, whitelist=whitelist)

    telegram_client.add_event_handler(on_new_message, events.NewMessage())
    logger.info("Обработчик новых сообщений зарегистрирован")

async def main() -> None:
    """Инициализирует и запускает userbot."""
    settings = load_settings_or_exit()
    setup_logging(settings.log_level)
    logger.info("Запуск приложения userbot")

    history = MessageHistory(settings.db_path)
    logger.info("Инициализация хранилища истории сообщений")
    await history.init_db()

    whitelist_ids = {
        int(uid.strip())
        for uid in settings.whitelist_user_ids.split(",")
        if uid.strip().isdigit()
    }
    whitelist = WhitelistFilter(user_ids=whitelist_ids)
    logger.info("Whitelist инициализирован из WHITELIST_USER_IDS: %s пользователей", len(whitelist_ids))

    prompt_loader = PromptLoader(settings.prompts_dir)
    reply_rules_loader = ReplyRulesLoader(settings.reply_rules_path)
    await reply_rules_loader.load()
    logger.info("Инициализация Gemini клиента")
    gemini_client = GeminiClient(
        settings.gemini_api_key,
        model_name=settings.gemini_model,
        proxy_url=settings.proxy_url,
        fallback_model_name=settings.gemini_fallback_model,
        max_retries=settings.gemini_max_retries,
        retry_backoff_seconds=settings.gemini_retry_backoff_seconds,
        retry_jitter_seconds=settings.gemini_retry_jitter_seconds,
    )

    topic_selector = TopicSelector(settings.topics_path)
    logger.info("Загрузка списка тем из %s", settings.topics_path)
    await topic_selector.load()
    conversation_session = ConversationSession(duration_minutes=settings.session_duration_minutes)
    silence_watcher = SilenceWatcher()

    userbot_client = UserBotClient(
        session_string=settings.session_string,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        proxy_url=settings.proxy_url,
    )
    logger.info("Запуск Telegram-клиента")
    await userbot_client.start()

    telegram_client = userbot_client.client
    if telegram_client is not None:
        logger.info("Привязка runtime-зависимостей к Telegram-клиенту")
        telegram_client.message_history = history
        telegram_client.prompt_loader = prompt_loader
        telegram_client.reply_rules_loader = reply_rules_loader
        telegram_client.gemini_client = gemini_client
        telegram_client.topic_selector = topic_selector
        telegram_client.conversation_session = conversation_session
        telegram_client.silence_watcher = silence_watcher
        telegram_client.group_chat_id = settings.group_chat_id
        telegram_client.group_target = settings.group_target
        telegram_client.dnd_hours_utc = settings.dnd_hours_utc
        telegram_client.scheduler_enabled = settings.scheduler_enabled
        await _log_resolved_group(
            telegram_client,
            settings.group_chat_id,
            settings.group_target,
        )

    scheduler = AsyncIOScheduler()

    if settings.scheduler_enabled:
        async def _session_expiry_job() -> None:
            """Проверяет истечение активной сессии разговора — запускается каждую минуту."""
            conversation_session.is_active()

        async def _silence_check_job() -> None:
            """Инициирует разговор после тишины — запускается каждые 10 минут."""
            if conversation_session.is_active():
                return
            if is_dnd_active_utc(settings.dnd_hours_utc, _utc_now()):
                logger.info("Проверка тишины пропущена: активен режим не беспокоить")
                return
            await _sync_group_activity(
                telegram_client,
                settings.group_chat_id,
                settings.group_target,
                silence_watcher,
            )
            if not silence_watcher.is_silence_exceeded(settings.silence_timeout_minutes):
                return
            if settings.group_chat_id is None and not settings.group_target:
                logger.warning("Невозможно начать разговор: GROUP_CHAT_ID и GROUP_TARGET не заданы в настройках")
                return
            if telegram_client is None:
                return
            resolved_group_target = await _resolve_group_target(
                telegram_client,
                settings.group_chat_id,
                settings.group_target,
            )
            if resolved_group_target is None:
                logger.warning(
                    "Невозможно начать разговор: не удалось резолвить target группы. "
                    "Проверьте доступ аккаунта к чату и настройку GROUP_TARGET."
                )
                return
            try:
                topic = await topic_selector.pick_random()
                system_prompt = await prompt_loader.load("system")
                start_topic_prompt = await prompt_loader.load("start_topic")
                message = await gemini_client.start_topic(
                    system_prompt=f"{system_prompt}\n\n{start_topic_prompt}",
                    topic=topic,
                )
                await telegram_client.send_message(resolved_group_target, message)
                conversation_session.start(topic)
                silence_watcher.update_last_activity()
                logger.info("Разговор на тему '%s' инициирован после тишины", topic)
            except Exception:
                logger.exception("Ошибка при инициации разговора по расписанию")

        scheduler.add_job(_session_expiry_job, "interval", minutes=1)
        scheduler.add_job(_silence_check_job, "interval", minutes=SILENCE_CHECK_INTERVAL_MINUTES)
        logger.info(
            "Планировщик активен: проверка тишины каждые %s мин, порог тишины %s мин, сессия до %s мин",
            SILENCE_CHECK_INTERVAL_MINUTES,
            settings.silence_timeout_minutes,
            settings.session_duration_minutes,
        )
    else:
        logger.info("Режим расписания отключён (SCHEDULER_ENABLED=false)")

    scheduler.start()
    logger.info("Планировщик запущен")

    try:
        await _register_handlers(userbot_client, whitelist)
        if telegram_client is not None:
            logger.info("Переход в режим ожидания сообщений Telegram")
            await telegram_client.run_until_disconnected()
        else:
            logger.warning("Запуск ожидания сообщений пропущен: Telegram-клиент отсутствует")
    finally:
        shutdown = getattr(scheduler, "shutdown", None)
        if callable(shutdown):
            logger.info("Остановка планировщика")
            result = shutdown(wait=False)
            if inspect.isawaitable(result):
                await result
        logger.info("Остановка Telegram-клиента")
        await userbot_client.stop()
        logger.info("Приложение остановлено")


if __name__ == "__main__":
    asyncio.run(main())
