"""Тесты для обработчиков сообщений и фильтра whitelist."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ai.reply_rules import ReplyRule
from ai.gemini import GeminiTemporaryError
from userbot.handlers import WhitelistFilter, _send_response, handle_new_message


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
    """Проверяет, что обработчик отвечает только внутри активной сессии."""
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
    session = SimpleNamespace(is_active=lambda: True, remaining_minutes=lambda: 6)
    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        conversation_session=session,
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
    session = SimpleNamespace(is_active=lambda: True, remaining_minutes=lambda: 6)
    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        conversation_session=session,
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
    session = SimpleNamespace(is_active=lambda: True, remaining_minutes=lambda: 6)
    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        conversation_session=session,
    )

    event.respond.assert_not_awaited()
    history.save_message.assert_not_awaited()


async def test_handle_new_message_skips_when_session_expires_after_generation():
    """Проверяет, что после истечения сессии по итогам генерации ответ не отправляется и не сохраняется."""
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
    session = Mock()
    session.is_active.side_effect = [True, False]
    session.remaining_minutes.return_value = 6

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        conversation_session=session,
    )

    gemini_client.generate_reply.assert_awaited_once()
    event.respond.assert_not_awaited()
    history.save_message.assert_not_awaited()


async def test_wind_down_hint_included_when_two_minutes_remain():
    """Проверяет, что wind-down hint добавляется в промт когда осталось 2 минуты."""
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

    session = SimpleNamespace(is_active=lambda: True, remaining_minutes=lambda: 2)

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
    assert "Осталось 2 мин." in system_prompt_used


async def test_wind_down_hint_not_included_when_time_is_enough():
    """Проверяет, что wind-down hint не добавляется когда времени больше 2 минут."""
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

    session = SimpleNamespace(is_active=lambda: True, remaining_minutes=lambda: 3)

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


async def test_handle_new_message_replies_when_session_is_missing():
    """Проверяет, что отсутствие объекта сессии не блокирует ответ whitelisted отправителю."""
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
    event.respond.assert_awaited_once_with("Ответ")
    history.get_history.assert_awaited_once()

async def test_handle_new_message_starts_session_when_it_is_inactive():
    """Проверяет, что первое сообщение из whitelist запускает локальную сессию."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(load=AsyncMock(side_effect=["Системный промт", "Промт ответа"]))
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ"))
    silence_watcher = SimpleNamespace(update_last_activity=Mock())
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())
    session_state = {"active": False}

    def start_session(_topic: str) -> None:
        session_state["active"] = True

    session = SimpleNamespace(
        is_active=lambda: session_state["active"],
        remaining_minutes=lambda: 6 if session_state["active"] else None,
        start=Mock(side_effect=start_session),
    )

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        silence_watcher=silence_watcher,
        conversation_session=session,
        scheduler_enabled=False,
    )

    silence_watcher.update_last_activity.assert_called_once_with()
    session.start.assert_called_once_with("Привет")
    gemini_client.generate_reply.assert_awaited_once()
    event.respond.assert_awaited_once_with("Ответ")
    history.get_history.assert_awaited_once()
    assert history.save_message.await_count == 2


async def test_handle_new_message_does_not_start_session_from_message_when_scheduler_enabled():
    """Проверяет, что при включённом планировщике без активной сессии сообщение пропускается."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(load=AsyncMock(side_effect=["Системный промт", "Промт ответа"]))
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ"))
    silence_watcher = SimpleNamespace(update_last_activity=Mock())
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())
    session = SimpleNamespace(is_active=lambda: False, remaining_minutes=lambda: None, start=Mock())

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        silence_watcher=silence_watcher,
        conversation_session=session,
        scheduler_enabled=True,
    )

    silence_watcher.update_last_activity.assert_called_once_with()
    session.start.assert_not_called()
    gemini_client.generate_reply.assert_not_awaited()
    event.respond.assert_not_awaited()
    history.get_history.assert_not_awaited()
    history.save_message.assert_not_awaited()


async def test_handle_new_message_skips_when_dnd_is_active():
    """Проверяет, что в DND бот не отвечает даже при активной сессии."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(load=AsyncMock(side_effect=["Системный промт", "Промт ответа"]))
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ"))
    silence_watcher = SimpleNamespace(update_last_activity=Mock())
    event = SimpleNamespace(sender_id=123, raw_text="Привет", respond=AsyncMock())
    session = SimpleNamespace(is_active=lambda: True, remaining_minutes=lambda: 6)

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        silence_watcher=silence_watcher,
        conversation_session=session,
        dnd_hours_utc="23-7",
        now_utc_factory=lambda: datetime(2026, 4, 10, 23, 30, tzinfo=UTC),
    )

    silence_watcher.update_last_activity.assert_called_once_with()
    gemini_client.generate_reply.assert_not_awaited()
    event.respond.assert_not_awaited()
    history.get_history.assert_not_awaited()


