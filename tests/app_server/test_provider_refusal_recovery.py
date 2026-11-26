"""A provider's 403 refuses the request, not the credential: the next model
call still reaches the provider, through the Harness and the app server's
credential service together.
"""

from __future__ import annotations

from typing import Any

import httpx2
from mistralai.client.errors import SDKError
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.types import LLMMessage, Role

pytest.importorskip("mistralai_vibe_local_harness.vibe")

# Below the skip: both reach the Harness at module scope, and the Harness is
# an optional extra.
from mistralai_vibe_local_harness.protocol import (
    RustCompletionFailedEvent,
    RustCompletionResult,
    RustCompletionSucceededEvent,
    RustLLMCallAction,
    RustModelInputUpdate,
    RustModelMessageReplace,
    RustModelToolCatalogKeep,
    RustTextContentBlock,
    RustTokenUsage,
    RustUserMessage,
)
from mistralai_vibe_local_harness.vibe import (
    LocalModelRoute,
    LocalRuntimeAdapterConfig,
    UtilityCompletionError,
    _completion,
)
from tests.stubs.fake_utility_completion import FakeUtilityCompletion
from vibe.app_server._provider_credentials import ProviderCredentialService
from vibe.core.llm.completion import CompletionTransport, complete_once

_TIER_REFUSAL = "This model is not available in your subscription tier"
_PROVIDER = ProviderConfig(
    name="primary",
    api_base="https://primary.example/v1",
    api_style="openai",
    api_key_env_var="PRIMARY_KEY",
)


def _service() -> ProviderCredentialService:
    config: VibeConfigSchema = build_test_vibe_config(
        providers=[_PROVIDER],
        models=[ModelConfig(name="primary-model", provider="primary", alias="primary")],
        active_model="primary",
    )
    return ProviderCredentialService(FakeConfigOrchestrator(config))


def _action(turn: int) -> RustLLMCallAction:
    return RustLLMCallAction(
        action_id=f"action-{turn}",
        turn_id=f"turn-{turn}",
        iteration=0,
        max_iterations=1,
        model_input=RustModelInputUpdate(
            messages=RustModelMessageReplace(
                revision=1,
                messages=[
                    RustUserMessage(content=[RustTextContentBlock(text="coucou")])
                ],
            ),
            tool_catalog=RustModelToolCatalogKeep(revision=0),
        ),
    )


@pytest.fixture(autouse=True)
def _provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIMARY_KEY", "free-tier-secret")


@pytest.mark.asyncio
async def test_a_turn_after_a_tier_refusal_reaches_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A free-tier key: the provider refuses the large model with a
      403 and serves the medium one.
    *Do*: Run a turn on the large model, then on the medium one, then on the
      large one again.
    *Assert*: Every turn reaches the provider. The refused turns carry the
      provider's own reason, never a rejected-credential message.
    """
    # Prepare
    sent: list[str] = []

    async def provider(
        _messages: Any,
        _tools: Any,
        _config: Any,
        _credential: Any,
        route: LocalModelRoute,
        **_kwargs: Any,
    ) -> RustCompletionResult:
        sent.append(route.model)
        if route.model == "large-model":
            raise SDKError(
                "provider returned 403",
                httpx2.Response(403),
                f'{{"message": "{_TIER_REFUSAL}", "type": "tier_not_allowed"}}',
            )
        return RustCompletionResult(
            parts=[RustTextContentBlock(text="Coucou !")],
            finish_reason="stop",
            usage=RustTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )

    monkeypatch.setattr(_completion, "execute_mistral_completion", provider)
    service = _service()

    async def turn(model: str) -> Any:
        return await _completion.execute_completion(
            _action(len(sent)),
            [],
            [],
            LocalRuntimeAdapterConfig(
                credentials=service, active_model=LocalModelRoute(model=model)
            ),
        )

    # Do
    refused = await turn("large-model")
    served = await turn("medium-model")
    refused_again = await turn("large-model")

    # Assert
    assert sent == ["large-model", "medium-model", "large-model"]
    assert isinstance(served, RustCompletionSucceededEvent)
    for event in (refused, refused_again):
        assert isinstance(event, RustCompletionFailedEvent)
        assert event.error.message == (
            f"The model provider refused this request: {_TIER_REFUSAL}"
        )


@pytest.mark.asyncio
async def test_a_utility_call_after_a_refused_one_reaches_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A utility completion the provider refuses with a 403.
    *Do*: Run it, then run a second one with the same credentials.
    *Assert*: The second request reaches the provider.
    """
    # Prepare
    service = _service()
    model = ModelConfig(name="fast-model", provider="primary", alias="fast")

    async def complete() -> str:
        reply = await complete_once(
            provider=_PROVIDER,
            model=model,
            messages=[LLMMessage(role=Role.user, content="ping")],
            max_tokens=1,
            metadata={},
            transport=CompletionTransport(
                timeout_s=3.0,
                retry_budget_s=0,
                stream_idle_timeout_s=None,
                enable_system_trust_store=True,
            ),
            credentials=service,
        )
        return reply.text

    FakeUtilityCompletion(
        error=SDKError("provider returned 403", httpx2.Response(403), "refused")
    ).install(monkeypatch)
    with pytest.raises(UtilityCompletionError):
        await complete()

    # Do
    served = FakeUtilityCompletion("pong").install(monkeypatch)
    text = await complete()

    # Assert
    assert text == "pong"
    assert len(served.requests) == 1
