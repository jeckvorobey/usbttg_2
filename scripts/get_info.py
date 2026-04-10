"""Скрипт выгрузки полной информации о текущем Telegram-пользователе."""

import asyncio
import logging
from pathlib import Path
from pprint import pformat
from typing import Any

from core.config import load_settings_or_exit
from core.logging import setup_logging
from userbot.client import UserBotClient


logger = logging.getLogger(__name__)
INFO_PATH = Path("tg_user_info/info.txt")


def _extract_user_attributes(user: Any) -> dict[str, Any]:
    """Собирает все доступные публичные атрибуты пользователя."""
    attributes: dict[str, Any] = {}

    for attribute_name in sorted(dir(user)):
        if attribute_name.startswith("_"):
            continue

        try:
            value = getattr(user, attribute_name)
        except Exception:
            continue

        if callable(value):
            continue

        attributes[attribute_name] = value

    return attributes


def build_user_info_report(user: Any) -> str:
    """Формирует текстовый отчёт по данным текущего Telegram-пользователя."""
    lines = ["Информация о текущем Telegram-пользователе", ""]

    basic_fields = {
        "id": getattr(user, "id", None),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "username": getattr(user, "username", None),
        "phone": getattr(user, "phone", None),
        "bot": getattr(user, "bot", None),
        "verified": getattr(user, "verified", None),
        "premium": getattr(user, "premium", None),
        "deleted": getattr(user, "deleted", None),
        "scam": getattr(user, "scam", None),
        "fake": getattr(user, "fake", None),
    }

    lines.append("Краткая сводка:")
    for key, value in basic_fields.items():
        lines.append(f"{key}: {value}")

    lines.append("")
    lines.append("Все доступные публичные атрибуты:")
    lines.append(pformat(_extract_user_attributes(user), sort_dicts=True, width=100))

    if hasattr(user, "to_dict") and callable(user.to_dict):
        lines.append("")
        lines.append("to_dict():")
        lines.append(pformat(user.to_dict(), sort_dicts=True, width=100))

    if hasattr(user, "stringify") and callable(user.stringify):
        lines.append("")
        lines.append("stringify():")
        lines.append(str(user.stringify()))

    return "\n".join(lines)


def save_user_info(report: str, path: Path | None = None) -> Path:
    """Сохраняет текстовый отчёт в файл."""
    if path is None:
        path = INFO_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    return path


async def main() -> int:
    """Запускает выгрузку данных текущего Telegram-пользователя."""
    settings = load_settings_or_exit()
    setup_logging(settings.log_level)

    userbot_client = UserBotClient(
        session_string=settings.session_string,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        proxy_url=settings.proxy_url,
    )

    await userbot_client.start()
    try:
        user = await userbot_client.get_current_user()
        report = build_user_info_report(user)
        output_path = save_user_info(report)
        logger.info("Полная информация о пользователе:\n%s", report)
        logger.info("Информация о пользователе сохранена в %s", output_path)
    finally:
        await userbot_client.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
