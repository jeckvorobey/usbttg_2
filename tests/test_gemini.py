"""Тесты для модуля Gemini AI клиента и загрузчика промтов."""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai.gemini import GeminiClient, GeminiTemporaryError, PromptLoader


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch):
    """Подменяет asyncio.to_thread на синхронную заглушку для быстрых unit-тестов."""

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("ai.gemini.asyncio.to_thread", fake_to_thread)


async def test_prompt_loader_reads_md_file():
    """Проверяет, что загрузчик читает содержимое .md файла по имени."""
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = Path(tmpdir) / "system.md"
        prompt_file.write_text("Ты полезный ассистент.", encoding="utf-8")

        loader = PromptLoader(prompts_dir=tmpdir)
        content = await loader.load("system")

        assert "Ты полезный ассистент" in content


async def test_prompt_loader_raises_on_missing_file():
    """Проверяет, что FileNotFoundError бросается при отсутствии файла."""
    with tempfile.TemporaryDirectory() as tmpdir:
        loader = PromptLoader(prompts_dir=tmpdir)

        with pytest.raises(FileNotFoundError):
            await loader.load("несуществующий_промт")


async def test_prompt_loader_preserves_full_content():
    """Проверяет, что загрузчик возвращает полное содержимое файла."""
    content = "# Заголовок\n\nПервый абзац.\nВторой абзац."
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "test.md").write_text(content, encoding="utf-8")

        loader = PromptLoader(prompts_dir=tmpdir)
        result = await loader.load("test")

        assert result == content


@pytest.mark.asyncio
async def test_prompt_files_target_nha_trang_group():
    """Проверяет, что промты ориентированы на группу про Нячанг, а не на группу про Дананг."""
    loader = PromptLoader(prompts_dir="ai/prompts")

    system_prompt = await loader.load("system")
    reply_prompt = await loader.load("reply")
    start_topic_prompt = await loader.load("start_topic")

    for prompt in (system_prompt, reply_prompt, start_topic_prompt):
        assert "Нячанг" in prompt
        assert "группы «Захотели ✈ Полетели | Дананг»" not in prompt
        assert "сообщества про жизнь и путешествия в Дананге" not in prompt


@pytest.mark.asyncio
async def test_system_and_reply_prompts_allow_short_multi_sentence_replies():
    """Проверяет, что промты больше не требуют ответа ровно в одно предложение."""
    loader = PromptLoader(prompts_dir="ai/prompts")

    system_prompt = await loader.load("system")
    reply_prompt = await loader.load("reply")

    assert "1–3 коротких предложения" in system_prompt
    assert "Одно предложение" not in reply_prompt
    assert "не более 3 коротких предложений" in reply_prompt


@pytest.mark.asyncio
async def test_start_topic_prompt_avoids_editorial_post_format():
    """Проверяет, что старт темы оформлен как живая реплика участника, а не пост канала."""
    loader = PromptLoader(prompts_dir="ai/prompts")

    start_topic_prompt = await loader.load("start_topic")

    assert "обычный вброс участника" in start_topic_prompt
    assert "Без списков" in start_topic_prompt
    assert "Без «топ-5»" in start_topic_prompt


def test_gemini_client_initializes_with_api_key():
    """Проверяет, что GeminiClient инициализируется и хранит имя модели."""
    client = GeminiClient(api_key="test_key_123", model_name="gemini-1.5-flash")
    assert client.model_name == "gemini-1.5-flash"


def test_gemini_client_default_model():
    """Проверяет дефолтное имя модели при инициализации."""
    client = GeminiClient(api_key="test_key_123")
    assert client.model_name is not None
    assert len(client.model_name) > 0


def test_gemini_client_tracks_fallback_model():
    """Проверяет сохранение имени резервной модели Gemini."""
    client = GeminiClient(
        api_key="test_key_123",
        model_name="gemini-2.5-flash",
        fallback_model_name="gemini-2.5-flash-lite",
    )

    assert client.fallback_model_name == "gemini-2.5-flash-lite"


