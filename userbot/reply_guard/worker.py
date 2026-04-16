"""Последовательный воркер reply_guard."""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
from typing import Any

from ai.gemini import GeminiGenerationError, GeminiTemporaryError, PromptLoader
from userbot.reply_guard.classifier import ReplyGuardClassifier, build_guard_input
from userbot.reply_guard.queue import ReplyGuardJob, ReplyGuardQueue
from userbot.reply_guard.safety import post_filter, regex_prefilter, sanitize


logger = logging.getLogger(__name__)


class ReplyGuardWorker:
    """Обрабатывает очередь reply_guard строго последовательно."""

    def __init__(
        self,
        queue: ReplyGuardQueue,
        client: object,
        classifier: ReplyGuardClassifier,
        prompt_loader: PromptLoader,
        gemini_client: object,
        refusal_text: str,
        poll_interval_seconds: float = 0.5,
        max_input_chars: int = 500,
        max_attempts: int = 3,
        retry_backoff_seconds: list[float] | None = None,
        system_prompt_name: str = "reply_guard/system",
        system_prompt_path: str | None = None,
    ) -> None:
        self.queue = queue
        self.client = client
        self.classifier = classifier
        self.prompt_loader = prompt_loader
        self.gemini_client = gemini_client
        self.refusal_text = refusal_text
        self.poll_interval_seconds = poll_interval_seconds
        self.max_input_chars = max_input_chars
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds or [2.0, 8.0, 30.0]
        self.system_prompt_name = system_prompt_name
        self.system_prompt_path = system_prompt_path
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Просит воркер завершить цикл."""
        self._stop_event.set()

    async def run(self) -> None:
        """Запускает цикл обработки очереди."""
        while not self._stop_event.is_set():
            job = await self.queue.claim_next()
            if job is None:
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            logger.info("reply_guard worker: claimed job_id=%s attempt=%s", job.id, job.attempts)
            await self.process_job(job)

    async def process_job(self, job: ReplyGuardJob) -> None:
        """Обрабатывает одну задачу очереди."""
        try:
            text = sanitize(job.text, max_chars=self.max_input_chars)
            regex_match = regex_prefilter(text)
            if regex_match is not None:
                logger.warning(
                    "reply_guard worker: regex refusal job_id=%s pattern_tag=%s",
                    job.id,
                    regex_match.tag,
                )
                await self._send_refusal(job)
                await self.queue.complete(job.id, "refused_injection_regex", verdict="injection")
                return

            reply_context = sanitize(job.reply_context or "", max_chars=self.max_input_chars)
            verdict = await self.classifier.classify(text, reply_context=reply_context or None)
            if verdict != "on_topic":
                logger.warning("reply_guard worker: refusal reason=%s job_id=%s", verdict, job.id)
                await self._send_refusal(job)
                await self.queue.complete(job.id, f"refused_{verdict}", verdict=verdict)
                return

            system_prompt = await self._load_system_prompt()
            answer = await self.gemini_client.generate_reply(
                system_prompt=system_prompt,
                history=[],
                user_message=build_guard_input(text, reply_context or None),
            )
            post_match = post_filter(answer)
            if post_match is not None:
                logger.warning(
                    "reply_guard worker: post_filter_triggered marker=%s job_id=%s",
                    post_match.tag,
                    job.id,
                )
                await self._send_refusal(job)
                await self.queue.complete(job.id, "refused_postfilter", verdict=verdict)
                return

            await self._send_message(job, answer)
            await self.queue.complete(job.id, "answered", verdict=verdict)
            logger.info("reply_guard worker: reply_sent chat_id=%s reply_to=%s job_id=%s", job.chat_id, job.user_msg_id, job.id)
        except (GeminiTemporaryError, TimeoutError) as exc:
            retry = job.attempts < self.max_attempts
            backoff_seconds = self._retry_backoff(job.attempts) if retry else 0.0
            logger.warning(
                "reply_guard worker: transient_error job_id=%s retry=%s next_retry_s=%s",
                job.id,
                retry,
                backoff_seconds,
            )
            await self.queue.fail(job.id, str(exc), retry=retry, backoff_seconds=backoff_seconds)
        except (GeminiGenerationError, Exception) as exc:
            logger.exception("reply_guard worker: terminal_error job_id=%s", job.id)
            await self.queue.fail(job.id, str(exc), retry=False)

    async def _send_refusal(self, job: ReplyGuardJob) -> None:
        """Отправляет фиксированный отказ reply-ом на сообщение пользователя."""
        await self._send_message(job, self.refusal_text)

    async def _send_message(self, job: ReplyGuardJob, text: str) -> None:
        """Отправляет Telegram-сообщение через Telethon-like клиент."""
        send_message = getattr(self.client, "send_message")
        result = send_message(job.chat_id, text, reply_to=job.user_msg_id)
        if inspect.isawaitable(result):
            await result

    async def _load_system_prompt(self) -> str:
        """Загружает системный prompt reply_guard."""
        if self.system_prompt_path:
            return Path(self.system_prompt_path).read_text(encoding="utf-8")
        return await self.prompt_loader.load(self.system_prompt_name)

    def _retry_backoff(self, attempts: int) -> float:
        """Возвращает задержку перед следующей попыткой."""
        index = max(0, min(attempts - 1, len(self.retry_backoff_seconds) - 1))
        return float(self.retry_backoff_seconds[index])
