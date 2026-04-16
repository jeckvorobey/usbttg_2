"""Тесты для markdown-правил ответа по триггерам."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ai.reply_rules import ReplyRulesLoader


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch):
    """Подменяет asyncio.to_thread на синхронную заглушку для быстрых unit-тестов."""

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("ai.reply_rules.asyncio.to_thread", fake_to_thread)


@pytest.mark.asyncio
async def test_reply_rules_loader_reads_markdown_rules():
    """Проверяет загрузку правил из markdown-файла."""
    content = """
# Правила

## Обмен валюты
triggers: обмен, доллары, usd
instruction: Можно мягко предложить @AntEx_support и отзывы.
notes: Важно не звучать рекламно.
""".strip()

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "reply_rules.md"
        path.write_text(content, encoding="utf-8")

        loader = ReplyRulesLoader(str(path))
        await loader.load()

        assert len(loader.rules) == 1
        assert loader.rules[0].name == "Обмен валюты"
        assert loader.rules[0].triggers == ("обмен", "доллары", "usd")
        assert "@AntEx_support" in loader.rules[0].instruction


@pytest.mark.asyncio
async def test_reply_rules_loader_returns_empty_list_for_empty_file():
    """Проверяет, что пустой файл не создаёт правил."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "reply_rules.md"
        path.write_text("", encoding="utf-8")

        loader = ReplyRulesLoader(str(path))
        await loader.load()

        assert loader.rules == []


@pytest.mark.asyncio
async def test_reply_rules_loader_matches_exchange_keywords():
    """Проверяет матчинг правил по словам из сообщения."""
    loader = ReplyRulesLoader("ai/prompts/reply_rules.md")
    await loader.load()

    matched = loader.find_matches("Где в Дананге лучше менять доллары и какой курс?")

    assert len(matched) == 1
    assert matched[0].name == "Обмен валюты"


@pytest.mark.asyncio
async def test_reply_rules_loader_parses_one_time_markers():
    """Проверяет, что one_time_markers парсятся из markdown-файла."""
    content = """
# Правила

## Обмен валюты
triggers: обмен, доллары
one_time_markers: @antex_support, t.me/+ui-tQ4T-jrNlNmQy
instruction: Можно мягко предложить @AntEx_support и отзывы.
notes: Только один раз.
""".strip()

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "reply_rules.md"
        path.write_text(content, encoding="utf-8")

        loader = ReplyRulesLoader(str(path))
        await loader.load()

        assert len(loader.rules) == 1
        rule = loader.rules[0]
        assert "@antex_support" in rule.one_time_markers
        assert "t.me/+ui-tq4t-jrnlnmqy" in rule.one_time_markers  # casefold применяется при парсинге


def test_settings_reads_reply_rules_path():
    """Проверяет загрузку пути к markdown-файлу правил из TOML."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        settings_path = Path(tmpdir) / "settings.toml"
        settings_path.write_text(
            """
            [paths]
            reply_rules_path = "custom/reply_rules.md"
            """,
            encoding="utf-8",
        )

        base_env = {
            "API_ID": "12345678",
            "API_HASH": "test_api_hash_abc",
            "GEMINI_API_KEY": "test_gemini_key_xyz",
            "SESSION_STRING": "test-session-string",
            "SETTINGS_PATH": str(settings_path),
        }
        with patch.dict("os.environ", base_env, clear=True):
            from core.config import Settings

            settings = Settings(_env_file=None)

            assert settings.reply_rules_path == "custom/reply_rules.md"
