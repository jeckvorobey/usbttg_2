"""Инициализация и управление Telethon клиентом."""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


class UserBotClient:
    """Управляет подключением к Telegram через Telethon MTProto."""

    def __init__(
        self,
        session_string: str,
        api_id: int,
        api_hash: str,
        proxy_url: str | None = None,
    ) -> None:
        """
        Инициализирует Telethon клиент.

        Args:
            session_string: Строковая Telethon-сессия.
            api_id: Telegram API ID (получить на https://my.telegram.org).
            api_hash: Telegram API Hash.
            proxy_url: URL proxy для подключения к Telegram.
        """
        self.session_string = session_string
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy_url = proxy_url
        self._client: Any | None = None

    async def start(self) -> None:
        """Запускает клиент и устанавливает подключение к Telegram."""
        if self._client is None:
            logger.info("Создание Telegram-клиента из строковой сессии")
            self._client = _build_telegram_client(
                self.session_string,
                self.api_id,
                self.api_hash,
                proxy=_build_proxy_settings(self.proxy_url),
            )
        logger.info("Подключение Telegram-клиента запущено")
        await self._client.start()
        logger.info("Telegram-клиент успешно запущен")

    async def stop(self) -> None:
        """Корректно останавливает клиент и разрывает соединение."""
        if self._client is None:
            logger.info("Остановка Telegram-клиента пропущена: клиент не создан")
            return
        if not self._client.is_connected():
            logger.info("Остановка Telegram-клиента пропущена: клиент уже отключён")
            return
        logger.info("Отключение Telegram-клиента")
        await self._client.disconnect()
        logger.info("Telegram-клиент отключён")

    @property
    def client(self) -> Any | None:
        """Возвращает внутренний экземпляр Telethon-клиента."""
        return self._client

    async def run_until_disconnected(self) -> None:
        """Делегирует ожидание отключения внутреннему Telethon-клиенту."""
        client = self._require_client()
        await client.run_until_disconnected()

    async def update_profile(self, first_name: str | None = None, last_name: str | None = None) -> None:
        """Обновляет имя и фамилию текущего Telegram-профиля."""
        client = self._require_client()
        requests = _import_telethon_profile_requests()
        logger.info("Обновление профиля Telegram: first_name=%s last_name=%s", first_name, last_name)
        await client(
            requests.UpdateProfileRequest(
                first_name=first_name,
                last_name=last_name,
            )
        )

    async def update_username(self, username: str) -> None:
        """Обновляет username текущего Telegram-профиля."""
        client = self._require_client()
        requests = _import_telethon_profile_requests()
        logger.info("Обновление username Telegram: %s", username)
        await client(requests.UpdateUsernameRequest(username=username))

    async def update_avatar(self, avatar_path: str | Path) -> None:
        """Загружает и устанавливает новую аватарку текущего Telegram-профиля."""
        client = self._require_client()
        requests = _import_telethon_profile_requests()
        normalized_path = str(avatar_path)
        logger.info("Загрузка новой аватарки Telegram из %s", normalized_path)
        uploaded_file = await client.upload_file(normalized_path)
        await client(requests.UploadProfilePhotoRequest(file=uploaded_file))

    async def get_current_user(self) -> Any:
        """Возвращает данные текущего Telegram-пользователя."""
        client = self._require_client()
        logger.info("Запрос данных текущего Telegram-пользователя")
        return await client.get_me()

    async def join_group(self, target: str) -> Any:
        """Вступает в публичную группу или канал по username/ссылке."""
        client = self._require_client()
        requests = _import_telethon_channel_requests()
        logger.info("Вступление в публичную Telegram-группу: target=%s", target)
        return await client(requests.JoinChannelRequest(target))

    async def join_invite_link(self, invite_link: str) -> Any:
        """Вступает в приватную группу по invite link."""
        client = self._require_client()
        invite_hash = _extract_invite_hash(invite_link)
        if invite_hash is None:
            raise ValueError("Некорректный invite link Telegram")

        requests = _import_telethon_invite_requests()
        logger.info("Вступление в приватную Telegram-группу по invite link")
        return await client(requests.ImportChatInviteRequest(invite_hash))

    def _require_client(self) -> Any:
        """Возвращает активный Telethon-клиент или поднимает ошибку."""
        if self._client is None:
            raise RuntimeError("Telegram-клиент не запущен")
        return self._client


