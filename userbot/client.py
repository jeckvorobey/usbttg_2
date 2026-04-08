"""Инициализация и управление Telethon клиентом."""

from urllib.parse import urlparse
from typing import Any


class UserBotClient:
    """Управляет подключением к Telegram через Telethon MTProto."""

    def __init__(
        self,
        session_name: str,
        api_id: int,
        api_hash: str,
        proxy_url: str | None = None,
    ) -> None:
        """
        Инициализирует Telethon клиент.

        Args:
            session_name: Имя файла сессии (без расширения .session).
            api_id: Telegram API ID (получить на https://my.telegram.org).
            api_hash: Telegram API Hash.
            proxy_url: URL proxy для подключения к Telegram.
        """
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.proxy_url = proxy_url
        self._client: Any | None = None

    async def start(self) -> None:
        """Запускает клиент и устанавливает подключение к Telegram."""
        if self._client is None:
            self._client = _build_telegram_client(
                self.session_name,
                self.api_id,
                self.api_hash,
                proxy=_build_proxy_settings(self.proxy_url),
            )
        await self._client.start()

    async def stop(self) -> None:
        """Корректно останавливает клиент и разрывает соединение."""
        if self._client is None:
            return
        if not self._client.is_connected():
            return
        await self._client.disconnect()

    @property
    def client(self) -> Any | None:
        """Возвращает внутренний экземпляр Telethon-клиента."""
        return self._client


def _build_telegram_client(
    session_name: str,
    api_id: int,
    api_hash: str,
    proxy: dict[str, Any] | None = None,
) -> Any:
    """Создаёт экземпляр TelegramClient с ленивым импортом Telethon."""
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError("Пакет telethon не установлен") from exc

    return TelegramClient(session_name, api_id, api_hash, proxy=proxy)


def _build_proxy_settings(proxy_url: str | None) -> dict[str, Any] | None:
    """Преобразует proxy URL в формат, поддерживаемый Telethon."""
    if not proxy_url:
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

    return {
        "proxy_type": proxy_type,
        "addr": parsed.hostname,
        "port": parsed.port,
        "username": parsed.username,
        "password": parsed.password,
        "rdns": True,
    }
