"""Обработчики входящих сообщений Telegram и фильтр пользователей по whitelist."""

import asyncio
import inspect
import logging
from pathlib import Path
from typing import Any

from ai.gemini import GeminiClient, GeminiGenerationError, GeminiTemporaryError, PromptLoader
from ai.history import MessageHistory


logger = logging.getLogger(__name__)
GENERATION_ERROR_REPLY = (
    "Сейчас не могу ответить из-за временной ошибки сервиса. Попробуй ещё раз позже."
)


class WhitelistFilter:
    """Фильтрует входящие сообщения по списку разрешённых Telegram user_id."""

    def __init__(self, whitelist_path: str) -> None:
        """
        Инициализирует фильтр.

        Args:
            whitelist_path: Путь к файлу whitelist.md со списком user_id.
        """
        self.whitelist_path = whitelist_path
        self.user_ids: set[int] = set()

    async def load(self) -> None:
        """
        Загружает список user_id из whitelist файла.

        Строки начинающиеся на '#' считаются комментариями и игнорируются.
        Пустые строки также игнорируются.
        """
        path = Path(self.whitelist_path)
        logger.info("Загрузка whitelist из %s", path)
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        user_ids: set[int] = set()
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line == "---":
                continue
            if line.isdigit():
                user_ids.add(int(line))
        self.user_ids = user_ids
        logger.info("Whitelist загружен: %s user_id", len(self.user_ids))

    async def is_allowed(self, user_id: int) -> bool:
        """
        Проверяет, разрешён ли пользователь — находится ли его user_id в whitelist.

        Args:
            user_id: Telegram ID пользователя для проверки.

        Returns:
            True если user_id есть в whitelist, иначе False.
        """
        allowed = user_id in self.user_ids
        logger.debug("Проверка whitelist для user_id=%s: %s", user_id, allowed)
        return allowed


async def handle_new_message(
    event: object,
    whitelist: WhitelistFilter,
    history: MessageHistory | None = None,
    prompt_loader: PromptLoader | None = None,
    gemini_client: GeminiClient | None = None,
) -> None:
    """
    Обработчик входящего сообщения в группе.

    Проверяет whitelist и инициирует генерацию ответа через Gemini.

    Args:
        event: Telethon событие нового сообщения.
        whitelist: Экземпляр фильтра whitelist для проверки отправителя.
    """
    sender_id = getattr(event, "sender_id", None)
    if sender_id is None:
        logger.warning("Сообщение пропущено: sender_id отсутствует")
        return

    if not await whitelist.is_allowed(sender_id):
        logger.info("Сообщение от user_id=%s пропущено: пользователь не входит в whitelist", sender_id)
        return

    logger.info("Обработка входящего сообщения от user_id=%s", sender_id)
    user_message = _extract_message_text(event)
    if not user_message:
        logger.info("Сообщение от user_id=%s пропущено: текст не найден", sender_id)
        return

    telethon_client = getattr(event, "client", None)
    history = history or getattr(telethon_client, "message_history", None)
    prompt_loader = prompt_loader or getattr(telethon_client, "prompt_loader", None)
    gemini_client = gemini_client or getattr(telethon_client, "gemini_client", None)
    if history is None or prompt_loader is None or gemini_client is None:
        logger.warning("Сообщение от user_id=%s не обработано: отсутствуют runtime-зависимости", sender_id)
        return

    history_items = await history.get_history(sender_id)
    logger.info("История для user_id=%s загружена: %s сообщений", sender_id, len(history_items))
    system_prompt = await prompt_loader.load("system")
    reply_prompt = await prompt_loader.load("reply")
    logger.info("Промты для ответа user_id=%s загружены", sender_id)
    try:
        reply_text = await gemini_client.generate_reply(
            system_prompt=f"{system_prompt}\n\n{reply_prompt}",
            history=history_items,
            user_message=user_message,
        )
    except GeminiTemporaryError as exc:
        logger.warning("Gemini временно недоступен для user_id=%s: %s", sender_id, exc)
        await _send_response(event, GENERATION_ERROR_REPLY)
        logger.info("Пользователю user_id=%s отправлен fallback-ответ после ошибки генерации", sender_id)
        return
    except GeminiGenerationError as exc:
        logger.error("Ошибка генерации ответа для user_id=%s: %s", sender_id, exc)
        await _send_response(event, GENERATION_ERROR_REPLY)
        logger.info("Пользователю user_id=%s отправлен fallback-ответ после ошибки генерации", sender_id)
        return
    except Exception:
        logger.exception("Ошибка генерации ответа для user_id=%s", sender_id)
        await _send_response(event, GENERATION_ERROR_REPLY)
        logger.info("Пользователю user_id=%s отправлен fallback-ответ после ошибки генерации", sender_id)
        return
    logger.info("Ответ для user_id=%s сгенерирован", sender_id)

    await history.save_message(sender_id, "user", user_message)
    await history.save_message(sender_id, "assistant", reply_text)
    logger.info("История диалога для user_id=%s сохранена", sender_id)
    await _send_response(event, reply_text)
    logger.info("Ответ пользователю user_id=%s отправлен", sender_id)


def _extract_message_text(event: object) -> str:
    """Извлекает текст сообщения из Telethon-события."""
    for attribute in ("raw_text", "text"):
        value = getattr(event, attribute, None)
        if isinstance(value, str) and value.strip():
            logger.debug("Текст сообщения извлечён из атрибута %s", attribute)
            return value.strip()

    message = getattr(event, "message", None)
    nested_text = getattr(message, "message", None)
    if isinstance(nested_text, str):
        logger.debug("Текст сообщения извлечён из вложенного объекта message")
        return nested_text.strip()
    logger.debug("Текст сообщения не найден в событии")
    return ""


async def _send_response(event: object, text: str) -> None:
    """Отправляет ответ через доступный метод события."""
    for method_name in ("respond", "reply"):
        method = getattr(event, method_name, None)
        if method is None:
            continue
        logger.info("Отправка ответа через метод %s", method_name)
        result = method(text)
        if inspect.isawaitable(result):
            await result
        return
    logger.warning("Ответ не отправлен: у события нет методов respond/reply")
