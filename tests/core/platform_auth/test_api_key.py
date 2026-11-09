from __future__ import annotations

import pytest

from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialIdentity,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.utils.api_keys import session_credentials


def _target() -> PlatformCredentialTarget:
    return PlatformCredentialTarget(base_url="https://api.mistral.ai/v1")


@pytest.mark.asyncio
async def test_resolves_the_current_api_key_on_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY")
    monkeypatch.setenv("MISTRAL_API_KEY", "first-secret")

    first = await provider.resolve(_target())
    monkeypatch.setenv("MISTRAL_API_KEY", "second-secret")
    second = await provider.resolve(_target())

    assert isinstance(first, PlatformCredentialSnapshot)
    assert isinstance(second, PlatformCredentialSnapshot)
    assert first.headers == {"Authorization": "Bearer first-secret"}
    assert second.headers == {"Authorization": "Bearer second-secret"}
    assert first.revision != second.revision


@pytest.mark.asyncio
async def test_session_credential_overrides_environment_and_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY")
    monkeypatch.setenv("MISTRAL_API_KEY", "environment-secret")
    monkeypatch.setattr(
        "vibe.utils.api_keys.get_api_key_from_keyring",
        lambda _env_var: "keyring-secret",
    )

    with session_credentials({"MISTRAL_API_KEY": "session-secret"}):
        resolved = await provider.resolve(_target())

    assert isinstance(resolved, PlatformCredentialSnapshot)
    assert resolved.headers == {"Authorization": "Bearer session-secret"}
    assert resolved.source == "the run's credential MISTRAL_API_KEY"


@pytest.mark.asyncio
async def test_missing_resolved_key_returns_typed_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_var = "ABSENT_PLATFORM_API_KEY"
    monkeypatch.delenv(env_var, raising=False)
    monkeypatch.setattr(
        "vibe.utils.api_keys.get_api_key_from_keyring", lambda _env_var: None
    )

    resolved = await ApiKeyPlatformCredentialProvider(env_var).resolve(_target())

    assert resolved == PlatformCredentialRequired(reason="missing")


@pytest.mark.asyncio
async def test_fixed_empty_key_does_not_fall_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "ambient-secret")

    resolved = await ApiKeyPlatformCredentialProvider.from_api_key("").resolve(
        _target()
    )

    assert resolved == PlatformCredentialRequired(reason="missing")


@pytest.mark.asyncio
async def test_rejected_revision_stays_rejected_until_the_key_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY")
    monkeypatch.setenv("MISTRAL_API_KEY", "rejected-secret")
    initial = await provider.resolve(_target())
    assert isinstance(initial, PlatformCredentialSnapshot)

    rejected = await provider.reject(
        _target(), observed=initial.identity, reason="http_unauthorized"
    )
    repeated = await provider.resolve(_target())
    monkeypatch.setenv("MISTRAL_API_KEY", "replacement-secret")
    replacement = await provider.resolve(_target())

    assert (
        rejected
        == repeated
        == PlatformCredentialRequired(
            reason="rejected", observed_revision=initial.revision
        )
    )
    assert isinstance(replacement, PlatformCredentialSnapshot)
    assert replacement.revision != initial.revision


@pytest.mark.asyncio
async def test_stale_rejection_does_not_discard_a_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY")
    monkeypatch.setenv("MISTRAL_API_KEY", "old-secret")
    old = await provider.resolve(_target())
    assert isinstance(old, PlatformCredentialSnapshot)
    monkeypatch.setenv("MISTRAL_API_KEY", "new-secret")

    result = await provider.reject(
        _target(), observed=old.identity, reason="http_unauthorized"
    )

    assert isinstance(result, PlatformCredentialSnapshot)
    assert result.headers == {"Authorization": "Bearer new-secret"}


@pytest.mark.asyncio
async def test_rejection_from_another_namespace_does_not_reject_the_current_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY")
    monkeypatch.setenv("MISTRAL_API_KEY", "current-secret")
    current = await provider.resolve(_target())
    assert isinstance(current, PlatformCredentialSnapshot)

    result = await provider.reject(
        _target(),
        observed=PlatformCredentialIdentity(
            kind=current.kind,
            cache_namespace="oauth:other-connection",
            revision=current.revision,
        ),
        reason="http_unauthorized",
    )

    assert result == current
    assert await provider.resolve(_target()) == current


def test_snapshot_repr_and_mutability_do_not_expose_the_key() -> None:
    snapshot = PlatformCredentialSnapshot(
        kind="api_key",
        headers={"Authorization": "Bearer secret"},
        cache_namespace="api_key:test",
        revision="revision",
    )

    assert "secret" not in repr(snapshot)
    with pytest.raises(TypeError):
        snapshot.headers["Authorization"] = "replacement"  # type: ignore[index]
