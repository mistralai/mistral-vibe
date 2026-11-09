"""``ProviderCredentialService``: the Vibe side of the Harness credential port."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import PropertyMock, patch

import keyring
from keyring.errors import KeyringError
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
import vibe.utils.api_keys as api_keys
from vibe.utils.api_keys import ApiKeyOrigin, ApiKeySource
import vibe.utils.keyring as keyring_utils

if TYPE_CHECKING:
    from vibe.core.config.orchestrator import ConfigOrchestrator

pytest.importorskip("mistralai_vibe_local_harness.vibe")

# Both imports sit below the skip because both reach the Harness credential
# port at module scope -- the service implements it, so importing the service
# is importing it. `mistralai-vibe-local-harness` is an optional extra, and an
# environment without it must skip this module rather than fail to collect it.
from mistralai_vibe_local_harness.vibe import (
    ProviderAuthRequired,
    ProviderCredentialSnapshot,
    invalid_api_key_message,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._vertex import (
    VERTEX_CREDENTIALS,
    VertexAnthropicAdapter,
    VertexCredentials,
)
from mistralai_vibe_local_harness.vibe.completion_profile import ANTHROPIC_VERSION
from vibe.app_server._provider_credentials import ProviderCredentialService
from vibe.core.platform_auth._mistral import api_key_credentials


def _provider(name: str, *, api_style: str = "openai", **kwargs: Any) -> ProviderConfig:
    kwargs.setdefault("api_key_env_var", f"{name.upper()}_KEY")
    return ProviderConfig(
        name=name, api_base=f"https://{name}.example/v1", api_style=api_style, **kwargs
    )


def _vertex_provider() -> ProviderConfig:
    # No key environment variable: Vertex mints a Google ADC token and ignores
    # whatever is configured.
    return _provider(
        "vertex",
        api_style="vertex-anthropic",
        api_key_env_var="",
        project_id="a-project",
        region="us-central1",
    )


def _config(
    *providers: ProviderConfig, active: str | None = None, **kwargs: Any
) -> VibeConfigSchema:
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
        **kwargs,
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
async def test_a_utility_route_keeps_its_providers_key_after_a_switch() -> None:
    """The route's endpoint is fixed at derivation; its key must not follow the
    live selection to another provider.
    """
    from vibe.app_server._runtime import _named_provider

    orchestrator = _orchestrator(
        _config(_provider("primary"), _provider("secondary"), active="primary")
    )
    service = ProviderCredentialService(
        orchestrator, select_provider=_named_provider("primary")
    )

    await orchestrator.set_field("/active_model", "secondary-model")
    result = await service.resolve()

    assert isinstance(result, ProviderCredentialSnapshot)
    assert result.token == "primary-secret"


def test_a_utility_routes_provider_gone_from_config_raises() -> None:
    from vibe.app_server._runtime import _named_provider

    select = _named_provider("removed")

    with pytest.raises(ValueError, match="no longer configured"):
        select(_config(_provider("primary"), active="primary"))


@pytest.mark.asyncio
async def test_select_provider_resolves_a_non_active_provider() -> None:
    """*Prepare*: Two providers, the first active; a service bound to the second.
    *Do*: Resolve.
    *Assert*: The second provider's key is returned, not the active one's.

    Background title generation uses this to run on the Mistral utility provider
    while the session's active provider is something else.
    """
    orchestrator = _orchestrator(
        _config(_provider("primary"), _provider("secondary"), active="primary")
    )
    service = ProviderCredentialService(
        orchestrator,
        select_provider=lambda config: next(
            provider for provider in config.providers if provider.name == "secondary"
        ),
    )

    result = await service.resolve()

    assert isinstance(result, ProviderCredentialSnapshot)
    assert result.token == "secondary-secret"


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
async def test_an_unreadable_keyring_is_named_instead_of_a_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProviderCredentialService(_orchestrator(_config(_provider("primary"))))
    monkeypatch.delenv("PRIMARY_KEY")

    def _locked(service_name: str, username: str) -> str | None:
        raise KeyringError("keychain locked")

    monkeypatch.setattr(keyring, "get_password", _locked)

    result = await service.resolve()

    assert isinstance(result, ProviderAuthRequired)
    assert result.reason == "missing"
    assert "Can't read PRIMARY_KEY from the system keyring" in result.message
    assert "keychain locked" in result.message
    assert "Missing PRIMARY_KEY" not in result.message


@pytest.mark.asyncio
async def test_a_turn_after_the_keyring_is_unlocked_reads_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ProviderCredentialService(_orchestrator(_config(_provider("primary"))))
    monkeypatch.delenv("PRIMARY_KEY")
    elapsed = [0.0]
    real_monotonic = keyring_utils.time.monotonic
    monkeypatch.setattr(
        keyring_utils.time, "monotonic", lambda: real_monotonic() + elapsed[0]
    )
    locked = True

    def _get(service_name: str, username: str) -> str | None:
        if locked:
            raise KeyringError("keychain locked")
        return "unlocked-key"

    monkeypatch.setattr(keyring, "get_password", _get)
    assert isinstance(await service.resolve(), ProviderAuthRequired)

    locked = False
    elapsed[0] = keyring_utils.READ_FAILURE_TTL_SECONDS

    result = await service.resolve()

    assert isinstance(result, ProviderCredentialSnapshot)
    assert keyring_utils.keyring_read_failure("PRIMARY_KEY") is None


@pytest.mark.asyncio
async def test_a_provider_without_a_key_requirement_resolves_empty_credentials() -> (
    None
):
    """A keyless local provider resolves without asking the client to sign in."""
    provider = _provider("local", api_key_env_var="")
    service = ProviderCredentialService(_orchestrator(_config(provider)))

    result = await service.resolve()

    assert isinstance(result, ProviderCredentialSnapshot)
    assert result.token is None
    assert dict(result.headers) == {}
    assert result.api_key_source is None


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
    service = ProviderCredentialService(_orchestrator(_config(_vertex_provider())))
    with patch.object(
        VertexCredentials,
        "access_token",
        new_callable=PropertyMock,
        return_value="adc-token",
    ):
        resolved = await service.resolve()
        assert isinstance(resolved, ProviderCredentialSnapshot)
        VERTEX_CREDENTIALS._credentials = object()  # pyright: ignore[reportAttributeAccessIssue]

        # Do
        await service.reject(
            observed_revision=resolved.revision, reason="http_unauthorized"
        )

    # Assert
    assert VertexAnthropicAdapter().credentials is VERTEX_CREDENTIALS
    assert VERTEX_CREDENTIALS._credentials is None


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
async def test_a_credential_says_which_source_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A provider key in the environment, then only in the keyring.
    *Do*: Resolve each.
    *Assert*: The snapshot names the source that answered.

    The variable keys both sources, so it alone cannot say which one did. The
    Harness cannot read Vibe's config, and this is what it puts in the message
    a refused credential produces.
    """
    # Prepare
    service = ProviderCredentialService(_orchestrator(_config(_provider("primary"))))

    # Do
    from_env = await service.resolve()
    monkeypatch.delenv("PRIMARY_KEY")
    monkeypatch.setattr(
        api_keys, "get_api_key_from_keyring", lambda _key: "from-the-keyring"
    )
    from_keyring = await service.resolve()

    # Assert
    assert isinstance(from_env, ProviderCredentialSnapshot)
    assert isinstance(from_keyring, ProviderCredentialSnapshot)
    assert from_env.api_key_source == "env var PRIMARY_KEY"
    assert from_keyring.api_key_source == "the keyring"


