from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustMessage,
    RustTextContentBlock,
    RustTokenUsage,
    RustToolDefinition,
)
from mistralai_vibe_local_harness.vibe import (
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
    ProviderCredentialSnapshot,
    _utility_completion,
)
from mistralai_vibe_local_harness.vibe._runtime_config import ProviderRetryObserver


@dataclass(frozen=True, slots=True)
class UtilityCompletionRequest:
    messages: list[RustMessage]
    config: LocalRuntimeAdapterConfig
    credential: ProviderCredentialSnapshot
    route: LocalModelRoute
    metadata: Mapping[str, str] | None


class FakeUtilityCompletion:
    """Stands in for the provider adapter behind every Harness utility completion.

    Answers with the given replies in order, then with empty ones, or raises
    ``error`` on every call. Patched where the Harness picks the adapter, so it
    catches every caller however it imports ``execute_utility_completion``.
    """

    def __init__(self, *replies: str, error: Exception | None = None) -> None:
        self._replies = list(replies)
        self._error = error
        self.requests: list[UtilityCompletionRequest] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Self:
        monkeypatch.setattr(
            _utility_completion, "_completion_for_backend", lambda _backend: self
        )
        return self

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
    ) -> RustCompletionResult:
        self.requests.append(
            UtilityCompletionRequest(messages, config, credential, route, metadata)
        )
        if self._error is not None:
            raise self._error
        text = self._replies.pop(0) if self._replies else ""
        return RustCompletionResult(
            # The adapters answer a reply with no text with a single space.
            parts=[RustTextContentBlock(text=text or " ")],
            finish_reason="stop",
            usage=RustTokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        )
