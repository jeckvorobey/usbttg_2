"""Защитные фильтры для изолированного reply_guard."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyMatch:
    """Результат срабатывания защитного фильтра."""

    tag: str
    pattern: str


_CONTROL_TRANSLATION = {
    codepoint: " "
    for codepoint in range(32)
    if codepoint not in (9, 10, 13)
}
_INVISIBLE_PATTERN = re.compile(r"[\u200b-\u200f\ufeff\u202a-\u202e]")

_REGEX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (tag, re.compile(pattern, re.IGNORECASE | re.UNICODE))
    for tag, pattern in [
        ("ignore_previous", r"ignore (all |the )?(previous|above) (instructions|rules|prompt)"),
        ("disregard_prompt", r"disregard .* (instructions|rules|prompt)"),
        ("forget_rules", r"forget (everything|all|your) (rules|instructions)"),
        ("role_override", r"you are now (a|an) .*"),
        ("act_as", r"act as .*"),
        ("pretend_role", r"pretend (to be|you are) .*"),
        ("ru_role_override", r"ты теперь .*"),
        ("ru_pretend", r"представь что ты .*"),
        ("ru_act_as", r"веди себя как .*"),
        ("ru_ignore_previous", r"игнорируй (все )?(предыдущие|прошлые) (инструкции|правила)"),
        ("chat_marker", r"(system|assistant)\s*:\s*"),
        ("system_marker", r"### ?system"),
        ("special_token", r"<\|im_start\|>|<\|endoftext\|>"),
        ("reveal_prompt", r"reveal (your )?(system )?prompt"),
        ("ru_reveal_prompt", r"покажи (свой )?(системный )?промт"),
        ("repeat_prompt", r"repeat (everything|the prompt) (above|before)"),
        ("code_request", r"write (me |a )?(python|js|javascript|bash|sql) (code|script|function)"),
        ("code_request", r"напиши (код|скрипт|функцию)"),
        ("jailbreak", r"jailbreak|DAN mode|developer mode"),
        ("base64_blob", r"[A-Za-z0-9+/=]{80,}"),
    ]
)

_POST_FILTER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (tag, re.compile(pattern, re.IGNORECASE | re.UNICODE))
    for tag, pattern in [
        ("role_leak", r"как (ИИ|AI|ассистент|языковая модель)"),
        ("role_leak", r"я (большая )?языковая модель"),
        ("system_prompt", r"system prompt"),
        ("role_boundary", r"я не могу выйти из"),
        ("prompt_leak", r"вот мой промт"),
    ]
)


def sanitize(text: str, max_chars: int = 500) -> str:
    """Нормализует пользовательский текст перед проверками."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _INVISIBLE_PATTERN.sub("", normalized)
    normalized = normalized.translate(_CONTROL_TRANSLATION)
    normalized = re.sub(r"[\t\r\n]+", " ", normalized)
    normalized = re.sub(r" {2,}", " ", normalized).strip()
    return normalized[:max_chars]


def regex_prefilter(text: str) -> SafetyMatch | None:
    """Ищет явные признаки prompt-injection и off-topic payload."""
    for tag, pattern in _REGEX_PATTERNS:
        if pattern.search(text):
            return SafetyMatch(tag=tag, pattern=pattern.pattern)
    return None


def post_filter(answer: str) -> SafetyMatch | None:
    """Проверяет ответ модели на утечки роли и системных инструкций."""
    for tag, pattern in _POST_FILTER_PATTERNS:
        if pattern.search(answer):
            return SafetyMatch(tag=tag, pattern=pattern.pattern)
    return None
