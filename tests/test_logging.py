"""Тесты централизованного логирования и ключевых runtime-событий."""

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai.gemini import GeminiClient, PromptLoader
from ai.history import MessageHistory
from userbot.handlers import WhitelistFilter, handle_new_message
from userbot.scheduler import ConversationSession, TopicSelector


def test_setup_logging_sets_root_level():
    """Проверяет, что инициализация задаёт ожидаемый уровень root logger."""
    from core.logging import setup_logging

    previous_level = logging.getLogger().level
    try:
        setup_logging("DEBUG")
        assert logging.getLogger().level == logging.DEBUG
    finally:
        logging.getLogger().setLevel(previous_level)


def test_setup_logging_reuses_existing_handler_count():
    """Проверяет, что повторная инициализация не дублирует handlers."""
    from core.logging import setup_logging

    root_logger = logging.getLogger()
    initial_handlers = len(root_logger.handlers)

    setup_logging("INFO")
    first_handlers = len(root_logger.handlers)
    setup_logging("DEBUG")
    second_handlers = len(root_logger.handlers)

    assert first_handlers >= initial_handlers
    assert second_handlers == first_handlers


@pytest.mark.asyncio
async def test_run_main_logs_startup_and_shutdown(monkeypatch, caplog):
    """Проверяет логирование запуска и корректного завершения приложения."""
    import run
    from core.config import Settings

    settings = Settings(
        api_id=1,
        api_hash="hash",
        gemini_api_key="gemini-key",
        session_name="84523248603",
        db_path=":memory:",
        whitelist_path="data/whitelist.md",
        topics_path="data/topics.md",
        prompts_dir="ai/prompts",
        proxy_url=None,
        log_level="INFO",
    )

    history = SimpleNamespace(init_db=AsyncMock())
    whitelist = SimpleNamespace(load=AsyncMock())
    topic_selector = SimpleNamespace(load=AsyncMock())
    fake_telegram_client = SimpleNamespace(
        run_until_disconnected=AsyncMock(),
        add_event_handler=AsyncMock(),
    )
    fake_userbot_client = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        client=fake_telegram_client,
    )
    scheduler = SimpleNamespace(start=lambda: None, shutdown=AsyncMock())

    monkeypatch.setattr(run, "get_settings", lambda: settings)
    monkeypatch.setattr(run, "MessageHistory", lambda db_path: history)
    monkeypatch.setattr(run, "WhitelistFilter", lambda whitelist_path: whitelist)
    monkeypatch.setattr(run, "PromptLoader", lambda prompts_dir: object())
    monkeypatch.setattr(
        run,
        "GeminiClient",
        lambda api_key, proxy_url=None: SimpleNamespace(
            api_key=api_key,
            proxy_url=proxy_url,
        ),
    )
    monkeypatch.setattr(run, "TopicSelector", lambda topics_path: topic_selector)
    monkeypatch.setattr(run, "ConversationSession", lambda: object())
    monkeypatch.setattr(run, "AsyncIOScheduler", lambda: scheduler)
    monkeypatch.setattr(run, "UserBotClient", lambda **kwargs: fake_userbot_client)
    monkeypatch.setattr(run, "_register_handlers", AsyncMock())

    with caplog.at_level(logging.INFO):
        await run.main()

    messages = [record.getMessage() for record in caplog.records]
    assert any("Запуск приложения userbot" in message for message in messages)
    assert any("Планировщик запущен" in message for message in messages)
    assert any("Приложение остановлено" in message for message in messages)