@pytest.mark.asyncio
async def test_each_session_resolves_its_own_credentials_before_the_process_key() -> (
    None
):
    """*Prepare*: Three services on one config: two with credentials of their
    own for the provider's variable, one without.
    *Do*: Resolve all three at once.
    *Assert*: Each with credentials sends its own, named as the run's; the
    other sends the process's key.
    """
    # Prepare
    orchestrator = _orchestrator(_config(_provider("primary")))
    first = ProviderCredentialService(
        orchestrator, credentials={"PRIMARY_KEY": "run-a-secret"}
    )
    second = ProviderCredentialService(
        orchestrator, credentials={"PRIMARY_KEY": "run-b-secret"}
    )
    plain = ProviderCredentialService(orchestrator)

    # Do
    results = await asyncio.gather(first.resolve(), second.resolve(), plain.resolve())

    # Assert
    assert all(isinstance(result, ProviderCredentialSnapshot) for result in results)
    tokens = [cast(ProviderCredentialSnapshot, result).token for result in results]
    assert tokens == ["run-a-secret", "run-b-secret", "primary-secret"]
    first_result = cast(ProviderCredentialSnapshot, results[0])
    assert first_result.api_key_source == "the run's credential PRIMARY_KEY"


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


@pytest.mark.parametrize(
    ("api_style", "expected"),
    [
        ("openai", {"Authorization": "Bearer primary-secret"}),
        ("openai-responses", {"Authorization": "Bearer primary-secret"}),
        ("reasoning", {"Authorization": "Bearer primary-secret"}),
        (
            "anthropic",
            {"x-api-key": "primary-secret", "anthropic-version": ANTHROPIC_VERSION},
        ),
    ],
)
@pytest.mark.asyncio
async def test_headers_follow_each_api_style(
    api_style: str, expected: dict[str, str]
) -> None:
    """*Prepare*: One provider per ``api_style``.
    *Do*: Resolve a credential.
    *Assert*: The credential headers are the ones that style's API expects.
    """
    # Prepare
    provider = _provider("primary", api_style=api_style)
    service = ProviderCredentialService(_orchestrator(_config(provider)))

    # Do
    resolved = await service.resolve()

    # Assert
    assert isinstance(resolved, ProviderCredentialSnapshot)
    assert dict(resolved.headers) == expected


