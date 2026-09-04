"""``ProviderCredentialService``: the Vibe side of the Harness credential port.

The two tier-2 pins from the authentication design live at the bottom of this
file. Both read the legacy source of truth instead of copying it, so an edit to
``generic.py`` or ``BackendError._fmt`` fails here rather than silently leaving
the Unified path sending a header the legacy path stopped sending.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import PropertyMock, patch

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.llm.backend.generic import _get_adapter
from vibe.core.llm.backend.vertex import VertexCredentials
from vibe.core.llm.exceptions import BackendError, PayloadSummary
from vibe.core.types import LLMMessage, Role

if TYPE_CHECKING:
    from vibe.core.config.orchestrator import ConfigOrchestrator

pytest.importorskip("mistralai_vibe_local_harness.vibe")

# Both imports sit below the skip because both reach the Harness credential
# port at module scope -- the service implements it, so importing the service
# is importing it. `mistralai-vibe-local-harness` is an optional extra, and an
# environment without it must skip this module rather than fail to collect it.
from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
    INVALID_API_KEY_MESSAGE,
    ProviderAuthRequired,
    ProviderCredentialSnapshot,
)

from vibe.app_server._provider_credentials import ProviderCredentialService

# The header names that carry credential material. The port owns these and
# nothing else: ``Content-Type`` and ``anthropic-beta`` describe the request,
# not the caller, and stay with whichever adapter builds the request.
_CREDENTIAL_HEADERS = frozenset({"Authorization", "x-api-key", "anthropic-version"})


def _provider(name: str, *, api_style: str = "openai", **kwargs: Any) -> ProviderConfig:
    kwargs.setdefault("api_key_env_var", f"{name.upper()}_KEY")
    return ProviderConfig(
        name=name, api_base=f"https://{name}.example/v1", api_style=api_style, **kwargs
    )


def _vertex_provider() -> ProviderConfig:
    # No key environment variable: Vertex mints a Google ADC token and ignores
    # whatever is configured, exactly as ``VertexAnthropicAdapter`` does.
    return _provider(
        "vertex",
        api_style="vertex-anthropic",
        api_key_env_var="",
        project_id="a-project",
        region="us-central1",
    )


def _config(*providers: ProviderConfig, active: str | None = None) -> VibeConfigSchema:
    models = [
        ModelConfig(
            name=f"{provider.name}-model",
            provider=provider.name,
            alias=f"{provider.name}-model",
        )
        for provider in providers
    ]
    return build_test_vibe_config(
        providers=list(providers),
        models=models,
        active_model=f"{active or providers[0].name}-model",
    )


def _orchestrator(config: VibeConfigSchema) -> ConfigOrchestrator[VibeConfigSchema]:
    return FakeConfigOrchestrator(config)


@pytest.fixture(autouse=True)
def _provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRIMARY_KEY", "primary-secret")
    monkeypatch.setenv("SECONDARY_KEY", "secondary-secret")


@pytest.mark.asyncio
async def test_resolution_reads_the_orchestrator_on_every_call() -> None:
    """*Prepare*: Two configured providers, the first one active.
    *Do*: Resolve, switch the active model, resolve again.
    *Assert*: The second resolution carries the second provider's key.
    """
    # Prepare
    orchestrator = _orchestrator(
        _config(_provider("primary"), _provider("secondary"), active="primary")
    )
    service = ProviderCredentialService(orchestrator)

    # Do
    before = await service.resolve()
    await orchestrator.set_field("/active_model", "secondary-model")
    after = await service.resolve()

    # Assert
    assert isinstance(before, ProviderCredentialSnapshot)
    assert isinstance(after, ProviderCredentialSnapshot)
    assert before.token == "primary-secret"
    assert after.token == "secondary-secret"
    # A session opened against one provider must not keep speaking to it after
    # ``/config`` switches models mid-session.
    assert before.revision != after.revision


@pytest.mark.asyncio
async def test_a_missing_key_is_a_state_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A provider whose key environment variable is unset.
    *Do*: Resolve.
    *Assert*: An authorization-required state naming the provider and variable.
    """
    # Prepare
    service = ProviderCredentialService(_orchestrator(_config(_provider("primary"))))
    # Unset after the config is built: the schema itself refuses to validate a
    # provider whose key is missing, which is the session-open path, not this one.
    monkeypatch.delenv("PRIMARY_KEY")

    # Do
    result = await service.resolve()

    # Assert
    assert isinstance(result, ProviderAuthRequired)
    assert result.reason == "missing"
    assert result.provider == "primary"
    assert "PRIMARY_KEY" in result.message