def _build_telegram_client(
    session_string: str,
    api_id: int,
    api_hash: str,
    proxy: dict[str, Any] | None = None,
) -> Any:
    """Создаёт экземпляр TelegramClient с ленивым импортом Telethon."""
    normalized_session_string = session_string.strip()
    if not normalized_session_string:
        raise ValueError("SESSION_STRING не должен быть пустым")

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as exc:
        raise RuntimeError("Пакет telethon не установлен") from exc

    logger.debug("Экземпляр TelegramClient создаётся через Telethon")
    return TelegramClient(StringSession(normalized_session_string), api_id, api_hash, proxy=proxy)


def _build_proxy_settings(proxy_url: str | None) -> dict[str, Any] | None:
    """Преобразует proxy URL в формат, поддерживаемый Telethon."""
    if not proxy_url:
        logger.debug("Proxy для Telethon не настроен")
        return None

    parsed = urlparse(proxy_url)
    if not parsed.scheme or not parsed.hostname or parsed.port is None:
        raise ValueError("Некорректный PROXY_URL: ожидается схема, хост и порт")

    proxy_type = parsed.scheme.lower()
    if proxy_type not in {"http", "socks4", "socks5"}:
        raise ValueError(
            "Неподдерживаемая схема proxy для Telethon. "
            "Используйте http://, socks4:// или socks5://"
        )

    logger.info("Подготовлены proxy-настройки для Telethon: %s://%s:%s", proxy_type, parsed.hostname, parsed.port)
    return {
        "proxy_type": proxy_type,
        "addr": parsed.hostname,
        "port": parsed.port,
        "username": parsed.username,
        "password": parsed.password,
        "rdns": True,
    }


def _import_telethon_profile_requests() -> Any:
    """Импортирует Telethon requests для операций над профилем."""
    try:
        from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
        from telethon.tl.functions.photos import UploadProfilePhotoRequest
    except ImportError as exc:
        raise RuntimeError("Пакет telethon не установлен") from exc

    return type(
        "TelethonProfileRequests",
        (),
        {
            "UpdateProfileRequest": UpdateProfileRequest,
            "UpdateUsernameRequest": UpdateUsernameRequest,
            "UploadProfilePhotoRequest": UploadProfilePhotoRequest,
        },
    )


def _import_telethon_channel_requests() -> Any:
    """Импортирует Telethon requests для вступления в публичные каналы и группы."""
    try:
        from telethon.tl.functions.channels import JoinChannelRequest
    except ImportError as exc:
        raise RuntimeError("Пакет telethon не установлен") from exc

    return type("TelethonChannelRequests", (), {"JoinChannelRequest": JoinChannelRequest})


def _import_telethon_invite_requests() -> Any:
    """Импортирует Telethon requests для приватных invite-ссылок."""
    try:
        from telethon.tl.functions.messages import ImportChatInviteRequest
    except ImportError as exc:
        raise RuntimeError("Пакет telethon не установлен") from exc

    return type("TelethonInviteRequests", (), {"ImportChatInviteRequest": ImportChatInviteRequest})


def _extract_invite_hash(invite_link: str) -> str | None:
    """Извлекает hash из Telegram invite link."""
    normalized = invite_link.strip()
    if not normalized:
        return None
    if normalized.startswith("https://t.me/+"):
        invite_hash = normalized.removeprefix("https://t.me/+")
        return invite_hash or None
    if normalized.startswith("http://t.me/+"):
        invite_hash = normalized.removeprefix("http://t.me/+")
        return invite_hash or None
    if normalized.startswith("https://t.me/joinchat/"):
        invite_hash = normalized.removeprefix("https://t.me/joinchat/")
        return invite_hash or None
    if normalized.startswith("http://t.me/joinchat/"):
        invite_hash = normalized.removeprefix("http://t.me/joinchat/")
        return invite_hash or None
    return None
