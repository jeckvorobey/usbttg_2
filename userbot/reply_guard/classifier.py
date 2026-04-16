"""LLM-классификатор темы и prompt-injection для reply_guard."""

from __future__ import annotations

import logging
from html import escape
from pathlib import Path
from typing import Literal

from ai.gemini import GeminiClient, PromptLoader


logger = logging.getLogger(__name__)
ReplyGuardVerdict = Literal["on_topic", "off_topic", "injection"]
VALID_VERDICTS: set[str] = {"on_topic", "off_topic", "injection"}


class ReplyGuardClassifier:
    """Классифицирует вопрос в один из verdict-ов reply_guard."""

    def __init__(
        self,
        prompt_loader: PromptLoader,
        gemini_client: GeminiClient,
        prompt_name: str = "reply_guard/classifier",
        prompt_path: str | None = None,
    ) -> None:
        self.prompt_loader = prompt_loader
        self.gemini_client = gemini_client
        self.prompt_name = prompt_name
        self.prompt_path = prompt_path

    async def classify(self, text: str, reply_context: str | None = None) -> ReplyGuardVerdict:
        """Возвращает on_topic, off_topic или injection."""
        prompt = await self._load_prompt()
        raw_verdict = await self.gemini_client.generate_reply(
            system_prompt=prompt,
            history=[],
            user_message=build_guard_input(text, reply_context),
        )
        verdict = raw_verdict.strip().lower()
        if verdict in VALID_VERDICTS:
            logger.info("reply_guard classifier: verdict=%s", verdict)
            return verdict  # type: ignore[return-value]

        logger.warning("reply_guard classifier: invalid verdict treated as injection")
        return "injection"

    async def _load_prompt(self) -> str:
        """Загружает prompt классификатора из явного пути или PromptLoader."""
        if self.prompt_path:
            return Path(self.prompt_path).read_text(encoding="utf-8")
        return await self.prompt_loader.load(self.prompt_name)


def build_guard_input(text: str, reply_context: str | None = None) -> str:
    """Формирует изолированный XML-like input для reply_guard."""
    safe_text = escape(text, quote=False)
    if reply_context:
        safe_context = escape(reply_context, quote=False)
        return (
            f"<bot_message>{safe_context}</bot_message>\n"
            f"<user_question>{safe_text}</user_question>"
        )
    return f"<user_question>{safe_text}</user_question>"