def test_gemini_client_builds_client_with_proxy(monkeypatch):
    """Проверяет передачу proxy-настроек в новый Gemini SDK."""
    captured: dict[str, object] = {}

    class FakeHttpOptions:
        def __init__(self, **kwargs) -> None:
            captured["http_options_kwargs"] = kwargs

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            captured["client_kwargs"] = kwargs

    fake_types = SimpleNamespace(HttpOptions=FakeHttpOptions)
    fake_genai = SimpleNamespace(Client=FakeClient, types=fake_types)

    monkeypatch.setattr("ai.gemini._import_google_genai", lambda: fake_genai)

    client = GeminiClient(
        api_key="test_key_123",
        model_name="gemini-2.5-flash",
        proxy_url="http://user:pass@127.0.0.1:8080",
    )

    sdk_client = client._get_client()

    assert isinstance(sdk_client, FakeClient)
    assert captured["client_kwargs"]["api_key"] == "test_key_123"
    assert "http_options" in captured["client_kwargs"]
    kwargs = captured["http_options_kwargs"]
    assert kwargs.get("client_args") == {"proxy": "http://user:pass@127.0.0.1:8080"}
    assert kwargs.get("async_client_args") == {"proxy": "http://user:pass@127.0.0.1:8080"}


@pytest.mark.asyncio
async def test_gemini_client_generate_reply_uses_system_instruction(monkeypatch):
    """Проверяет передачу system instruction и содержимого запроса в SDK."""
    captured: dict[str, object] = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured["generate_content_kwargs"] = kwargs
            return SimpleNamespace(text="Ответ модели")

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.models = FakeModels()

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    fake_types = SimpleNamespace(GenerateContentConfig=FakeGenerateContentConfig)
    fake_genai = SimpleNamespace(Client=FakeClient, types=fake_types)

    monkeypatch.setattr("ai.gemini._import_google_genai", lambda: fake_genai)

    client = GeminiClient(api_key="test_key_123", model_name="gemini-2.5-flash")

    result = await client.generate_reply(
        system_prompt="Системная роль",
        history=[{"role": "user", "text": "Привет"}],
        user_message="Как дела?",
    )

    assert result == "Ответ модели"
    assert captured["generate_content_kwargs"]["model"] == "gemini-2.5-flash"
    assert captured["generate_content_kwargs"]["contents"] == (
        "История диалога:\nuser: Привет\n\nПользователь: Как дела?"
    )
    assert (
        captured["generate_content_kwargs"]["config"].kwargs["system_instruction"]
        == "Системная роль"
    )


