from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.llm.completion import complete_once
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.types import Backend

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mistralai_vibe_local_harness.vibe import UtilityReply
    from vibe.config_values import ThinkingLevel
    from vibe.core.config import ModelConfig, ProviderConfig
    from vibe.core.llm.completion import CompletionTransport
    from vibe.core.types import LLMMessage

MAX_DESCRIPTION_TOKENS = 2_048

# A model whose levels are unknown gets no reasoning field for "off", which
# leaves the server default (reasoning on) in charge on Mistral; any other
# configured level lets the lowest request send off there.
_NO_REASONING: dict[Backend, ThinkingLevel] = {Backend.MISTRAL: "low"}


async def complete_vision(
    *,
    model: ModelConfig,
    provider: ProviderConfig,
    messages: Sequence[LLMMessage],
    transport: CompletionTransport,
    session_id: str | None,
) -> UtilityReply:
    # Reasoning off whatever the model config says: transcribing an image is
    # not a reasoning task, and the trace is charged against max_tokens -- a
    # dense screenshot exhausts the budget mid-trace and comes back with no
    # content at all.
    describing = model.model_copy(
        update={"thinking": _NO_REASONING.get(provider.backend, "off")}
    )
    return await complete_once(
        provider=provider,
        model=describing,
        messages=messages,
        max_tokens=MAX_DESCRIPTION_TOKENS,
        metadata=build_request_metadata(
            launch_context=None, session_id=session_id, call_type="secondary_call"
        ).model_dump(exclude_none=True),
        transport=transport,
        request_thinking="lowest",
    )