async def test_handle_new_message_ignores_other_group_chat():
    """Проверяет, что сообщения из другого чата не учитываются и не обрабатываются."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(load=AsyncMock())
    gemini_client = SimpleNamespace(generate_reply=AsyncMock())
    silence_watcher = SimpleNamespace(update_last_activity=Mock())
    event = SimpleNamespace(sender_id=123, chat_id=-100999, raw_text="Привет", respond=AsyncMock())

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        silence_watcher=silence_watcher,
        group_chat_id=-100555,
    )

    silence_watcher.update_last_activity.assert_not_called()
    gemini_client.generate_reply.assert_not_awaited()
    event.respond.assert_not_awaited()
    history.get_history.assert_not_awaited()


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
    session = SimpleNamespace(is_active=lambda: True, remaining_minutes=lambda: 6)

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
        group_chat_id=-1009876543210,
        conversation_session=session,
    )

    gemini_client.generate_reply.assert_awaited_once()
    event.respond.assert_awaited_once_with("Ответ бота")


async def test_handle_new_message_adds_reply_rule_hint_for_exchange_question():
    """Проверяет, что в промт добавляется служебная подсказка по обмену валюты."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    reply_rules_loader = SimpleNamespace(
        find_matches=Mock(
            return_value=[
                ReplyRule(
                    name="Обмен валюты",
                    triggers=("обмен",),
                    instruction=(
                        "Если сообщение действительно про обмен валюты, можно мягко "
                        "упомянуть @AntEx_support и отзывы: "
                        "https://t.me/+ui-tQ4T-jrNlNmQy."
                    ),
                )
            ]
        )
    )
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ бота"))
    event = SimpleNamespace(sender_id=123, raw_text="Где лучше менять доллары?", respond=AsyncMock())
    session = SimpleNamespace(is_active=lambda: True, remaining_minutes=lambda: 6)

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        reply_rules_loader=reply_rules_loader,
        gemini_client=gemini_client,
        conversation_session=session,
    )

    call_args = gemini_client.generate_reply.call_args
    system_prompt_used = call_args.kwargs.get("system_prompt") or call_args.args[0]
    assert "Дополнительные указания для этого сообщения:" in system_prompt_used
    assert "@AntEx_support" in system_prompt_used
    assert "https://t.me/+ui-tQ4T-jrNlNmQy" in system_prompt_used


async def test_handle_new_message_does_not_add_reply_rule_hint_without_match():
    """Проверяет, что без совпадения дополнительных указаний в промте нет."""
    whitelist = WhitelistFilter(user_ids={123})

    history = SimpleNamespace(
        get_history=AsyncMock(return_value=[]),
        save_message=AsyncMock(),
    )
    prompt_loader = SimpleNamespace(
        load=AsyncMock(side_effect=["Системный промт", "Промт ответа"])
    )
    reply_rules_loader = SimpleNamespace(find_matches=Mock(return_value=[]))
    gemini_client = SimpleNamespace(generate_reply=AsyncMock(return_value="Ответ бота"))
    event = SimpleNamespace(sender_id=123, raw_text="Какой район лучше для жизни?", respond=AsyncMock())
    session = SimpleNamespace(is_active=lambda: True, remaining_minutes=lambda: 6)

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        reply_rules_loader=reply_rules_loader,
        gemini_client=gemini_client,
        conversation_session=session,
    )

    call_args = gemini_client.generate_reply.call_args
    system_prompt_used = call_args.kwargs.get("system_prompt") or call_args.args[0]
    assert "Дополнительные указания для этого сообщения:" not in system_prompt_used


async def test_send_response_uses_delay_between_30_and_60_seconds(monkeypatch):
    """Проверяет, что искусственная задержка ответа выбирается в диапазоне 30-60 секунд."""
    captured: dict[str, float] = {}

    async def fake_sleep(delay: float) -> None:
        captured["delay"] = delay

    monkeypatch.setattr("userbot.handlers.asyncio.sleep", fake_sleep)

    def fake_uniform(start: float, end: float) -> float:
        captured["uniform_start"] = start
        captured["uniform_end"] = end
        return 45.0

    monkeypatch.setattr("userbot.handlers.random.uniform", fake_uniform)

    event = SimpleNamespace(is_reply=False, respond=AsyncMock())

    await _send_response(event, "Ответ")

    assert captured["uniform_start"] == 30
    assert captured["uniform_end"] == 60
    assert captured["delay"] == 45.0
    event.respond.assert_awaited_once_with("Ответ")


async def test_send_response_cancels_when_session_expires_during_delay(monkeypatch):
    """Проверяет, что отправка отменяется, если сессия истекла во время искусственной задержки."""
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("userbot.handlers.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("userbot.handlers.random.uniform", lambda _start, _end: 45.0)

    event = SimpleNamespace(is_reply=False, respond=AsyncMock())
    session = SimpleNamespace(is_active=lambda: False)

    sent = await _send_response(
        event,
        "Ответ",
        conversation_session=session,
        sender_id=123,
        chat_id=-100555000111,
    )

    assert sent is False
    event.respond.assert_not_awaited()
