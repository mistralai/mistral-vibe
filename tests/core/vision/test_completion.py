from __future__ import annotations

import pytest

from mistralai_vibe_local_harness.vibe.completion_profile import WireFormat
from tests.stubs.fake_utility_completion import (
    FakeUtilityCompletion,
    UtilityCompletionRequest,
)
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.llm.completion import CompletionTransport
from vibe.core.types import Backend
from vibe.core.vision import complete_vision

_PROVIDER = ProviderConfig(
    name="mistral",
    api_base="https://api.mistral.ai/v1",
    api_key_env_var="MISTRAL_API_KEY",
    backend=Backend.MISTRAL,
    extra_headers={"x-tenant": "acme"},
)

_GENERIC_PROVIDER = ProviderConfig(
    name="gateway",
    api_base="https://gateway.internal/v1",
    api_key_env_var="GATEWAY_API_KEY",
    backend=Backend.GENERIC,
)

_MODEL = ModelConfig(
    name="mistral-vibe-cli-latest",
    provider="mistral",
    alias="vision",
    supports_images=True,
    temperature=1.0,
    thinking="high",
)


async def _describe(
    monkeypatch: pytest.MonkeyPatch,
    provider: ProviderConfig = _PROVIDER,
    model: ModelConfig = _MODEL,
) -> UtilityCompletionRequest:
    completion = FakeUtilityCompletion("a description").install(monkeypatch)
    await complete_vision(
        model=model,
        provider=provider,
        messages=[],
        transport=CompletionTransport(
            timeout_s=30.0,
            retry_budget_s=60.0,
            stream_idle_timeout_s=None,
            enable_system_trust_store=False,
        ),
        session_id="session-1",
    )
    [request] = completion.requests
    return request


@pytest.mark.asyncio
async def test_the_vision_model_keeps_its_own_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mistral rejects temperature 0 unless top_p is 1, which vibe never sends,
    # so a hardcoded 0.0 here 400s the whole description.
    request = await _describe(monkeypatch)

    assert request.route.temperature == 1.0


@pytest.mark.asyncio
async def test_describing_an_image_never_reasons_on_mistral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The trace is charged against max_tokens, and a thinking model spends the
    # whole budget restating the image -- the response comes back with no
    # content at all and the image goes undescribed. Off has to be sent: left
    # out, the server default (reasoning on) decides.
    request = await _describe(monkeypatch)

    assert request.route.reasoning_setting(WireFormat.MISTRAL) == "off"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["mistral-large-4", "mistral-large-4-0"])
async def test_describing_an_image_on_mistral_large_4_sends_its_lowest_effort(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    # Mistral Large 4 rejects reasoning_effort "none", so off would fail every
    # description with a 400.
    large = _MODEL.model_copy(update={"name": name})
    request = await _describe(monkeypatch, model=large)

    assert request.route.reasoning_setting(WireFormat.MISTRAL) == "low"


@pytest.mark.asyncio
async def test_describing_an_image_never_reasons_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Elsewhere a model of unknown levels gets no reasoning field at all.
    unknown = _MODEL.model_copy(update={"name": "gateway-vision"})
    request = await _describe(monkeypatch, _GENERIC_PROVIDER, unknown)

    assert request.route.reasoning_setting(WireFormat.OPENAI_CHAT) is None


@pytest.mark.asyncio
async def test_the_provider_headers_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    # A custom gateway may route, authenticate or redact on these; a
    # description must not be the one request on the provider that omits them.
    request = await _describe(monkeypatch)

    assert request.config.extra_headers["x-tenant"] == "acme"


@pytest.mark.asyncio
async def test_the_call_is_attributed_to_its_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = await _describe(monkeypatch)

    assert request.metadata is not None
    assert request.metadata["session_id"] == "session-1"