@pytest.mark.asyncio
async def test_a_rejected_credential_is_not_offered_again() -> None:
    """*Prepare*: A resolved credential the provider then refuses.
    *Do*: Report the rejection and resolve again.
    *Assert*: The same material is withheld rather than re-sent.
    """
    # Prepare
    service = ProviderCredentialService(_orchestrator(_config(_provider("primary"))))
    resolved = await service.resolve()
    assert isinstance(resolved, ProviderCredentialSnapshot)

    # Do
    await service.reject(
        observed_revision=resolved.revision, reason="http_unauthorized"
    )
    result = await service.resolve()

    # Assert
    assert isinstance(result, ProviderAuthRequired)
    assert result.reason == "rejected"
    assert result.provider == "primary"
    # Nothing recoverable about the key itself reaches the client.
    assert "primary-secret" not in result.message


@pytest.mark.asyncio
async def test_a_replaced_credential_clears_the_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A rejected credential.
    *Do*: Re-export the environment variable with new material and resolve.
    *Assert*: The new key is offered without reopening the session.
    """
    # Prepare
    service = ProviderCredentialService(_orchestrator(_config(_provider("primary"))))
    resolved = await service.resolve()
    assert isinstance(resolved, ProviderCredentialSnapshot)
    await service.reject(
        observed_revision=resolved.revision, reason="http_unauthorized"
    )

    # Do
    monkeypatch.setenv("PRIMARY_KEY", "repaired-secret")
    result = await service.resolve()

    # Assert
    assert isinstance(result, ProviderCredentialSnapshot)
    assert result.token == "repaired-secret"


@pytest.mark.asyncio
async def test_a_stale_rejection_does_not_discard_fresh_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A credential resolved before the user replaced it.
    *Do*: Report the rejection of the superseded revision, then resolve.
    *Assert*: The replacement survives — a late 401 cannot revoke it.
    """
    # Prepare
    service = ProviderCredentialService(_orchestrator(_config(_provider("primary"))))
    in_flight = await service.resolve()
    assert isinstance(in_flight, ProviderCredentialSnapshot)
    monkeypatch.setenv("PRIMARY_KEY", "repaired-secret")

    # Do
    await service.reject(
        observed_revision=in_flight.revision, reason="http_unauthorized"
    )
    result = await service.resolve()

    # Assert
    assert isinstance(result, ProviderCredentialSnapshot)
    assert result.token == "repaired-secret"


@pytest.mark.asyncio
async def test_a_vertex_rejection_drops_the_shared_google_credential() -> None:
    """*Prepare*: A Vertex provider whose process-wide credential is minted.
    *Do*: Report a rejection of the minted token.
    *Assert*: The shared credential is cleared so the next call re-mints.
    """
    # Prepare
    from vibe.core.llm.backend import vertex

    service = ProviderCredentialService(_orchestrator(_config(_vertex_provider())))
    with patch.object(
        VertexCredentials,
        "access_token",
        new_callable=PropertyMock,
        return_value="adc-token",
    ):
        resolved = await service.resolve()
        assert isinstance(resolved, ProviderCredentialSnapshot)
        vertex._CREDENTIALS._credentials = object()  # pyright: ignore[reportAttributeAccessIssue]

        # Do
        await service.reject(
            observed_revision=resolved.revision, reason="http_unauthorized"
        )

    # Assert
    assert vertex._CREDENTIALS._credentials is None


@pytest.mark.asyncio
async def test_resolution_never_raises() -> None:
    """*Prepare*: A provider whose credential lookup raises.
    *Do*: Resolve.
    *Assert*: The failure arrives as a state, not as an exception.

    An exception here would reach the Harness action executor and fail the turn
    with an untyped error instead of the "sign in" state a client can act on.
    """
    # Prepare
    service = ProviderCredentialService(_orchestrator(_config(_vertex_provider())))

    # Do
    with patch.object(
        VertexCredentials,
        "access_token",
        new_callable=PropertyMock,
        side_effect=RuntimeError("no application default credentials"),
    ):
        result = await service.resolve()

    # Assert
    assert isinstance(result, ProviderAuthRequired)
    assert result.reason == "missing"
    assert result.provider == "vertex"


