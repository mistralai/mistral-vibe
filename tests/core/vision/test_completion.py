from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.mock.utils import mock_llm_chunk
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.types import Backend
from vibe.core.vision import _completion, complete_vision

if TYPE_CHECKING:
    from vibe.core.types import LLMChunk

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


class RecordingBackend:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def __aenter__(self) -> RecordingBackend:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def complete(self, **kwargs: Any) -> LLMChunk:
        self.kwargs = kwargs
        return mock_llm_chunk(content="a description")


_MODEL = ModelConfig(
    name="mistral-vibe-cli-latest",
    provider="mistral",
    alias="vision",
    supports_images=True,
    temperature=1.0,
    thinking="high",
)


async def _describe(
    monkeypatch: pytest.MonkeyPatch, provider: ProviderConfig = _PROVIDER
) -> RecordingBackend:
    backend = RecordingBackend()
    monkeypatch.setattr(_completion, "create_backend", lambda **_: backend)
    await complete_vision(
        model=_MODEL,
        provider=provider,
        messages=[],
        timeout=30.0,
        retry_max_elapsed_time=60.0,
        session_id="session-1",
        enable_otel=False,
    )
    return backend


@pytest.mark.asyncio
async def test_the_vision_model_keeps_its_own_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mistral rejects temperature 0 unless top_p is 1, which vibe never sends,
    # so a hardcoded 0.0 here 400s the whole description.
    backend = await _describe(monkeypatch)

    assert backend.kwargs["temperature"] == 1.0


@pytest.mark.asyncio
async def test_describing_an_image_never_reasons_on_mistral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The trace is charged against max_tokens, and a thinking model spends the
    # whole budget restating the image -- the response comes back with no
    # content at all and the image goes undescribed. On this backend "off"
    # omits reasoning_effort rather than setting it, which leaves the server
    # default in charge; "low" is what maps to reasoning_effort="none".
    backend = await _describe(monkeypatch)

    assert backend.kwargs["model"].thinking == "low"


@pytest.mark.asyncio
async def test_describing_an_image_never_reasons_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Everywhere else "low" is a real low reasoning effort and "off" is the
    # level that sends no reasoning_effort at all.
    backend = await _describe(monkeypatch, _GENERIC_PROVIDER)

    assert backend.kwargs["model"].thinking == "off"


@pytest.mark.asyncio
async def test_the_provider_headers_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    # A custom gateway may route, authenticate or redact on these; a
    # description must not be the one request on the provider that omits them.
    backend = await _describe(monkeypatch)

    assert backend.kwargs["extra_headers"]["x-tenant"] == "acme"
    assert backend.kwargs["extra_headers"]["user-agent"]


@pytest.mark.asyncio
async def test_the_call_is_attributed_to_its_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = await _describe(monkeypatch)

    assert backend.kwargs["metadata"]["session_id"] == "session-1"