@pytest.mark.asyncio
async def test_vertex_headers_carry_the_minted_token() -> None:
    """*Prepare*: A Vertex provider and a pinned Google ADC token.
    *Do*: Resolve a credential.
    *Assert*: The token goes out as a bearer header, and nothing else.
    """
    # Prepare
    service = ProviderCredentialService(_orchestrator(_config(_vertex_provider())))

    # Do
    with patch.object(
        VertexCredentials,
        "access_token",
        new_callable=PropertyMock,
        return_value="adc-token",
    ):
        resolved = await service.resolve()

    # Assert
    assert isinstance(resolved, ProviderCredentialSnapshot)
    assert dict(resolved.headers) == {"Authorization": "Bearer adc-token"}


@pytest.mark.parametrize(
    ("api_key_origin", "sentence"),
    [
        (None, "Invalid API key. Please check your API key and try again."),
        (
            ApiKeyOrigin(ApiKeySource.ENVIRONMENT, "PRIMARY_API_KEY"),
            "Invalid API key (from env var PRIMARY_API_KEY). "
            "Please check your API key and try again.",
        ),
    ],
)
def test_the_unauthorized_message_names_the_key_source(
    api_key_origin: ApiKeyOrigin | None, sentence: str
) -> None:
    """*Prepare*: A credential origin, or none.
    *Do*: Read the message the Harness returns for a rejected credential.
    *Assert*: It is the user-facing sentence, naming the source when known.
    """
    # Prepare
    described = api_key_origin.describe() if api_key_origin else None

    # Do
    message = invalid_api_key_message(described)

    # Assert
    assert message == sentence


class _ConnectionToken:
    """A connection's first-party credential (a CLI or Desktop user token)."""

    def __init__(
        self,
        *,
        signed_in: bool = True,
        rotates: bool = False,
        reason: str = "reauthentication_required",
    ) -> None:
        self.signed_in = signed_in
        # Why a signed-out connection has no token.
        self.reason = reason
        # Whether the owner (Desktop) issues a new token when told of a 401.
        self.rotates = rotates
        self.revision = "revision-of-user-id-token"
        self.rejected: list[Any] = []
        self.env_vars: list[str] = []

    def __call__(self, env_var: str) -> _ConnectionToken:
        self.env_vars.append(env_var)
        return self

    async def resolve(self, target: Any) -> Any:
        from vibe.core.platform_auth import (
            PlatformCredentialRequired,
            PlatformCredentialSnapshot,
        )

        if not self.signed_in:
            return PlatformCredentialRequired(reason=self.reason)  # type: ignore[arg-type]
        return PlatformCredentialSnapshot(
            kind="oidc_id_token",
            headers={
                "Authorization": "Bearer user-id-token",
                "x-external-organization-id": "org",
            },
            cache_namespace="oidc_id_token:test",
            revision=self.revision,
            source="the connection",
            principal="oidc:v1:user",
        )

    async def reject(self, target: Any, *, observed: Any, **_: Any) -> Any:
        self.rejected.append(observed)
        if self.rotates:
            self.revision = "revision-of-a-fresh-user-id-token"
        return await self.resolve(target)