@pytest.mark.asyncio
async def test_gemini_client_retries_on_temporary_server_error(monkeypatch):
    """Проверяет, что временная ошибка Gemini приводит к повторной попытке."""
    attempts = {"count": 0}

    class FakeServerError(Exception):
        def __init__(self, status_code: int, message: str) -> None:
            super().__init__(message)
            self.status_code = status_code

    class FakeModels:
        def generate_content(self, **kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise FakeServerError(503, "503 UNAVAILABLE")
            return SimpleNamespace(text="Ответ после повтора")

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.models = FakeModels()

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    fake_types = SimpleNamespace(GenerateContentConfig=FakeGenerateContentConfig)
    fake_genai = SimpleNamespace(Client=FakeClient, types=fake_types)

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("ai.gemini._import_google_genai", lambda: fake_genai)
    monkeypatch.setattr("ai.gemini.asyncio.sleep", fake_sleep)

    client = GeminiClient(
        api_key="test_key_123",
        model_name="gemini-2.5-flash",
        max_retries=3,
        retry_backoff_seconds=0.5,
        retry_jitter_seconds=0.0,
    )

    result = await client.generate_reply(
        system_prompt="Системная роль",
        history=[],
        user_message="Привет",
    )

    assert result == "Ответ после повтора"
    assert attempts["count"] == 3
    assert delays == [0.5, 1.0]


@pytest.mark.asyncio
async def test_gemini_client_raises_temporary_error_after_retry_limit(monkeypatch):
    """Проверяет, что после исчерпания повторов поднимается специализированная ошибка."""

    class FakeServerError(Exception):
        def __init__(self, status_code: int, message: str) -> None:
            super().__init__(message)
            self.status_code = status_code

    class FakeModels:
        def generate_content(self, **kwargs):
            raise FakeServerError(503, "503 UNAVAILABLE")

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.models = FakeModels()

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    fake_types = SimpleNamespace(GenerateContentConfig=FakeGenerateContentConfig)
    fake_genai = SimpleNamespace(Client=FakeClient, types=fake_types)

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("ai.gemini._import_google_genai", lambda: fake_genai)
    monkeypatch.setattr("ai.gemini.asyncio.sleep", fake_sleep)

    client = GeminiClient(
        api_key="test_key_123",
        model_name="gemini-2.5-flash",
        max_retries=2,
        retry_backoff_seconds=0.5,
        retry_jitter_seconds=0.0,
    )

    with pytest.raises(GeminiTemporaryError):
        await client.generate_reply(
            system_prompt="Системная роль",
            history=[],
            user_message="Привет",
        )

    assert delays == [0.5]


@pytest.mark.asyncio
async def test_gemini_client_switches_to_fallback_model_after_retry_limit(monkeypatch):
    """Проверяет переключение на резервную модель после исчерпания повторов основной."""
    attempts: list[str] = []

    class FakeServerError(Exception):
        def __init__(self, status_code: int, message: str) -> None:
            super().__init__(message)
            self.status_code = status_code

    class FakeModels:
        def generate_content(self, **kwargs):
            model = kwargs["model"]
            attempts.append(model)
            if model == "gemini-2.5-flash":
                raise FakeServerError(503, "503 UNAVAILABLE")
            return SimpleNamespace(text="Ответ резервной модели")

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.models = FakeModels()

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    fake_types = SimpleNamespace(GenerateContentConfig=FakeGenerateContentConfig)
    fake_genai = SimpleNamespace(Client=FakeClient, types=fake_types)

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("ai.gemini._import_google_genai", lambda: fake_genai)
    monkeypatch.setattr("ai.gemini.asyncio.sleep", fake_sleep)

    client = GeminiClient(
        api_key="test_key_123",
        model_name="gemini-2.5-flash",
        fallback_model_name="gemini-2.5-flash-lite",
        max_retries=2,
        retry_backoff_seconds=0.5,
        retry_jitter_seconds=0.0,
    )

    result = await client.generate_reply(
        system_prompt="Системная роль",
        history=[],
        user_message="Привет",
    )

    assert result == "Ответ резервной модели"
    assert attempts == [
        "gemini-2.5-flash",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_gemini_client_adds_jitter_to_retry_delay(monkeypatch):
    """Проверяет добавление jitter к экспоненциальной задержке повтора."""

    class FakeServerError(Exception):
        def __init__(self, status_code: int, message: str) -> None:
            super().__init__(message)
            self.status_code = status_code

    class FakeModels:
        def generate_content(self, **kwargs):
            raise FakeServerError(503, "503 UNAVAILABLE")

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.models = FakeModels()

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    fake_types = SimpleNamespace(GenerateContentConfig=FakeGenerateContentConfig)
    fake_genai = SimpleNamespace(Client=FakeClient, types=fake_types)

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("ai.gemini._import_google_genai", lambda: fake_genai)
    monkeypatch.setattr("ai.gemini.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("ai.gemini.random.uniform", lambda start, end: 0.25)

    client = GeminiClient(
        api_key="test_key_123",
        model_name="gemini-2.5-flash",
        max_retries=2,
        retry_backoff_seconds=0.5,
        retry_jitter_seconds=0.5,
    )

    with pytest.raises(GeminiTemporaryError):
        await client.generate_reply(
            system_prompt="Системная роль",
            history=[],
            user_message="Привет",
        )

    assert delays == [0.75]
