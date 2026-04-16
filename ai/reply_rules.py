"""Загрузка и применение дополняемых правил ответа по триггерам."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplyRule:
    """Одно правило подсказки для ответа модели."""

    name: str
    triggers: tuple[str, ...]
    instruction: str
    notes: str = ""
    one_time_markers: tuple[str, ...] = ()

    def matches(self, text: str) -> bool:
        """Проверяет, срабатывает ли правило для нормализованного текста."""
        normalized_text = text.casefold()
        return any(trigger in normalized_text for trigger in self.triggers)


class ReplyRulesLoader:
    """Читает markdown-файл с триггерными правилами ответа и ищет совпадения."""

    def __init__(self, rules_path: str) -> None:
        """Сохраняет путь к markdown-файлу с правилами."""
        self.rules_path = rules_path
        self.rules: list[ReplyRule] = []

    async def load(self) -> None:
        """Загружает и парсит правила из markdown-файла."""
        path = Path(self.rules_path)
        logger.info("Загрузка правил ответа из %s", path)
        if not path.exists():
            logger.error("Файл правил ответа не найден: %s", path)
            raise FileNotFoundError(path)

        content = path.read_text(encoding="utf-8")
        self.rules = self._parse_rules(content)
        logger.info("Правил ответа загружено: %s", len(self.rules))

    def find_matches(self, message_text: str) -> list[ReplyRule]:
        """Возвращает все правила, подходящие к сообщению."""
        normalized_text = message_text.casefold().strip()
        if not normalized_text:
            return []
        return [rule for rule in self.rules if rule.matches(normalized_text)]

    @staticmethod
    def _parse_rules(content: str) -> list[ReplyRule]:
        """Парсит markdown-документ с блоками правил."""
        lines = content.splitlines()
        rules: list[ReplyRule] = []
        current_name: str | None = None
        current_fields: dict[str, str] = {}
        current_field_name: str | None = None

        def flush_rule() -> None:
            nonlocal current_name, current_fields, current_field_name
            if current_name is None:
                return

            triggers_raw = current_fields.get("triggers", "")
            instruction = current_fields.get("instruction", "").strip()
            notes = current_fields.get("notes", "").strip()
            triggers = tuple(
                part.strip().casefold()
                for part in triggers_raw.split(",")
                if part.strip()
            )
            one_time_markers = tuple(
                part.strip().casefold()
                for part in current_fields.get("one_time_markers", "").split(",")
                if part.strip()
            )

            if triggers and instruction:
                rules.append(
                    ReplyRule(
                        name=current_name,
                        triggers=triggers,
                        instruction=instruction,
                        notes=notes,
                        one_time_markers=one_time_markers,
                    )
                )

            current_name = None
            current_fields = {}
            current_field_name = None

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("# ") or stripped.startswith("---"):
                continue

            if stripped.startswith("## "):
                flush_rule()
                current_name = stripped[3:].strip()
                continue

            if current_name is None:
                continue

            if ":" in stripped:
                field_name, value = stripped.split(":", maxsplit=1)
                normalized_field = field_name.strip().lower().removeprefix("-").strip()
                if normalized_field in {"triggers", "instruction", "notes", "one_time_markers"}:
                    current_field_name = normalized_field
                    current_fields[current_field_name] = value.strip()
                    continue

            if current_field_name is not None:
                current_fields[current_field_name] = (
                    f"{current_fields[current_field_name]}\n{stripped}".strip()
                )

        flush_rule()
        return rules
