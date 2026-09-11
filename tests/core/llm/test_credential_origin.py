"""The origin a 401 names must describe the credential the call actually sent."""

from __future__ import annotations

import pytest

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.backend.mistral import MistralBackend
from vibe.utils.api_keys import ApiKeySource


def test_generic_backend_names_the_source_the_key_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIGIN_TEST_KEY", "some-key")
    provider = ProviderConfig(
        name="generic",
        api_base="https://example.com/v1",
        api_key_env_var="ORIGIN_TEST_KEY",
    )

    api_key, origin = GenericBackend(provider=provider)._resolve_credential("openai")

    assert api_key == "some-key"
    assert origin is not None
    assert origin.source is ApiKeySource.ENVIRONMENT
    assert origin.env_var == "ORIGIN_TEST_KEY"


def test_generic_backend_reports_no_origin_for_vertex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vertex mints an ADC token, so the configured key is never sent."""
    monkeypatch.setenv("ORIGIN_TEST_KEY", "some-key")
    provider = ProviderConfig(
        name="vertex",
        api_base="https://example.com/v1",
        api_key_env_var="ORIGIN_TEST_KEY",
        api_style="vertex-anthropic",
    )

    _, origin = GenericBackend(provider=provider)._resolve_credential(
        "vertex-anthropic"
    )

    assert origin is None


def test_mistral_backend_freezes_the_origin_with_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client is built once, so a later env change must not relabel it."""
    monkeypatch.setenv("ORIGIN_TEST_KEY", "some-key")
    provider = ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var="ORIGIN_TEST_KEY",
    )
    backend = MistralBackend(provider=provider)

    monkeypatch.delenv("ORIGIN_TEST_KEY")

    assert backend._api_key == "some-key"
    assert backend._api_key_origin is not None
    assert backend._api_key_origin.source is ApiKeySource.ENVIRONMENT
