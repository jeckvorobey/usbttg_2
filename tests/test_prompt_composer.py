"""Тесты композиции промтов для swarm-персон."""

import pytest

from ai.prompt_composer import PromptComposer


class StubPromptLoader:
    """Простая подмена загрузчика промтов."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    async def load(self, name: str) -> str:
        return self.mapping[name]


@pytest.mark.asyncio
async def test_prompt_composer_combines_base_prompt_and_persona():
    """Проверяет сборку system prompt из базового текста и persona overlay."""
    composer = PromptComposer(
        prompt_loader=StubPromptLoader({"system": "Базовый system"}),
        bot_profiles_dir="ai/prompts/bots",
    )

    system_prompt = await composer.compose("system", bot_id="anna", persona_file="anna.md", persona_text="Persona Анны")

    assert system_prompt == "Базовый system\n\nPersona Анны"


@pytest.mark.asyncio
async def test_prompt_composer_adds_exchange_context_when_provided():
    """Проверяет добавление exchange context в конец итогового промта."""
    composer = PromptComposer(
        prompt_loader=StubPromptLoader({"reply": "Базовый reply"}),
        bot_profiles_dir="ai/prompts/bots",
    )

    prompt = await composer.compose(
        "reply",
        bot_id="anna",
        persona_file="anna.md",
        persona_text="Persona Анны",
        exchange_context="Текущий exchange: вопрос пользователя",
    )

    assert prompt == "Базовый reply\n\nPersona Анны\n\nТекущий exchange: вопрос пользователя"


@pytest.mark.asyncio
async def test_prompt_composer_uses_explicit_persona_file(tmp_path):
    """Проверяет загрузку persona строго по persona_file."""
    prompts_dir = tmp_path / "bots"
    prompts_dir.mkdir()
    (prompts_dir / "special.md").write_text("Особая persona", encoding="utf-8")
    composer = PromptComposer(
        prompt_loader=StubPromptLoader({"system": "Базовый system"}),
        bot_profiles_dir=str(prompts_dir),
    )

    system_prompt = await composer.compose("system", bot_id="anna", persona_file="special.md")

    assert system_prompt == "Базовый system\n\nОсобая persona"
