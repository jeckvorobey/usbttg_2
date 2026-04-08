"""Точка входа для запуска Telegram userbot'а."""

import asyncio
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ai.gemini import GeminiClient, PromptLoader
from ai.history import MessageHistory
from core.config import get_settings
from userbot.client import UserBotClient
from userbot.handlers import WhitelistFilter, handle_new_message
from userbot.scheduler import ConversationSession, TopicSelector


async def _register_handlers(userbot_client: UserBotClient, whitelist: WhitelistFilter) -> None:
    """Регистрирует обработчики Telethon, если библиотека доступна."""
    telegram_client = userbot_client.client
    if telegram_client is None:
        return

    try:
        from telethon import events
    except ImportError:
        return

    async def on_new_message(event: object) -> None:
        await handle_new_message(event=event, whitelist=whitelist)

    telegram_client.add_event_handler(on_new_message, events.NewMessage())


def _build_session_path(session_name: str) -> str:
    """Преобразует имя сессии в путь до файла Telethon."""
    session_path = Path(session_name)
    if session_path.parent != Path(".") or session_path.suffix == ".session":
        return str(session_path)
    return str(Path("data/sessions") / session_name)


async def main() -> None:
    """Инициализирует и запускает userbot."""
    settings = get_settings()

    history = MessageHistory(settings.db_path)
    await history.init_db()

    whitelist = WhitelistFilter(settings.whitelist_path)
    await whitelist.load()

    prompt_loader = PromptLoader(settings.prompts_dir)
    gemini_client = GeminiClient(
        settings.gemini_api_key,
        proxy_url=settings.proxy_url,
    )

    topic_selector = TopicSelector(settings.topics_path)
    await topic_selector.load()
    conversation_session = ConversationSession()

    userbot_client = UserBotClient(
        session_name=_build_session_path(settings.session_name),
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        proxy_url=settings.proxy_url,
    )
    await userbot_client.start()

    telegram_client = userbot_client.client
    if telegram_client is not None:
        telegram_client.message_history = history
        telegram_client.prompt_loader = prompt_loader
        telegram_client.gemini_client = gemini_client
        telegram_client.topic_selector = topic_selector
        telegram_client.conversation_session = conversation_session

    scheduler = AsyncIOScheduler()
    scheduler.start()

    try:
        await _register_handlers(userbot_client, whitelist)
        if telegram_client is not None:
            await telegram_client.run_until_disconnected()
    finally:
        shutdown = getattr(scheduler, "shutdown", None)
        if callable(shutdown):
            shutdown(wait=False)
        await userbot_client.stop()


if __name__ == "__main__":
    asyncio.run(main())
