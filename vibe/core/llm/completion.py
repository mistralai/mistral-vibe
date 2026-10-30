from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from vibe.core.llm.reasoning import profile_thinking, written_reasoning_levels
from vibe.core.types import FileImageSource, InlineImageSource, LLMMessage, Role
from vibe.utils.api_keys import resolve_api_key

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from mistralai_vibe_local_harness.protocol import RustContentBlock, RustMessage
    from mistralai_vibe_local_harness.vibe import (
        LocalRuntimeAdapterConfig,
        UtilityReply,
    )
    from mistralai_vibe_local_harness.vibe.completion_profile import (
        CompiledProfile,
        RequestReasoning,
    )
    from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
    from vibe.core.types import ImageAttachment

__all__ = ["CompletionTransport", "ImageReadError", "complete_once"]

_IMAGE_CACHE_MAX = 32


class ImageReadError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CompletionTransport:
    """How long a side call may take, and which certificate roots it trusts."""

    timeout_s: float
    retry_budget_s: float
    stream_idle_timeout_s: float | None
    enable_system_trust_store: bool

    @classmethod
    def from_config(cls, config: VibeConfigSchema) -> CompletionTransport:
        """The limits the session's own completions run with."""
        return cls(
            timeout_s=config.api_timeout,
            retry_budget_s=config.api_retry_max_elapsed_time,
            stream_idle_timeout_s=config.api_stream_idle_timeout or None,
            enable_system_trust_store=config.enable_system_trust_store,
        )


async def complete_once(
    *,
    provider: ProviderConfig,
    model: ModelConfig,
    messages: Sequence[LLMMessage],
    max_tokens: int | None,
    metadata: Mapping[str, str],
    transport: CompletionTransport,
    temperature: float | None = None,
    request_thinking: RequestReasoning | None = None,
) -> UtilityReply:
    """One completion outside a turn, on the Harness provider adapters.

    Raises ``UtilityCompletionError`` when the provider call fails.
    """
    # Imported here: the Harness pulls the provider SDKs, which stay off the
    # CLI startup path.
    from mistralai_vibe_local_harness.vibe import (
        LocalModelRoute,
        LocalRuntimeAdapterConfig,
        ProviderCredentialSnapshot,
        execute_utility_completion,
    )

    token = (
        resolve_api_key(provider.api_key_env_var) if provider.api_key_env_var else None
    )
    config = LocalRuntimeAdapterConfig(
        provider=provider.name,
        backend=str(provider.backend),
        api_style=provider.api_style,
        base_url=provider.api_base,
        max_tokens=max_tokens,
        reasoning_field_name=provider.reasoning_field_name,
        emits_finish_reason=provider.emits_finish_reason,
        extra_headers=provider.extra_headers,
        project_id=provider.project_id,
        region=provider.region,
        timeout_s=transport.timeout_s,
        stream_idle_timeout_s=transport.stream_idle_timeout_s,
        retry_max_elapsed_time_s=transport.retry_budget_s,
        enable_system_trust_store=transport.enable_system_trust_store,
    )
    compiled = _compile_reasoning(model, config)
    route = LocalModelRoute(
        model=model.name,
        # top_p belongs to the model's own temperature: Mistral rejects it
        # alongside greedy sampling.
        temperature=model.temperature if temperature is None else temperature,
        top_p=model.top_p if temperature is None else None,
        thinking=profile_thinking(compiled),
        reasoning=compiled.profile.format.reasoning,
        request_thinking=request_thinking,
        max_output_tokens=model.max_output_tokens,
    )
    return await execute_utility_completion(
        _harness_messages(messages),
        config=config,
        credential=ProviderCredentialSnapshot(
            token=token, headers={}, revision=provider.name
        ),
        route=route,
        metadata=metadata,
    )


def _compile_reasoning(
    model: ModelConfig, config: LocalRuntimeAdapterConfig
) -> CompiledProfile:
    """The model's reasoning, compiled as the session compiles a turn's."""
    from mistralai_vibe_local_harness.vibe import adapter_capabilities
    from mistralai_vibe_local_harness.vibe.completion_profile import (
        ExplicitFacts,
        compile_profile,
    )

    return compile_profile(
        model.name,
        adapter_capabilities(config),
        ExplicitFacts(
            reasoning=model.thinking if model.thinking_is_set else None,
            reasoning_levels=written_reasoning_levels(model),
        ),
    )


def _harness_messages(messages: Sequence[LLMMessage]) -> list[RustMessage]:
    from mistralai_vibe_local_harness.protocol import (
        RustAssistantMessage,
        RustImageContentBlock,
        RustSystemMessage,
        RustTextContentBlock,
        RustUserMessage,
    )

    converted: list[RustMessage] = []
    for message in messages:
        text = message.content or ""
        match message.role:
            case Role.system if text:
                converted.append(
                    RustSystemMessage(content=[RustTextContentBlock(text=text)])
                )
            case Role.user:
                blocks: list[RustContentBlock] = [
                    RustImageContentBlock(
                        data=_base64(image), mime_type=image.mime_type
                    )
                    for image in message.images or []
                ]
                if text:
                    blocks.insert(0, RustTextContentBlock(text=text))
                if blocks:
                    converted.append(RustUserMessage(content=blocks))
            case Role.assistant if text:
                converted.append(
                    RustAssistantMessage(content=[RustTextContentBlock(text=text)])
                )
            case _:
                pass
    return converted


def _base64(image: ImageAttachment) -> str:
    match image.source:
        case InlineImageSource(data=data):
            return data
        case FileImageSource(path=path):
            try:
                stat = path.stat()
            except OSError as e:
                raise ImageReadError(f"Failed to stat image {path}: {e}") from e
            return _encode_file(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=_IMAGE_CACHE_MAX)
def _encode_file(path: str, mtime_ns: int, size: int) -> str:
    try:
        return base64.b64encode(Path(path).read_bytes()).decode("ascii")
    except OSError as e:
        raise ImageReadError(f"Failed to read image {path}: {e}") from e
