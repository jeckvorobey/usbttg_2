"""Композиция базовых промтов и persona-оверлеев для swarm-ботов."""

from __future__ import annotations

import logging
from pathlib import Path

from ai.gemini import PromptLoader


logger = logging.getLogger(__name__)


class PromptComposer:
    """Собирает итоговые промты из базового шаблона, persona и контекста обмена."""

    def __init__(self, prompt_loader: PromptLoader, bot_profiles_dir: str) -> None:
        self.prompt_loader = prompt_loader
        self.bot_profiles_dir = Path(bot_profiles_dir)

    async def compose(
        self,
        prompt_name: str,
        *,
        bot_id: str | None = None,
        persona_file: str | None = None,
        persona_text: str | None = None,
        exchange_context: str | None = None,
    ) -> str:
        """Возвращает итоговый промт для конкретного бота и действия."""
        base_prompt = await self.prompt_loader.load(prompt_name)
        resolved_persona = persona_text if persona_text is not None else self._load_persona(bot_id=bot_id, persona_file=persona_file)

        parts = [base_prompt.strip()]
        if resolved_persona and resolved_persona.strip():
            parts.append(resolved_persona.strip())
        if exchange_context and exchange_context.strip():
            parts.append(exchange_context.strip())
        composed_prompt = "\n\n".join(parts)
        logger.info(
            "Собран промт '%s': bot_id=%s persona_file=%s exchange_context=%s длина=%s",
            prompt_name,
            bot_id,
            persona_file,
            bool(exchange_context and exchange_context.strip()),
            len(composed_prompt),
        )
        return composed_prompt

    def _load_persona(self, *, bot_id: str | None, persona_file: str | None) -> str:
        """Загружает persona-файл конкретного бота строго по persona_file."""
        if persona_file is None:
            if bot_id is None:
                raise ValueError("bot_id or persona_file is required for persona loading")
            persona_path = self.bot_profiles_dir / f"{bot_id}.md"
        else:
            persona_path = self.bot_profiles_dir / persona_file
        logger.info("Загрузка persona-файла: bot_id=%s path=%s", bot_id, persona_path)
        if not persona_path.exists():
            logger.warning("Persona-файл не найден: %s", persona_path)
            return ""
        return persona_path.read_text(encoding="utf-8")
