"""Точка входа для запуска Telegram userbot'а."""

import asyncio
import inspect
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ai.gemini import GeminiClient, PromptLoader
from ai.history import MessageHistory
from core.config import load_settings_or_exit
from core.logging import setup_logging
from userbot.client import UserBotClient
from userbot.handlers import WhitelistFilter, handle_new_message
from userbot.scheduler import ConversationSession, SilenceWatcher, TopicSelector


logger = logging.getLogger(__name__)


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
        telegram_client.gemini_client = gemini_client
        telegram_client.topic_selector = topic_selector
        telegram_client.conversation_session = conversation_session
        telegram_client.silence_watcher = silence_watcher

    scheduler = AsyncIOScheduler()

    if settings.scheduler_enabled:
        async def _session_expiry_job() -> None:
            """Проверяет истечение активной сессии разговора — запускается каждую минуту."""
            conversation_session.is_active()

        async def _silence_check_job() -> None:
            """Инициирует разговор после тишины — запускается каждые SILENCE_TIMEOUT_MINUTES."""
            if conversation_session.is_active():
                return
            if not silence_watcher.is_silence_exceeded(settings.silence_timeout_minutes):
                return
            if settings.group_chat_id is None:
                logger.warning("Невозможно начать разговор: GROUP_CHAT_ID не задан в настройках")
                return
            if telegram_client is None:
                return
            try:
                topic = await topic_selector.pick_random()
                system_prompt = await prompt_loader.load("system")
                start_topic_prompt = await prompt_loader.load("start_topic")
                message = await gemini_client.start_topic(
                    system_prompt=f"{system_prompt}\n\n{start_topic_prompt}",
                    topic=topic,
                )
                await telegram_client.send_message(settings.group_chat_id, message)
                conversation_session.start(topic)
                silence_watcher.update_last_activity()
                logger.info("Разговор на тему '%s' инициирован после тишины", topic)
            except Exception:
                logger.exception("Ошибка при инициации разговора по расписанию")

        scheduler.add_job(_session_expiry_job, "interval", minutes=1)
        scheduler.add_job(_silence_check_job, "interval", minutes=settings.silence_timeout_minutes)
        logger.info(
            "Планировщик активен: проверка тишины каждые %s мин, сессия до %s мин",
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
