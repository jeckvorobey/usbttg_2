"""Тесты для модуля Gemini AI клиента и загрузчика промтов."""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai.gemini import GeminiClient, PromptLoader


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


def test_gemini_client_initializes_with_api_key():
    """Проверяет, что GeminiClient инициализируется и хранит имя модели."""
    client = GeminiClient(api_key="test_key_123", model_name="gemini-1.5-flash")
    assert client.model_name == "gemini-1.5-flash"


def test_gemini_client_default_model():
    """Проверяет дефолтное имя модели при инициализации."""
    client = GeminiClient(api_key="test_key_123")
    assert client.model_name is not None
    assert len(client.model_name) > 0


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
    assert captured["http_options_kwargs"] == {
        "client_args": {"proxy": "http://user:pass@127.0.0.1:8080"},
        "async_client_args": {"proxy": "http://user:pass@127.0.0.1:8080"},
    }
    assert captured["client_kwargs"]["api_key"] == "test_key_123"
    assert "http_options" in captured["client_kwargs"]


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
