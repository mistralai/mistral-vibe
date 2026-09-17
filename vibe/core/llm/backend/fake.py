"""Deterministic offline LLM backend, gated by the VIBE_FAKE_LLM env var.

Returns a fixed canned reply for every completion, ignoring the prompt. This
keeps CLI output byte-stable across runs and across the Python and Rust
frontends, which the client-e2e grid comparison relies on. It never affects
normal runs: `create_backend` only uses it when VIBE_FAKE_LLM is set.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
import os
import types
from typing import TYPE_CHECKING

from vibe.core.types import LLMChunk, LLMMessage, LLMUsage, Role, StopInfo

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig
    from vibe.core.types import AvailableTool, StrToolChoice

FAKE_LLM_ENV_VAR = "VIBE_FAKE_LLM"

_CANNED_REPLY = "Hi. What do you need?"
_USAGE = LLMUsage(prompt_tokens=10, completion_tokens=5, cached_tokens=0)

# Deterministic latency before the reply. Without it the reply is instant and
# the frontends' in-progress "working" widget never appears on screen, so
# client-e2e could not compare it. Fixed so runs stay reproducible.
_RESPONSE_DELAY = 0.8


def fake_backend_enabled() -> bool:
    return bool(os.environ.get(FAKE_LLM_ENV_VAR))


class FakeBackend:
    """Offline backend returning one fixed reply for any request."""

    def __init__(self, **_: object) -> None:
        self._chunk = LLMChunk(
            message=LLMMessage(role=Role.assistant, content=_CANNED_REPLY),
            usage=_USAGE,
            stop=StopInfo(reason="stop"),
        )

    async def __aenter__(self) -> FakeBackend:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        return None

    async def complete(
        self,
        *,
        model: ModelConfig,
        messages: Sequence[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        extra_headers: dict[str, str] | None,
        metadata: dict[str, str] | None = None,
    ) -> LLMChunk:
        await asyncio.sleep(_RESPONSE_DELAY)
        return self._chunk

    async def complete_streaming(
        self,
        *,
        model: ModelConfig,
        messages: Sequence[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        extra_headers: dict[str, str] | None,
        metadata: dict[str, str] | None = None,
    ) -> AsyncGenerator[LLMChunk, None]:
        await asyncio.sleep(_RESPONSE_DELAY)
        yield self._chunk
