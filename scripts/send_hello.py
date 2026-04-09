"""Разовая отправка сообщения через существующую сессию Telethon."""

import asyncio
from pathlib import Path

from core.config import get_settings
from userbot.client import UserBotClient, _build_telegram_client, _build_proxy_settings


# Укажи @username или номер телефона (+7XXXXXXXXXX) второго аккаунта
TARGET = "@sergeywebdev_test"
MESSAGE = "привет"


def _build_session_path(session_name: str) -> str:
    session_path = Path(session_name)
    if session_path.parent != Path(".") or session_path.suffix == ".session":
        return str(session_path)
    return str(Path("data/sessions") / session_name)


async def main() -> None:
    settings = get_settings()
    session_path = _build_session_path(settings.session_name)

    client = _build_telegram_client(
        session_path,
        settings.api_id,
        settings.api_hash,
        proxy=_build_proxy_settings(settings.proxy_url),
    )

    await client.start()
    print(f"Отправляю '{MESSAGE}' → {TARGET}...")
    entity = await client.get_entity(TARGET)
    await client.send_message(entity, MESSAGE)
    print("Сообщение отправлено!")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
