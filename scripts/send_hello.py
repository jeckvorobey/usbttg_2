"""Разовая отправка сообщения через существующую сессию Telethon."""

import asyncio

from core.config import load_settings_or_exit
from userbot.client import _build_telegram_client, _build_proxy_settings


# Укажи @username или номер телефона (+7XXXXXXXXXX) второго аккаунта
TARGET = "@sergeywebdev_test"
MESSAGE = "привет"


async def main() -> None:
    settings = load_settings_or_exit()

    client = _build_telegram_client(
        settings.session_string,
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