@pytest.mark.asyncio
async def test_an_unresolvable_active_model_is_reported_not_raised() -> None:
    """*Prepare*: A config whose active model names an absent provider.
    *Do*: Resolve.
    *Assert*: An authorization-required state carrying the config's own message.
    """
    # Prepare
    config = _config(_provider("primary"))
    config.providers.clear()
    service = ProviderCredentialService(_orchestrator(config))

    # Do
    result = await service.resolve()

    # Assert
    assert isinstance(result, ProviderAuthRequired)
    assert result.reason == "missing"
    assert "not found in configuration" in result.message


@pytest.mark.asyncio
async def test_the_revision_carries_nothing_of_the_credential() -> None:
    """*Prepare*: A resolved credential.
    *Do*: Read its revision.
    *Assert*: It names the provider and a digest, and never the key.
    """
    # Prepare
    service = ProviderCredentialService(_orchestrator(_config(_provider("primary"))))

    # Do
    resolved = await service.resolve()

    # Assert
    assert isinstance(resolved, ProviderCredentialSnapshot)
    provider, _, digest = resolved.revision.partition(":")
    assert provider == "primary"
    assert len(digest) == 16
    assert "primary-secret" not in resolved.revision
    # Nor through the default repr, which is what a traceback prints.
    assert "primary-secret" not in repr(resolved)


# --- Tier-2 pins: the legacy source of truth, read rather than copied. -------


def _legacy_credential_headers(
    provider: ProviderConfig, api_key: str
) -> dict[str, str]:
    """The credential headers the legacy adapter for ``provider`` would send."""
    prepared = _get_adapter(provider.api_style).prepare_request(
        model_name="a-model",
        messages=[LLMMessage(role=Role.user, content="hello")],
        temperature=0.0,
        tools=None,
        max_tokens=64,
        tool_choice=None,
        enable_streaming=False,
        provider=provider,
        api_key=api_key,
    )
    return {
        name: value
        for name, value in prepared.headers.items()
        if name in _CREDENTIAL_HEADERS
    }


@pytest.mark.parametrize(
    "api_style", ["openai", "openai-responses", "reasoning", "anthropic"]
)
@pytest.mark.asyncio
async def test_headers_match_the_legacy_adapter_for_each_api_style(
    api_style: str,
) -> None:
    """*Prepare*: One provider per ``api_style`` and its legacy adapter.
    *Do*: Resolve a credential and prepare a legacy request with the same key.
    *Assert*: The credential headers are identical.

    Tier-2 pin: the header table in ``ProviderCredentialService`` is a copy of
    ``generic.py``'s ``_ADAPTERS``, and this is what keeps the copy honest.
    """
    # Prepare
    provider = _provider("primary", api_style=api_style)
    service = ProviderCredentialService(_orchestrator(_config(provider)))

    # Do
    resolved = await service.resolve()
    assert isinstance(resolved, ProviderCredentialSnapshot)
    legacy = _legacy_credential_headers(provider, "primary-secret")

    # Assert
    assert dict(resolved.headers) == legacy


@pytest.mark.asyncio
async def test_vertex_headers_match_the_legacy_adapter() -> None:
    """*Prepare*: A Vertex provider and a pinned Google ADC token.
    *Do*: Resolve a credential and prepare a legacy Vertex request.
    *Assert*: The credential headers are identical.
    """
    # Prepare
    provider = _vertex_provider()
    service = ProviderCredentialService(_orchestrator(_config(provider)))

    # Do
    with patch.object(
        VertexCredentials,
        "access_token",
        new_callable=PropertyMock,
        return_value="adc-token",
    ):
        resolved = await service.resolve()
        assert isinstance(resolved, ProviderCredentialSnapshot)
        legacy = _legacy_credential_headers(provider, "ignored-by-vertex")

    # Assert
    assert dict(resolved.headers) == legacy


def test_the_unauthorized_message_is_the_legacy_sentence() -> None:
    """*Prepare*: A legacy ``BackendError`` for a 401.
    *Do*: Read the message the Harness returns for a rejected credential.
    *Assert*: They are the same sentence, character for character.

    Tier-2 pin: the sentence lives in ``BackendError._fmt`` on the legacy path
    and in the Harness on the Unified one, and a user must not be able to tell
    which backend answered.
    """
    # Prepare
    legacy = BackendError(
        provider="primary",
        endpoint="/chat/completions",
        status=401,
        reason="Unauthorized",
        headers={},
        body_text="",
        parsed_error=None,
        model="a-model",
        payload_summary=PayloadSummary(
            model="a-model",
            message_count=1,
            approx_chars=5,
            temperature=0.0,
            has_tools=False,
            tool_choice=None,
        ),
    )

    # Do / Assert
    assert INVALID_API_KEY_MESSAGE == str(legacy)
