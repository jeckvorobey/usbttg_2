"""Тесты для обработчиков сообщений и фильтра whitelist."""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai.gemini import GeminiTemporaryError
from userbot.handlers import WhitelistFilter, handle_new_message


async def test_whitelist_allows_listed_user():
    """Проверяет, что user_id из whitelist пропускается фильтром."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("# Разрешённые пользователи\n123456789\n987654321\n")
        tmp_path = f.name

    try:
        wf = WhitelistFilter(whitelist_path=tmp_path)
        await wf.load()
        assert await wf.is_allowed(user_id=123456789) is True
    finally:
        os.unlink(tmp_path)


async def test_whitelist_blocks_unlisted_user():
    """Проверяет, что user_id не из whitelist блокируется фильтром."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("123456789\n")
        tmp_path = f.name

    try:
        wf = WhitelistFilter(whitelist_path=tmp_path)
        await wf.load()
        assert await wf.is_allowed(user_id=000000000) is False
    finally:
        os.unlink(tmp_path)


async def test_whitelist_loads_multiple_ids():
    """Проверяет, что все user_id из файла загружаются корректно."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("111111111\n222222222\n333333333\n")
        tmp_path = f.name

    try:
        wf = WhitelistFilter(whitelist_path=tmp_path)
        await wf.load()
        assert len(wf.user_ids) == 3
    finally:
        os.unlink(tmp_path)


async def test_whitelist_ignores_comment_lines():
    """Проверяет, что строки с комментариями (начинающиеся на #) игнорируются."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("# Это комментарий\n111111111\n# Ещё комментарий\n222222222\n")
        tmp_path = f.name

    try:
        wf = WhitelistFilter(whitelist_path=tmp_path)
        await wf.load()
        assert len(wf.user_ids) == 2
        assert await wf.is_allowed(user_id=111111111) is True
    finally:
        os.unlink(tmp_path)


async def test_whitelist_ignores_empty_lines():
    """Проверяет, что пустые строки в файле игнорируются."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write("\n111111111\n\n222222222\n\n")
        tmp_path = f.name

    try:
        wf = WhitelistFilter(whitelist_path=tmp_path)
        await wf.load()
        assert len(wf.user_ids) == 2
    finally:
        os.unlink(tmp_path)


async def test_handle_new_message_replies_for_whitelisted_user():
    """Проверяет, что обработчик генерирует и отправляет ответ разрешённому пользователю."""
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
    whitelist = WhitelistFilter(whitelist_path="unused")
    whitelist.user_ids = {123}

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


async def test_handle_new_message_sends_fallback_on_gemini_error():
    """Проверяет, что при ошибке Gemini обработчик отправляет безопасный fallback."""
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

    await handle_new_message(
        event=event,
        whitelist=whitelist,
        history=history,
        prompt_loader=prompt_loader,
        gemini_client=gemini_client,
    )

    gemini_client.generate_reply.assert_awaited_once()
    event.respond.assert_awaited_once_with("Сейчас не могу ответить из-за временной ошибки сервиса. Попробуй ещё раз позже.")
    history.save_message.assert_not_awaited()


async def test_handle_new_message_sends_fallback_on_temporary_gemini_error():
    """Проверяет, что временная ошибка Gemini тоже ведёт к безопасному fallback."""
    whitelist = WhitelistFilter(whitelist_path="unused")
    whitelist.user_ids = {123}

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

    event.respond.assert_awaited_once_with(
        "Сейчас не могу ответить из-за временной ошибки сервиса. Попробуй ещё раз позже."
    )
    history.save_message.assert_not_awaited()