def _service(
    monkeypatch: pytest.MonkeyPatch,
    connection: _ConnectionToken,
    *,
    providers: list[dict[str, Any]] | None = None,
    run_keys: dict[str, str] | None = None,
) -> ProviderCredentialService:
    monkeypatch.setenv("MISTRAL_API_KEY", "process-api-key")
    config = (
        build_test_vibe_config(providers=providers)
        if providers
        else build_test_vibe_config()
    )
    return ProviderCredentialService(
        cast("ConfigOrchestrator[VibeConfigSchema]", FakeConfigOrchestrator(config)),
        credentials=run_keys,
        platform_credentials=connection,
    )


@pytest.mark.asyncio
async def test_mistral_models_use_the_connection_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _ConnectionToken()
    resolved = await _service(monkeypatch, connection).resolve()
    assert isinstance(resolved, ProviderCredentialSnapshot)
    assert resolved.headers["Authorization"] == "Bearer user-id-token"
    assert resolved.headers["x-external-organization-id"] == "org"
    assert resolved.follow_redirects is False
    assert "user-id-token" not in resolved.revision
    assert connection.env_vars == ["MISTRAL_API_KEY"]


@pytest.mark.asyncio
async def test_a_signed_out_connection_never_falls_back_to_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = await _service(monkeypatch, _ConnectionToken(signed_in=False)).resolve()
    assert isinstance(resolved, ProviderAuthRequired)
    assert "API_KEY" not in resolved.message


@pytest.mark.parametrize(
    ("reason", "expected_reason", "hint"),
    [
        ("reauthentication_required", "expired", "Sign in again"),
        ("expired", "expired", "Sign in again"),
        ("rejected", "rejected", "Sign in again"),
        ("invalid", "missing", "Sign in again"),
        ("missing", "missing", "Sign in"),
        ("transient", "missing", "Retry"),
    ],
)
@pytest.mark.asyncio
async def test_a_connection_without_a_token_asks_to_sign_in_not_for_a_key(
    monkeypatch: pytest.MonkeyPatch, reason: str, expected_reason: str, hint: str
) -> None:
    """*Prepare*: A signed-in connection whose token cannot be used, for each
      reason the credential reports.
    *Do*: Resolve the model credential.
    *Assert*: The user is told to sign in (or retry), never to set an API key.
    """
    # Prepare
    service = _service(monkeypatch, _ConnectionToken(signed_in=False, reason=reason))

    # Do
    resolved = await service.resolve()

    # Assert
    assert isinstance(resolved, ProviderAuthRequired)
    assert resolved.reason == expected_reason
    assert hint in resolved.message
    assert "API_KEY" not in resolved.message
    assert "API key" not in resolved.message


