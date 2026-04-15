"""Тесты классификатора reply_guard."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai.gemini import GeminiTemporaryError
from userbot.reply_guard.classifier import ReplyGuardClassifier


async def test_classifier_returns_valid_verdict():
    """Проверяет нормализацию валидного ответа классификатора."""
    loader = SimpleNamespace(load=AsyncMock(return_value="Промт классификатора"))
    gemini = SimpleNamespace(generate_reply=AsyncMock(return_value=" on_topic\n"))
    classifier = ReplyGuardClassifier(loader, gemini)

    assert await classifier.classify("Где в Нячанге поесть фо?") == "on_topic"


async def test_classifier_treats_invalid_output_as_injection():
    """Проверяет безопасный fallback при мусорном ответе LLM."""
    loader = SimpleNamespace(load=AsyncMock(return_value="Промт классификатора"))
    gemini = SimpleNamespace(generate_reply=AsyncMock(return_value="maybe"))
    classifier = ReplyGuardClassifier(loader, gemini)

    assert await classifier.classify("Где рынок?") == "injection"


async def test_classifier_propagates_temporary_errors():
    """Проверяет, что временные ошибки Gemini отдаёт воркеру для retry."""
    loader = SimpleNamespace(load=AsyncMock(return_value="Промт классификатора"))
    gemini = SimpleNamespace(generate_reply=AsyncMock(side_effect=GeminiTemporaryError("503")))
    classifier = ReplyGuardClassifier(loader, gemini)

    with pytest.raises(GeminiTemporaryError):
        await classifier.classify("Где рынок?")
