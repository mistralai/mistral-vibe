from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.llm.backend.factory import create_backend
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.types import Backend
from vibe.utils.http import get_user_agent

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vibe.config_values import ThinkingLevel
    from vibe.core.config import ModelConfig, ProviderConfig
    from vibe.core.types import LLMChunk, LLMMessage

MAX_DESCRIPTION_TOKENS = 2_048

# "off" is the level that sends no reasoning effort at all -- except on the
# Mistral backend, where it omits the field rather than setting it, leaving a
# reasoning-capable describer to reason at the server default. "low" is the
# level that maps to ``reasoning_effort="none"`` there.
_NO_REASONING: dict[Backend, ThinkingLevel] = {Backend.MISTRAL: "low"}


async def complete_vision(
    *,
    model: ModelConfig,
    provider: ProviderConfig,
    messages: Sequence[LLMMessage],
    timeout: float,
    retry_max_elapsed_time: float,
    session_id: str | None,
    enable_otel: bool,
) -> LLMChunk:
    # Reasoning off whatever the model config says: transcribing an image is
    # not a reasoning task, and the trace is charged against max_tokens -- a
    # dense screenshot exhausts the budget mid-trace and comes back with no
    # content at all.
    describing = model.model_copy(
        update={"thinking": _NO_REASONING.get(Backend(provider.backend), "off")}
    )
    # A fresh backend per call rather than a session-lived one: the vision
    # model may sit on a provider the session never otherwise reaches.
    backend = create_backend(
        provider=provider,
        timeout=timeout,
        retry_max_elapsed_time=retry_max_elapsed_time,
        enable_otel=enable_otel,
    )
    async with backend:
        return await backend.complete(
            model=describing,
            messages=messages,
            # The model's own temperature, not a hardcoded 0.0: Mistral rejects
            # greedy sampling unless top_p is 1, which vibe never sends.
            temperature=describing.temperature,
            tools=None,
            tool_choice=None,
            max_tokens=MAX_DESCRIPTION_TOKENS,
            # The provider's own headers carry gateway routing, tenant and
            # privacy choices that every other completion on this provider
            # gets; a description must not be the one request that skips them.
            extra_headers={
                **provider.extra_headers,
                "user-agent": get_user_agent(provider.backend),
            },
            metadata=build_request_metadata(
                launch_context=None, session_id=session_id, call_type="secondary_call"
            ).model_dump(exclude_none=True),
        )