@pytest.mark.asyncio
async def test_handle_new_message_logs_successful_processing(caplog):
    """Проверяет логирование успешной обработки входящего сообщения."""
    whitelist = WhitelistFilter(whitelist_path="unused")
    whitelist.user_ids = {123}

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[{"role": "user", "text": "Предыдущее"}]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ бота"))
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())

    with caplog.at_level(logging.INFO):
        await handle_new_message(
            event=event,
            whitelist=whitelist,
            history=history,
            prompt_loader=prompt_loader,
            gemini_client=gemini_client,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("Обработка входящего сообщения от user_id=123" in message for message in messages)
    assert any("Ответ пользователю user_id=123 отправлен" in message for message in messages)


@pytest.mark.asyncio
async def test_handle_new_message_logs_whitelist_skip(caplog):
    """Проверяет логирование пропуска сообщения для пользователя вне whitelist."""
    whitelist = WhitelistFilter(whitelist_path="unused")
    whitelist.user_ids = {123}
    event = SimpleNamespace(sender_id=999, raw_text="Привет", respond=AsyncMock())

    with caplog.at_level(logging.INFO):
        await handle_new_message(
            event=event,
            whitelist=whitelist,
            history=None,
            prompt_loader=None,
            gemini_client=None,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("не входит в whitelist" in message for message in messages)


@pytest.mark.asyncio
async def test_handle_new_message_logs_gemini_error_and_fallback(caplog):
    """Проверяет логирование ошибки Gemini и отправку fallback-ответа."""
    whitelist = WhitelistFilter(whitelist_path="unused")
    whitelist.user_ids = {123}

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[{"role": "user", "text": "Предыдущее"}]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(side_effect=RuntimeError("503 UNAVAILABLE")))
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())

    with caplog.at_level(logging.ERROR):
        await handle_new_message(
            event=event,
            whitelist=whitelist,
            history=history,
            prompt_loader=prompt_loader,
            gemini_client=gemini_client,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("Ошибка генерации ответа для user_id=123" in message for message in messages)
    event.respond.assert_awaited_once()


@pytest.mark.asyncio
async def test_message_history_logs_save_and_fetch(caplog):
    """Проверяет логирование операций чтения и записи истории."""
    history = MessageHistory(":memory:")

    with caplog.at_level(logging.INFO):
        await history.init_db()
        await history.save_message(123, "user", "Привет")
        result = await history.get_history(123)

    messages = [record.getMessage() for record in caplog.records]
    assert result == [{"role": "user", "text": "Привет"}]
    assert any("Инициализация базы истории сообщений" in message for message in messages)
    assert any("Сообщение сохранено в историю для user_id=123" in message for message in messages)
    assert any("Загружена история сообщений для user_id=123" in message for message in messages)


@pytest.mark.asyncio
async def test_prompt_loader_logs_file_loading(tmp_path, caplog):
    """Проверяет логирование загрузки промта из файла."""
    (tmp_path / "system.md").write_text("Системный промт", encoding="utf-8")
    loader = PromptLoader(str(tmp_path))

    with caplog.at_level(logging.INFO):
        content = await loader.load("system")

    messages = [record.getMessage() for record in caplog.records]
    assert content == "Системный промт"
    assert any("Загрузка промта 'system'" in message for message in messages)


@pytest.mark.asyncio
async def test_gemini_client_logs_generation(monkeypatch, caplog):
    """Проверяет логирование запуска генерации ответа через Gemini."""
    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="Ответ модели")

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.models = FakeModels()

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    fake_types = SimpleNamespace(GenerateContentConfig=FakeGenerateContentConfig)
    fake_genai = SimpleNamespace(Client=FakeClient, types=fake_types)

    monkeypatch.setattr("ai.gemini._import_google_genai", lambda: fake_genai)

    client = GeminiClient(api_key="test_key_123", model_name="gemini-2.5-flash")

    with caplog.at_level(logging.INFO):
        result = await client.generate_reply(
            system_prompt="Системная роль",
            history=[{"role": "user", "text": "Привет"}],
            user_message="Как дела?",
        )

    messages = [record.getMessage() for record in caplog.records]
    assert result == "Ответ модели"
    assert any("Запуск генерации ответа через Gemini" in message for message in messages)
    assert any("Ответ Gemini успешно получен" in message for message in messages)


@pytest.mark.asyncio
async def test_topic_selector_and_session_log_lifecycle(tmp_path, caplog):
    """Проверяет логирование загрузки тем и жизненного цикла сессии."""
    topics_path = tmp_path / "topics.md"
    topics_path.write_text("Тема 1\nТема 2\n", encoding="utf-8")

    selector = TopicSelector(str(topics_path))
    session = ConversationSession(duration_minutes=30)

    with caplog.at_level(logging.INFO):
        await selector.load()
        topic = await selector.pick_random()
        session.start(topic)
        session.stop()

    messages = [record.getMessage() for record in caplog.records]
    assert topic in {"Тема 1", "Тема 2"}
    assert any("Загрузка тем разговора" in message for message in messages)
    assert any("Выбрана тема разговора" in message for message in messages)
    assert any("Сессия разговора запущена" in message for message in messages)
    assert any("Сессия разговора остановлена" in message for message in messages)