@pytest.mark.asyncio
async def test_an_api_key_connection_without_a_key_still_asks_for_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: An API-key connection and no key anywhere.
    *Do*: Resolve the model credential.
    *Assert*: The message names the missing key, as before.
    """
    # Prepare
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    service = ProviderCredentialService(
        cast(
            "ConfigOrchestrator[VibeConfigSchema]",
            FakeConfigOrchestrator(build_test_vibe_config()),
        ),
        platform_credentials=api_key_credentials,
    )

    # Do
    resolved = await service.resolve()

    # Assert
    assert isinstance(resolved, ProviderAuthRequired)
    assert resolved.reason == "missing"
    assert "MISTRAL_API_KEY" in resolved.message


@pytest.mark.asyncio
async def test_a_rejected_api_key_connection_asks_to_update_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: An API-key connection whose key the model refused (401).
    *Do*: Resolve the model credential again.
    *Assert*: The user is told to update the API key, not that it is missing.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "refused-key")
    service = ProviderCredentialService(
        cast(
            "ConfigOrchestrator[VibeConfigSchema]",
            FakeConfigOrchestrator(build_test_vibe_config()),
        ),
        platform_credentials=api_key_credentials,
    )
    sent = await service.resolve()
    assert isinstance(sent, ProviderCredentialSnapshot)
    await service.reject(observed_revision=sent.revision, reason="http_unauthorized")

    # Do
    resolved = await service.resolve()

    # Assert
    assert isinstance(resolved, ProviderAuthRequired)
    assert resolved.reason == "rejected"
    assert "Update the API key" in resolved.message


@pytest.mark.asyncio
async def test_a_third_party_provider_keeps_its_own_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTHER_API_KEY", "other-key")
    connection = _ConnectionToken()
    service = _service(
        monkeypatch,
        connection,
        providers=[
            {
                "name": "other",
                "api_base": "https://other.example/v1",
                "api_key_env_var": "OTHER_API_KEY",
                "api_style": "openai",
                "backend": "generic",
            }
        ],
    )
    monkeypatch.setattr(
        VibeConfigSchema, "get_active_provider", lambda self: self.providers[0]
    )
    service._select_provider = lambda config: config.providers[0]
    resolved = await service.resolve()
    assert isinstance(resolved, ProviderCredentialSnapshot)
    assert resolved.headers == {"Authorization": "Bearer other-key"}
    assert connection.env_vars == []


@pytest.mark.asyncio
async def test_a_runs_own_key_wins_over_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _ConnectionToken()
    resolved = await _service(
        monkeypatch, connection, run_keys={"MISTRAL_API_KEY": "run-key"}
    ).resolve()
    assert isinstance(resolved, ProviderCredentialSnapshot)
    assert resolved.headers == {"Authorization": "Bearer run-key"}
    assert connection.env_vars == []


@pytest.mark.asyncio
async def test_a_rejected_connection_token_is_reported_to_its_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _ConnectionToken(rotates=True)
    service = _service(monkeypatch, connection)
    sent = await service.resolve()
    assert isinstance(sent, ProviderCredentialSnapshot)

    await service.reject(observed_revision=sent.revision, reason="http_unauthorized")
    result = await service.resolve()

    assert [observed.revision for observed in connection.rejected] == [
        "revision-of-user-id-token"
    ]
    assert isinstance(result, ProviderCredentialSnapshot)
    assert result.revision != sent.revision


@pytest.mark.asyncio
async def test_a_connection_token_its_owner_keeps_is_withheld_with_a_sign_in_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _ConnectionToken()
    service = _service(monkeypatch, connection)
    sent = await service.resolve()
    assert isinstance(sent, ProviderCredentialSnapshot)

    await service.reject(observed_revision=sent.revision, reason="http_unauthorized")
    result = await service.resolve()

    assert len(connection.rejected) == 1
    assert isinstance(result, ProviderAuthRequired)
    assert result.reason == "rejected"
    assert "Sign in again" in result.message
    assert "API key" not in result.message


@pytest.mark.asyncio
async def test_a_signed_out_connection_asks_to_sign_in_not_for_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = await _service(monkeypatch, _ConnectionToken(signed_in=False)).resolve()
    assert isinstance(resolved, ProviderAuthRequired)
    assert "Sign in" in resolved.message
    assert "MISTRAL_API_KEY" not in resolved.message


@pytest.mark.asyncio
async def test_a_connection_token_refused_after_a_refresh_asks_to_update_or_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A connection whose owner already refreshed the token once.
    *Do*: The model refuses the refreshed token (401), then the next turn
      resolves the credential.
    *Assert*: The next turn says to update Vibe or sign in again.
    """
    from vibe.core.platform_auth import (
        UNAUTHORIZED_AFTER_REFRESH_MESSAGE,
        PlatformCredentialRequired,
    )

    # Prepare
    connection = _ConnectionToken()
    service = _service(monkeypatch, connection)
    sent = await service.resolve()
    assert isinstance(sent, ProviderCredentialSnapshot)

    async def refused_after_refresh(target: Any, *, observed: Any, **_: Any) -> Any:
        connection.rejected.append(observed)
        return PlatformCredentialRequired(
            reason="unauthorized_after_refresh", observed_revision=observed.revision
        )

    connection.reject = refused_after_refresh  # type: ignore[method-assign]

    # Do
    await service.reject(observed_revision=sent.revision, reason="http_unauthorized")
    result = await service.resolve()

    # Assert
    assert isinstance(result, ProviderAuthRequired)
    assert result.reason == "rejected"
    assert result.message == UNAUTHORIZED_AFTER_REFRESH_MESSAGE


@pytest.mark.asyncio
async def test_a_connection_reporting_unauthorized_after_refresh_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe.core.platform_auth import UNAUTHORIZED_AFTER_REFRESH_MESSAGE

    service = _service(
        monkeypatch,
        _ConnectionToken(signed_in=False, reason="unauthorized_after_refresh"),
    )

    resolved = await service.resolve()

    assert isinstance(resolved, ProviderAuthRequired)
    assert resolved.message == UNAUTHORIZED_AFTER_REFRESH_MESSAGE
