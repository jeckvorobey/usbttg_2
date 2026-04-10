"""Интерактивный скрипт обновления профиля Telegram-аккаунта."""

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.config import load_settings_or_exit
from core.logging import setup_logging
from userbot.client import UserBotClient


logger = logging.getLogger(__name__)
InputFunc = Callable[[str], str]
YES_VALUES = {"да", "д", "y", "yes"}
NO_VALUES = {"нет", "н", "no", "n"}


@dataclass(slots=True)
class ProfileChanges:
    """Набор изменений профиля, выбранных пользователем."""

    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    avatar_path: Path | None = None

    def has_changes(self) -> bool:
        """Возвращает True, если выбран хотя бы один тип обновления."""
        return any(
            value is not None
            for value in (self.first_name, self.last_name, self.username, self.avatar_path)
        )


@dataclass(slots=True)
class OperationResult:
    """Результат одной операции обновления профиля."""

    name: str
    success: bool
    details: str


def ask_yes_no(question: str, input_func: InputFunc = input) -> bool:
    """Запрашивает у пользователя ответ да/нет."""
    while True:
        answer = input_func(f"{question} (да/нет): ").strip().lower()
        if answer in YES_VALUES:
            return True
        if answer in NO_VALUES:
            return False
        logger.warning("Некорректный ответ. Введите 'да' или 'нет'.")


def ask_non_empty_value(prompt: str, input_func: InputFunc = input) -> str:
    """Запрашивает непустое текстовое значение."""
    while True:
        value = input_func(f"{prompt}: ").strip()
        if value:
            return value
        logger.warning("Значение не должно быть пустым. Повторите ввод.")


def ask_avatar_path(input_func: InputFunc = input) -> Path:
    """Запрашивает валидный путь к файлу аватарки."""
    while True:
        path = Path(input_func("Введите путь к файлу аватарки: ").strip()).expanduser()
        if path.exists() and path.is_file():
            return path
        logger.warning("Файл аватарки не найден или путь не указывает на файл. Повторите ввод.")


def normalize_username(username: str) -> str:
    """Нормализует username перед отправкой в Telegram."""
    return username.strip().lstrip("@")


def collect_profile_changes(input_func: InputFunc = input) -> ProfileChanges:
    """Пошагово собирает изменения профиля из интерактивного ввода."""
    changes = ProfileChanges()

    if ask_yes_no("Обновить имя?", input_func=input_func):
        changes.first_name = ask_non_empty_value("Введите имя", input_func=input_func)

    if ask_yes_no("Обновить фамилию?", input_func=input_func):
        changes.last_name = ask_non_empty_value("Введите фамилию", input_func=input_func)

    if ask_yes_no("Обновить username?", input_func=input_func):
        changes.username = normalize_username(
            ask_non_empty_value("Введите username", input_func=input_func)
        )

    if ask_yes_no("Обновить аватар?", input_func=input_func):
        changes.avatar_path = ask_avatar_path(input_func=input_func)

    return changes


async def apply_profile_changes(
    userbot_client: UserBotClient,
    changes: ProfileChanges,
) -> list[OperationResult]:
    """Применяет выбранные изменения профиля и возвращает результаты операций."""
    results: list[OperationResult] = []

    if changes.first_name is not None or changes.last_name is not None:
        try:
            await userbot_client.update_profile(
                first_name=changes.first_name,
                last_name=changes.last_name,
            )
        except Exception as exc:
            logger.exception("Не удалось обновить имя и/или фамилию")
            results.append(OperationResult("profile", False, str(exc)))
        else:
            results.append(OperationResult("profile", True, "Имя и фамилия обновлены"))

    if changes.username is not None:
        try:
            await userbot_client.update_username(changes.username)
        except Exception as exc:
            logger.exception("Не удалось обновить username")
            results.append(OperationResult("username", False, str(exc)))
        else:
            results.append(OperationResult("username", True, "Username обновлён"))

    if changes.avatar_path is not None:
        try:
            await userbot_client.update_avatar(changes.avatar_path)
        except Exception as exc:
            logger.exception("Не удалось обновить аватарку")
            results.append(OperationResult("avatar", False, str(exc)))
        else:
            results.append(OperationResult("avatar", True, "Аватарка обновлена"))

    return results


def log_operation_results(results: list[OperationResult]) -> None:
    """Пишет в лог краткую сводку по выбранным операциям."""
    for result in results:
        status = "успешно" if result.success else "ошибка"
        logger.info("%s: %s (%s)", result.name, status, result.details)


async def log_final_user_info(userbot_client: UserBotClient) -> None:
    """Выводит в лог итоговое состояние текущего аккаунта."""
    try:
        user = await userbot_client.get_current_user()
    except Exception:
        logger.exception("Не удалось получить итоговую информацию о пользователе")
        return

    logger.info(
        "Итоговый профиль пользователя: id=%s first_name=%s last_name=%s username=%s phone=%s has_photo=%s",
        getattr(user, "id", None),
        getattr(user, "first_name", None),
        getattr(user, "last_name", None),
        getattr(user, "username", None),
        getattr(user, "phone", None),
        getattr(user, "photo", None) is not None,
    )
async def main() -> int:
    """Точка входа интерактивного обновления профиля."""
    settings = load_settings_or_exit()
    setup_logging(settings.log_level)

    changes = collect_profile_changes()
    if not changes.has_changes():
        logger.info("Изменения не выбраны")
        return 0

    userbot_client = UserBotClient(
        session_string=settings.session_string,
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        proxy_url=settings.proxy_url,
    )

    await userbot_client.start()
    try:
        results = await apply_profile_changes(userbot_client, changes)
        log_operation_results(results)
        await log_final_user_info(userbot_client)
    finally:
        await userbot_client.stop()

    return 0 if all(result.success for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
