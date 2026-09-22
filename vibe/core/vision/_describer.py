from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Protocol

from vibe.core.llm.exceptions import BackendError
from vibe.core.prompts import UtilityPrompt
from vibe.core.types import (
    FileImageSource,
    ImageAttachment,
    InlineImageSource,
    LLMMessage,
    Role,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from vibe.core.config import ModelConfig
    from vibe.core.types import LLMChunk, LLMUsage

logger = logging.getLogger(__name__)

MAX_INSTRUCTION_CHARS = 2_000
# One provider call per image, so a message carrying many of them is a fan-out
# the user never asked for. Describing a few at a time keeps the wait short
# without handing a single turn the whole connection pool.
MAX_CONCURRENT_DESCRIPTIONS = 4


class VisionCompleteFn(Protocol):
    async def __call__(
        self, *, model: ModelConfig, messages: Sequence[LLMMessage]
    ) -> LLMChunk: ...


def _digest(payload: str) -> str:
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def attachment_key(att: ImageAttachment) -> str:
    # Stat rather than bytes, the same trick the backend's base64 cache uses:
    # looking up a description must not read the image.
    match att.source:
        case FileImageSource(path=path):
            try:
                stat = path.stat()
            except OSError:
                return f"file:{path}"
            return f"file:{path}:{stat.st_mtime_ns}:{stat.st_size}"
        case InlineImageSource(data=data):
            return f"inline:{_digest(data)}"


@dataclass(frozen=True)
class FailedDescription:
    alias: str
    error: BaseException

    @property
    def reason(self) -> str:
        # BackendError renders as a multi-line diagnostic block; the provider's
        # own message is the one line that says what to fix.
        if isinstance(self.error, BackendError) and self.error.parsed_error:
            return self.error.parsed_error
        lines = str(self.error).strip().splitlines()
        return lines[0] if lines else repr(self.error)


@dataclass(frozen=True)
class DescribeReport:
    cached: int = 0
    described: int = 0
    failures: list[FailedDescription] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0

    @property
    def outcome(self) -> str:
        if not self.failures:
            return "success"
        return "partial" if self.described else "failure"


class ImageDescriber:
    def __init__(self, complete: VisionCompleteFn) -> None:
        self._complete = complete
        self._cache: dict[str, str] = {}

    def cached(self, att: ImageAttachment) -> str | None:
        return self._cache.get(attachment_key(att))

    async def describe_all(
        self,
        attachments: Iterable[ImageAttachment],
        *,
        model: ModelConfig,
        instruction: str = "",
    ) -> DescribeReport:
        # Already-described images are left alone: re-describing one under a
        # later turn's instruction would rewrite what the model was told, and
        # history would stop matching itself.
        pending: dict[str, ImageAttachment] = {}
        cached = 0
        for att in attachments:
            key = attachment_key(att)
            if key in self._cache:
                cached += 1
                continue
            pending.setdefault(key, att)
        if not pending:
            return DescribeReport(cached=cached)

        started = time.monotonic()
        steered = _normalize(instruction)
        limit = asyncio.Semaphore(MAX_CONCURRENT_DESCRIPTIONS)

        async def described(att: ImageAttachment) -> tuple[str, LLMUsage | None]:
            async with limit:
                return await self._describe(att, model=model, instruction=steered)

        results = await asyncio.gather(
            *(described(att) for att in pending.values()), return_exceptions=True
        )
        failures: list[FailedDescription] = []
        prompt_tokens = 0
        completion_tokens = 0
        for (key, att), result in zip(pending.items(), results, strict=True):
            if isinstance(result, BaseException):
                # A failed description must not fail the turn; the caller
                # degrades to a placeholder and decides how loudly to say so.
                logger.warning("Image description failed: %r", result)
                failures.append(FailedDescription(alias=att.alias, error=result))
                continue
            text, usage = result
            self._cache[key] = text
            if usage is not None:
                prompt_tokens += usage.prompt_tokens
                completion_tokens += usage.completion_tokens
        return DescribeReport(
            cached=cached,
            described=len(pending) - len(failures),
            failures=failures,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _describe(
        self, att: ImageAttachment, *, model: ModelConfig, instruction: str
    ) -> tuple[str, LLMUsage | None]:
        result = await self._complete(
            model=model, messages=_build_messages(att, instruction)
        )
        text = (result.message.content or "").strip()
        if not text:
            stop = result.stop.reason if result.stop else "unknown"
            raise ValueError(
                f"Vision model '{model.alias}' returned no description"
                f" (stop reason: {stop})"
            )
        return text, result.usage


def _normalize(instruction: str) -> str:
    return " ".join(instruction.split())[:MAX_INSTRUCTION_CHARS]


def _build_messages(att: ImageAttachment, instruction: str) -> list[LLMMessage]:
    request = f"Describe `{att.alias}`."
    if instruction:
        request += (
            "\n\nThe agent was asked the following. Bias the description toward"
            " whatever it needs to answer this, but still transcribe everything"
            f" legible:\n\n{instruction}"
        )
    return [
        LLMMessage(role=Role.system, content=UtilityPrompt.VISION_DESCRIBE.read()),
        LLMMessage(role=Role.user, content=request, images=[att]),
    ]
