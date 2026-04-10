"""Тесты для обработчиков сообщений и фильтра whitelist."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai.gemini import GeminiTemporaryError
from userbot.handlers import WhitelistFilter, handle_new_message


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch):
    """Убирает реальные thread-вызовы из unit-тестов обработчика."""

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("userbot.handlers.asyncio.to_thread", fake_to_thread)


@pytest.fixture(autouse=True)
def skip_response_delay(monkeypatch):
    """Отключает реальную задержку перед отправкой ответа в unit-тестах."""

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("userbot.handlers.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("userbot.handlers.random.uniform", lambda _a, _b: 0.0)


async def test_whitelist_allows_listed_user():
    """Проверяет, что user_id из whitelist пропускается фильтром."""
    wf = WhitelistFilter(user_ids={123456789, 987654321})
    assert await wf.is_allowed(user_id=123456789) is True


async def test_whitelist_blocks_unlisted_user():
    """Проверяет, что user_id не из whitelist блокируется фильтром."""
    wf = WhitelistFilter(user_ids={123456789})
    assert await wf.is_allowed(user_id=999999999) is False


async def test_whitelist_stores_all_ids():
    """Проверяет, что все переданные user_id хранятся в фильтре."""
    wf = WhitelistFilter(user_ids={111111111, 222222222, 333333333})
    assert len(wf.user_ids) == 3


async def test_whitelist_empty_allows_nobody():
    """Проверяет, что пустой whitelist блокирует всех."""
    wf = WhitelistFilter(user_ids=set())
    assert await wf.is_allowed(user_id=123456789) is False


async def test_handle_new_message_replies_for_whitelisted_user():
    """Проверяет, что обработчик генерирует и отправляет ответ разрешённому пользователю."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[{"role": "user", "text": "Предыдущее"}]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ бота"))
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
    )

    gemini_client.generate_reply.assert_awaited_once()
    event.respond.assert_awaited_once_with("Ответ бота")
    assert history.save_message.await_count == 2


async def test_handle_new_message_skips_non_whitelisted_user():
    """Проверяет, что неразрешённому пользователю бот не отвечает."""
    whitelist = WhitelistFilter(user_ids={123})

    gemini_client = SimpleNamespace(generate_reply=AsyncMock())
    event = SimpleNamespace(sender_id=999, raw_text="Привет", respond=AsyncMock())

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=None,
        prompt_loader=None,
        gemini_client=gemini_client,
    )

    gemini_client.generate_reply.assert_not_awaited()
    event.respond.assert_not_awaited()


async def test_handle_new_message_silent_on_gemini_error():
    """Проверяет, что при ошибке Gemini бот ничего не отправляет в Telegram."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[{"role": "user", "text": "Предыдущее"}]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(side_effect=RuntimeError("503 UNAVAILABLE")))
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
    )

    gemini_client.generate_reply.assert_awaited_once()
    event.respond.assert_not_awaited()
    history.save_message.assert_not_awaited()


async def test_handle_new_message_silent_on_temporary_gemini_error():
    """Проверяет, что временная ошибка Gemini тоже не вызывает ответа в Telegram."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[{"role": "user", "text": "Предыдущее"}]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(side_effect=GeminiTemporaryError("Gemini временно недоступен")))
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
    )

    event.respond.assert_not_awaited()
    history.save_message.assert_not_awaited()


async def test_wind_down_hint_included_when_few_minutes_remain():
    """Проверяет, что wind-down hint добавляется в промт когда осталось ≤5 минут."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа", "Осталось {remaining} мин."])
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ"))
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())

    session = SimpleNamespace(remaining_minutes=lambda: 3)

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        conversation_session=session,
    )

    call_args = gemini_client.generate_reply.call_args
    system_prompt_used = call_args.kwargs.get("system_prompt") or call_args.args[0]
    assert "Осталось 3 мин." in system_prompt_used


async def test_wind_down_hint_not_included_when_time_is_enough():
    """Проверяет, что wind-down hint не добавляется когда времени больше 5 минут."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ"))
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())

    session = SimpleNamespace(remaining_minutes=lambda: 15)

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        conversation_session=session,
    )

    call_args = gemini_client.generate_reply.call_args
    system_prompt_used = call_args.kwargs.get("system_prompt") or call_args.args[0]
    assert "wind_down" not in system_prompt_used
    assert "Осталось" not in system_prompt_used


async def test_wind_down_hint_not_included_when_no_session():
    """Проверяет, что при отсутствии сессии wind-down hint не добавляется."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ"))
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        conversation_session=None,
    )

    gemini_client.generate_reply.assert_awaited_once()
    call_args = gemini_client.generate_reply.call_args
    system_prompt_used = call_args.kwargs.get("system_prompt") or call_args.args[0]
    assert "Осталось" not in system_prompt_used


async def test_handle_new_message_accepts_chat_id_from_event():
    """Проверяет, что наличие chat_id не мешает штатной обработке сообщения."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ бота"))
    event = SimpleNamespace(
        sender_id=123,
        chat_id=-1009876543210,
        raw_text="Привет",
        respond=AsyncMock(),
    )

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
    )

    gemini_client.generate_reply.assert_awaited_once()
    event.respond.assert_awaited_once_with("Ответ бота")
