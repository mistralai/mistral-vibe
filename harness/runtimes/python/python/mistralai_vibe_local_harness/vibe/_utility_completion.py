"""One model call outside a turn, such as a session title or a Host's side call.

The call has no tools and no session state. It runs on the same provider
adapters as the session's own completions, so a Host needs no second set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from mistralai_vibe_local_harness.protocol import (
    RustCompletionFinishReason,
    RustCompletionResult,
    RustMessage,
    RustTextContentBlock,
    RustTokenUsage,
    RustToolDefinition,
)
from mistralai_vibe_local_harness.vibe._completion import credential_rejection
from mistralai_vibe_local_harness.vibe._credentials import (
    ProviderCredentialSnapshot,
    ProviderRejectionReason,
)
from mistralai_vibe_local_harness.vibe._runtime_config import (
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
    ProviderRetryObserver,
)
from mistralai_vibe_local_harness.vibe.adapters._provider_failure import (
    read_provider_failure,
)
from mistralai_vibe_local_harness.vibe.adapters.generic import (
    execute_generic_completion,
)
from mistralai_vibe_local_harness.vibe.adapters.mistral import (
    execute_mistral_completion,
)


class CompletionFn(Protocol):
    """One completion adapter invocation, carrying the call's attribution.

    The leading parameters are positional-only so any callable matching the
    adapters' shape satisfies the protocol, whatever it names its arguments.
    ``metadata`` is the Host's request metadata for the call, or ``None`` when
    the Host provides no source. The generic adapters accept and drop it: their
    provider APIs have no request-metadata channel.
    """

    async def __call__(
        self,
        messages: list[RustMessage],
        tools: list[RustToolDefinition],
        config: LocalRuntimeAdapterConfig,
        credential: ProviderCredentialSnapshot,
        route: LocalModelRoute,
        /,
        *,
        metadata: Mapping[str, str] | None = None,
        on_retry: ProviderRetryObserver | None = None,
    ) -> RustCompletionResult: ...


class UtilityCompletionError(RuntimeError):
    """A failed utility completion.

    ``status`` is the provider's HTTP status when it answered with an error.
    ``rejection`` says the provider refused the credential, so the caller can
    report it to the credential's owner. ``explanation`` is the provider's own
    error message, the one line that says what to fix.
    """

    status: int | None
    rejection: ProviderRejectionReason | None
    explanation: str | None

    def __init__(
        self,
        message: str,
        *,
        status: int | None,
        rejection: ProviderRejectionReason | None,
        explanation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.rejection = rejection
        self.explanation = explanation


@dataclass(frozen=True, slots=True)
class UtilityReply:
    """A utility completion's answer; ``text`` is empty when the model wrote none."""

    text: str
    usage: RustTokenUsage
    finish_reason: RustCompletionFinishReason


async def execute_utility_completion(
    messages: list[RustMessage],
    *,
    config: LocalRuntimeAdapterConfig,
    credential: ProviderCredentialSnapshot,
    route: LocalModelRoute,
    metadata: Mapping[str, str] | None = None,
    on_retry: ProviderRetryObserver | None = None,
    complete: CompletionFn | None = None,
) -> UtilityReply:
    """Run one completion on ``config``'s provider; raise ``UtilityCompletionError``.

    ``config.retry_max_elapsed_time_s`` bounds the retries and
    ``config.max_tokens`` caps the reply. ``metadata`` reaches only providers
    with a request-metadata field. ``complete`` replaces the provider adapter
    ``config.backend`` selects.
    """
    try:
        adapter = complete or _completion_for_backend(config.backend)
        result = await adapter(
            messages,
            [],
            config,
            credential,
            route,
            metadata=metadata,
            on_retry=on_retry,
        )
    except Exception as exc:
        failure = read_provider_failure(exc)
        raise UtilityCompletionError(
            failure.message,
            status=None if failure.response is None else failure.response.status,
            rejection=credential_rejection(failure),
            explanation=failure.explanation,
        ) from exc
    text = "".join(
        part.text for part in result.parts if isinstance(part, RustTextContentBlock)
    )
    return UtilityReply(
        # The adapters answer a reply with no text with a single space.
        text=text if text.strip() else "",
        usage=result.usage,
        finish_reason=result.finish_reason,
    )


def _completion_for_backend(backend: str) -> CompletionFn:
    match backend:
        case "mistral":
            return execute_mistral_completion
        case "generic":
            return execute_generic_completion
        case _:
            raise ValueError(f"Unsupported completion backend: {backend}")


__all__ = [
    "CompletionFn",
    "UtilityCompletionError",
    "UtilityReply",
    "execute_utility_completion",
]
