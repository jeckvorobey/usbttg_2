"""Клиент Gemini AI и загрузчик промтов из .md файлов."""

import asyncio
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class PromptLoader:
    """Загружает промты из .md файлов в runtime. Промты никогда не хардкодятся в коде."""

    def __init__(self, prompts_dir: str) -> None:
        """
        Инициализирует загрузчик промтов.

        Args:
            prompts_dir: Путь к директории, содержащей .md файлы промтов.
        """
        self.prompts_dir = prompts_dir

    async def load(self, name: str) -> str:
        """
        Загружает содержимое файла {name}.md из директории промтов.

        Args:
            name: Имя промта — имя файла без расширения .md.

        Returns:
            Полное текстовое содержимое файла промта.

        Raises:
            FileNotFoundError: Если файл {name}.md не найден в директории промтов.
        """
        path = Path(self.prompts_dir) / f"{name}.md"
        logger.info("Загрузка промта '%s' из %s", name, path)
        if not path.exists():
            logger.error("Файл промта не найден: %s", path)
            raise FileNotFoundError(path)
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        logger.info("Промт '%s' успешно загружен", name)
        return content


class GeminiClient:
    """Клиент для генерации ответов через Google Gemini API."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash",
        proxy_url: str | None = None,
    ) -> None:
        """
        Инициализирует клиент Gemini.

        Args:
            api_key: API ключ для доступа к Gemini.
            model_name: Название модели Gemini для генерации.
            proxy_url: Общий proxy URL для Gemini API.
        """
        self.api_key = api_key
        self.model_name = model_name
        self.proxy_url = proxy_url
        self._client: Any | None = None
        self._types: Any | None = None

    async def generate_reply(
        self,
        system_prompt: str,
        history: list[dict[str, Any]],
        user_message: str,
    ) -> str:
        """
        Генерирует ответ на сообщение пользователя с учётом истории диалога.

        Args:
            system_prompt: Системный промт, задающий роль и поведение.
            history: История предыдущих сообщений (список словарей role/text).
            user_message: Текущее сообщение пользователя.

        Returns:
            Сгенерированный текстовый ответ.
        """
        logger.info(
            "Запуск генерации ответа через Gemini: история=%s, длина_сообщения=%s",
            len(history),
            len(user_message),
        )
        prompt_parts = [self._render_history(history), f"Пользователь: {user_message}"]
        prompt = "\n\n".join(part for part in prompt_parts if part)
        return await self._generate_text(system_prompt=system_prompt, prompt=prompt)

    async def start_topic(self, system_prompt: str, topic: str) -> str:
        """
        Генерирует начальное сообщение для инициирования разговора на заданную тему.

        Args:
            system_prompt: Системный промт, задающий роль и поведение.
            topic: Тема разговора из списка тем.

        Returns:
            Начальное сообщение для старта разговора.
        """
        logger.info("Запуск генерации стартового сообщения по теме: %s", topic)
        prompt = f"Тема разговора: {topic}"
        return await self._generate_text(system_prompt=system_prompt, prompt=prompt)

    async def _generate_text(self, system_prompt: str, prompt: str) -> str:
        """Выполняет один вызов модели и нормализует ответ."""
        client = self._get_client()
        types_module = self._get_types_module()
        config = types_module.GenerateContentConfig(system_instruction=system_prompt)
        logger.debug("Отправка запроса в Gemini: модель=%s, длина_prompt=%s", self.model_name, len(prompt))
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=self.model_name,
            contents=prompt,
            config=config,
        )
        text = getattr(response, "text", "")
        normalized_text = str(text).strip()
        logger.info("Ответ Gemini успешно получен, длина=%s", len(normalized_text))
        return normalized_text

    def _get_client(self) -> Any:
        """Ленивая инициализация клиента нового Gemini SDK."""
        if self._client is None:
            logger.info("Инициализация Gemini SDK клиента")
            genai = _import_google_genai()
            self._types = genai.types

            client_kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.proxy_url:
                http_options = genai.types.HttpOptions(
                    client_args={"proxy": self.proxy_url},
                    async_client_args={"proxy": self.proxy_url},
                )
                client_kwargs["http_options"] = http_options
                logger.info("Для Gemini настроен proxy")

            self._client = genai.Client(**client_kwargs)
            logger.info("Gemini SDK клиент создан")
        return self._client

    def _get_types_module(self) -> Any:
        """Возвращает модуль типов из Gemini SDK."""
        if self._types is None:
            self._get_client()
        return self._types

    @staticmethod
    def _render_history(history: list[dict[str, Any]]) -> str:
        """Преобразует историю диалога в текстовую форму для модели."""
        if not history:
            return ""

        rendered_messages = []
        for item in history:
            role = item.get("role", "user")
            text = item.get("text", "")
            rendered_messages.append(f"{role}: {text}")
        return "История диалога:\n" + "\n".join(rendered_messages)


def _import_google_genai() -> Any:
    """Импортирует новый Gemini SDK и поднимает понятную ошибку при отсутствии пакета."""
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Пакет google-genai не установлен") from exc

    logger.debug("Модуль google.genai успешно импортирован")
    return genai
