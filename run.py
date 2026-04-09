"""Точка входа для запуска Telegram userbot'а."""

import asyncio
import inspect
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ai.gemini import GeminiClient, PromptLoader
from ai.history import MessageHistory
from core.config import get_settings
from core.logging import setup_logging
from userbot.client import UserBotClient
from userbot.handlers import WhitelistFilter, handle_new_message
from userbot.scheduler import ConversationSession, TopicSelector


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


def _build_session_path(session_name: str) -> str:
    """Преобразует имя сессии в путь до файла Telethon."""
    session_path = Path(session_name)
    if session_path.parent != Path(".") or session_path.suffix == ".session":
        return str(session_path)
    return str(Path("data/sessions") / session_name)


async def main() -> None:
    """Инициализирует и запускает userbot."""
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("Запуск приложения userbot")

    history = MessageHistory(settings.db_path)
    logger.info("Инициализация хранилища истории сообщений")
    await history.init_db()

    whitelist = WhitelistFilter(settings.whitelist_path)
    logger.info("Загрузка whitelist из %s", settings.whitelist_path)
    await whitelist.load()

    prompt_loader = PromptLoader(settings.prompts_dir)
    logger.info("Инициализация Gemini клиента")
    gemini_client = GeminiClient(
        settings.gemini_api_key,
        proxy_url=settings.proxy_url,
    )

    topic_selector = TopicSelector(settings.topics_path)
    logger.info("Загрузка списка тем из %s", settings.topics_path)
    await topic_selector.load()
    conversation_session = ConversationSession()

    userbot_client = UserBotClient(
        session_name=_build_session_path(settings.session_name),
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

    scheduler = AsyncIOScheduler()
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
