"""Клиент Gemini AI и загрузчик промтов из .md файлов."""

import asyncio
import logging
import random
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


logger = logging.getLogger(__name__)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


class GeminiGenerationError(RuntimeError):
    """Базовая ошибка генерации ответа через Gemini."""


class GeminiTemporaryError(GeminiGenerationError):
    """Временная ошибка внешнего Gemini API, для которой допустимы повторы."""


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
        fallback_model_name: str | None = None,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
        retry_jitter_seconds: float = 0.0,
        request_timeout_seconds: float = 45.0,
    ) -> None:
        """
        Инициализирует клиент Gemini.

        Args:
            api_key: API ключ для доступа к Gemini.
            model_name: Название модели Gemini для генерации.
            proxy_url: Общий proxy URL для Gemini API.
            fallback_model_name: Имя резервной модели для временных сбоев основной.
            max_retries: Максимальное число попыток для временных ошибок Gemini API.
            retry_backoff_seconds: Базовая задержка между повторными попытками.
            retry_jitter_seconds: Максимальная случайная добавка к задержке между попытками.
            request_timeout_seconds: Максимальное время ожидания одного запроса к Gemini.
        """
        self.api_key = api_key
        self.model_name = model_name
        self.proxy_url = proxy_url
        self.fallback_model_name = fallback_model_name
        self.max_retries = max(1, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.retry_jitter_seconds = max(0.0, retry_jitter_seconds)
        self.request_timeout_seconds = max(0.1, request_timeout_seconds)
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
        last_error: Exception | None = None

        model_names = self._get_model_names()

        for model_index, current_model_name in enumerate(model_names):
            for attempt in range(1, self.max_retries + 1):
                try:
                    logger.debug(
                        "Отправка запроса в Gemini: модель=%s, длина_prompt=%s, попытка=%s/%s, timeout=%.1f сек, proxy=%s",
                        current_model_name,
                        len(prompt),
                        attempt,
                        self.max_retries,
                        self.request_timeout_seconds,
                        self._describe_proxy(),
                    )
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            client.models.generate_content,
                            model=current_model_name,
                            contents=prompt,
                            config=config,
                        ),
                        timeout=self.request_timeout_seconds,
                    )
                    text = getattr(response, "text", "")
                    normalized_text = str(text).strip()
                    logger.info(
                        "Ответ Gemini успешно получен: модель=%s, длина=%s",
                        current_model_name,
                        len(normalized_text),
                    )
                    return normalized_text
                except Exception as exc:
                    if isinstance(exc, TimeoutError):
                        last_error = GeminiTemporaryError(
                            f"Gemini API не ответил за {self.request_timeout_seconds:.1f} сек"
                        )
                    else:
                        last_error = exc
                    if self._is_temporary_error(exc):
                        is_last_attempt_for_model = attempt >= self.max_retries
                        has_fallback = model_index < len(model_names) - 1

                        if not is_last_attempt_for_model:
                            delay = self._calculate_retry_delay(attempt)
                            logger.warning(
                                "Gemini временно недоступен: модель=%s, status=%s, попытка=%s/%s, повтор через %.2f сек, timeout=%.1f сек, proxy=%s",
                                current_model_name,
                                self._extract_status_code(exc),
                                attempt,
                                self.max_retries,
                                delay,
                                self.request_timeout_seconds,
                                self._describe_proxy(),
                            )
                            await asyncio.sleep(delay)
                            continue

                        if has_fallback:
                            fallback_model_name = model_names[model_index + 1]
                            logger.warning(
                                "Gemini временно недоступен после %s попыток: модель=%s, status=%s. Переключение на резервную модель=%s",
                                self.max_retries,
                                current_model_name,
                                self._extract_status_code(exc),
                                fallback_model_name,
                            )
                            break

                        logger.warning(
                            "Gemini временно недоступен после %s попыток: модель=%s, status=%s, timeout=%.1f сек, proxy=%s",
                            self.max_retries,
                            current_model_name,
                            self._extract_status_code(exc),
                            self.request_timeout_seconds,
                            self._describe_proxy(),
                        )
                        raise GeminiTemporaryError(str(last_error)) from exc

                    raise GeminiGenerationError("Ошибка генерации ответа через Gemini") from exc

        raise GeminiGenerationError("Ошибка генерации ответа через Gemini") from last_error

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
                logger.info("Для Gemini настроен proxy: %s", self._describe_proxy())

            self._client = genai.Client(**client_kwargs)
            logger.info("Gemini SDK клиент создан")
        return self._client

    def _get_model_names(self) -> list[str]:
        """Возвращает основной и резервный список моделей без дубликатов."""
        model_names = [self.model_name]
        if self.fallback_model_name and self.fallback_model_name != self.model_name:
            model_names.append(self.fallback_model_name)
        return model_names

    def _calculate_retry_delay(self, attempt: int) -> float:
        """Вычисляет экспоненциальную задержку повтора с jitter."""
        base_delay = self.retry_backoff_seconds * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, self.retry_jitter_seconds) if self.retry_jitter_seconds else 0.0
        return base_delay + jitter

    def _describe_proxy(self) -> str:
        """Возвращает безопасное текстовое описание proxy без учётных данных."""
        if not self.proxy_url:
            return "off"

        parsed = urlparse(self.proxy_url)
        if parsed.scheme and parsed.hostname and parsed.port:
            return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        return "configured"

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

    @staticmethod
    def _extract_status_code(exc: Exception) -> int | None:
        """Извлекает HTTP status code из исключения SDK, если он доступен."""
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code
        response = getattr(exc, "response", None)
        response_status = getattr(response, "status_code", None)
        if isinstance(response_status, int):
            return response_status
        return None

    @classmethod
    def _is_temporary_error(cls, exc: Exception) -> bool:
        """Определяет, является ли ошибка временной и подходит ли для повторной попытки."""
        status_code = cls._extract_status_code(exc)
        if status_code in TRANSIENT_STATUS_CODES:
            return True
        if isinstance(exc, TimeoutError):
            return True

        message = str(exc).upper()
        return any(
            marker in message
            for marker in ("429", "500", "502", "503", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED")
        )


def _import_google_genai() -> Any:
    """Импортирует новый Gemini SDK и поднимает понятную ошибку при отсутствии пакета."""
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Пакет google-genai не установлен") from exc

    logger.debug("Модуль google.genai успешно импортирован")
    return genai
