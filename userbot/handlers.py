"""Обработчики входящих сообщений Telegram и фильтр пользователей по whitelist."""

import asyncio
import inspect
import logging
import random
from typing import Any

from ai.gemini import GeminiClient, GeminiGenerationError, GeminiTemporaryError, PromptLoader
from ai.history import MessageHistory
from userbot.scheduler import SilenceWatcher


logger = logging.getLogger(__name__)


class WhitelistFilter:
    """Фильтрует входящие сообщения по списку разрешённых Telegram user_id."""

    def __init__(self, user_ids: set[int]) -> None:
        """
        Инициализирует фильтр.

        Args:
            user_ids: Множество разрешённых Telegram user_id.
        """
        self.user_ids = user_ids
        logger.info("Whitelist инициализирован: %s user_id", len(self.user_ids))

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
    silence_watcher: SilenceWatcher | None = None,
    conversation_session: object | None = None,
    group_chat_id: int | None = None,
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
    chat_id = _extract_chat_id(event)

    telethon_client = getattr(event, "client", None)
    effective_group_chat_id = group_chat_id
    if effective_group_chat_id is None:
        effective_group_chat_id = getattr(telethon_client, "group_chat_id", None)
    if effective_group_chat_id is not None and chat_id != effective_group_chat_id:
        logger.info(
            "Сообщение от user_id=%s в chat_id=%s пропущено: чат не совпадает с целевой группой",
            sender_id,
            chat_id,
        )
        return

    # Фиксируем активность только для целевой группы — до проверки whitelist
    _sw = silence_watcher or getattr(telethon_client, "silence_watcher", None)
    if _sw is not None:
        _sw.update_last_activity()

    if not await whitelist.is_allowed(sender_id):
        logger.info(
            "Сообщение от user_id=%s в chat_id=%s пропущено: пользователь не входит в whitelist",
            sender_id,
            chat_id,
        )
        return

    logger.info("Обработка входящего сообщения от user_id=%s в chat_id=%s", sender_id, chat_id)
    user_message = _extract_message_text(event)
    if not user_message:
        logger.info("Сообщение от user_id=%s в chat_id=%s пропущено: текст не найден", sender_id, chat_id)
        return

    history = history or getattr(telethon_client, "message_history", None)
    prompt_loader = prompt_loader or getattr(telethon_client, "prompt_loader", None)
    gemini_client = gemini_client or getattr(telethon_client, "gemini_client", None)
    conversation_session = conversation_session or getattr(telethon_client, "conversation_session", None)
    if history is None or prompt_loader is None or gemini_client is None:
        logger.warning(
            "Сообщение от user_id=%s в chat_id=%s не обработано: отсутствуют runtime-зависимости",
            sender_id,
            chat_id,
        )
        return

    history_items = await history.get_history(sender_id)
    logger.info(
        "История для user_id=%s в chat_id=%s загружена: %s сообщений",
        sender_id,
        chat_id,
        len(history_items),
    )
    system_prompt = await prompt_loader.load("system")
    reply_prompt = await prompt_loader.load("reply")
    wind_down_hint = ""
    if conversation_session is not None and conversation_session.is_active():
        remaining = conversation_session.remaining_minutes()
        if remaining is not None and remaining <= 2:
            hint_template = await prompt_loader.load("wind_down_hint")
            wind_down_hint = hint_template.format(remaining=remaining)
            logger.info(
                "Wind-down hint активирован для user_id=%s: осталось %s мин", sender_id, remaining
            )
    logger.info("Промты для ответа user_id=%s в chat_id=%s загружены", sender_id, chat_id)
    full_prompt = f"{system_prompt}\n\n{reply_prompt}"
    if wind_down_hint:
        full_prompt = f"{full_prompt}\n\n{wind_down_hint}"
    try:
        reply_text = await gemini_client.generate_reply(
            system_prompt=full_prompt,
            history=history_items,
            user_message=user_message,
        )
    except GeminiTemporaryError as exc:
        logger.warning("Gemini временно недоступен для user_id=%s в chat_id=%s: %s", sender_id, chat_id, exc)
        return
    except GeminiGenerationError as exc:
        logger.error("Ошибка генерации ответа для user_id=%s в chat_id=%s: %s", sender_id, chat_id, exc)
        return
    except Exception:
        logger.exception("Ошибка генерации ответа для user_id=%s в chat_id=%s", sender_id, chat_id)
        return
    logger.info("Ответ для user_id=%s в chat_id=%s сгенерирован", sender_id, chat_id)

    await history.save_message(sender_id, "user", user_message)
    await history.save_message(sender_id, "assistant", reply_text)
    logger.info("История диалога для user_id=%s в chat_id=%s сохранена", sender_id, chat_id)
    await _send_response(event, reply_text)
    logger.info("Ответ пользователю user_id=%s в chat_id=%s отправлен", sender_id, chat_id)


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


def _extract_chat_id(event: object) -> int | None:
    """Извлекает chat_id из Telethon-события, если он доступен."""
    direct_chat_id = getattr(event, "chat_id", None)
    if isinstance(direct_chat_id, int):
        return direct_chat_id

    chat = getattr(event, "chat", None)
    chat_id = getattr(chat, "id", None)
    if isinstance(chat_id, int):
        return chat_id

    message = getattr(event, "message", None)
    peer_id = getattr(message, "peer_id", None)
    channel_id = getattr(peer_id, "channel_id", None)
    if isinstance(channel_id, int):
        return channel_id
    chat_peer_id = getattr(peer_id, "chat_id", None)
    if isinstance(chat_peer_id, int):
        return chat_peer_id

    return None


async def _send_response(event: object, text: str) -> None:
    """Отправляет ответ через доступный метод события с рандомной задержкой.

    Если входящее сообщение само является reply — отвечает с цитатой (reply),
    иначе отправляет обычное сообщение в чат (respond).
    """
    delay = random.uniform(10, 30)
    logger.info("Задержка перед отправкой ответа: %.1f сек", delay)
    await asyncio.sleep(delay)

    is_reply = bool(getattr(event, "is_reply", False))
    method_name = "reply" if is_reply else "respond"
    method = getattr(event, method_name, None)
    if method is not None:
        logger.info("Отправка ответа через метод %s (is_reply=%s)", method_name, is_reply)
        result = method(text)
        if inspect.isawaitable(result):
            await result
        return
    logger.warning("Ответ не отправлен: у события нет метода %s", method_name)
