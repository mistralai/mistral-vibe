from __future__ import annotations

from typing import Literal

import httpx
import pytest
import respx

from tests.conftest import build_test_vibe_config
from vibe.core.experiments.manager import hash_api_key
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialIdentity,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
    PlatformCredentialUnavailable,
)
from vibe.core.platform_auth._mistral import (
    OIDC_PRINCIPAL_PREFIX,
    credential_request,
    fetched_for_current_account,
    may_cache_under,
    mistral_credentials,
)
from vibe.core.telemetry.send import mistral_credential_cache_key


class _UserToken:
    """A user-token provider: an ID token for one principal."""

    def __init__(self, principal: str, *, token: str = "id-token") -> None:
        self.principal = principal
        self.token = token
        self.rejected: list[str] = []

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        return PlatformCredentialSnapshot(
            kind="oidc_id_token",
            headers={"Authorization": f"Bearer {self.token}"},
            cache_namespace="oidc_id_token:test",
            revision=self.token,
            principal=self.principal,
        )

    async def reject(
        self,
        target: PlatformCredentialTarget,
        *,
        observed: PlatformCredentialIdentity,
        reason: Literal["http_unauthorized"],
    ) -> PlatformCredentialResult:
        self.rejected.append(observed.revision)
        return await self.resolve(target)

    def has_credential(self) -> bool:
        return True

    def cached_principal(self) -> str | None:
        return self.principal


def test_api_key_principal_keeps_the_existing_cache_keys(monkeypatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "existing-key")
    assert mistral_credential_cache_key(build_test_vibe_config()) == hash_api_key(
        "existing-key"
    )


def test_a_user_token_keys_caches_by_principal_never_by_token() -> None:
    principal = f"{OIDC_PRINCIPAL_PREFIX}user-a"
    provider = _UserToken(principal)
    key = mistral_credential_cache_key(build_test_vibe_config(), lambda _: provider)
    assert key == principal
    provider.token = "rotated"
    assert (
        mistral_credential_cache_key(build_test_vibe_config(), lambda _: provider)
        == principal
    )


@pytest.mark.parametrize(
    ("key", "fetched_for", "allowed"),
    [
        ("api-key-hash", None, True),
        ("api-key-hash", "api-key-hash", True),
        (f"{OIDC_PRINCIPAL_PREFIX}a", f"{OIDC_PRINCIPAL_PREFIX}a", True),
        (f"{OIDC_PRINCIPAL_PREFIX}b", f"{OIDC_PRINCIPAL_PREFIX}a", False),
        (f"{OIDC_PRINCIPAL_PREFIX}a", None, False),
        (None, None, False),
    ],
)
def test_a_user_response_is_cached_only_for_the_account_it_was_fetched_for(
    key, fetched_for, allowed
) -> None:
    assert may_cache_under(key, fetched_for=fetched_for) is allowed


@pytest.mark.parametrize(
    ("fetched_for", "provider", "current"),
    [
        ("api-key-hash", ApiKeyPlatformCredentialProvider.from_api_key("k"), True),
        ("api-key-hash", object(), True),
        (f"{OIDC_PRINCIPAL_PREFIX}a", _UserToken(f"{OIDC_PRINCIPAL_PREFIX}a"), True),
        (f"{OIDC_PRINCIPAL_PREFIX}a", _UserToken(f"{OIDC_PRINCIPAL_PREFIX}b"), False),
        (f"{OIDC_PRINCIPAL_PREFIX}a", object(), False),
    ],
)
def test_a_user_fetch_is_current_only_while_its_account_is_signed_in(
    fetched_for, provider, current
) -> None:
    assert fetched_for_current_account(provider, fetched_for) is current


def test_a_missing_api_key_means_no_first_party_call(monkeypatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert mistral_credentials(build_test_vibe_config()) is None


@pytest.mark.asyncio
@respx.mock
async def test_a_user_token_never_follows_a_redirect() -> None:
    route = respx.get("https://api.mistral.ai/v1/models").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://elsewhere.example/steal"}
        )
    )
    elsewhere = respx.get("https://elsewhere.example/steal")
    response = await credential_request(
        _UserToken(f"{OIDC_PRINCIPAL_PREFIX}a"),
        "GET",
        "https://api.mistral.ai/v1/models",
    )
    assert response.status_code == 302
    assert route.call_count == 1
    assert not elsewhere.called


@pytest.mark.asyncio
@respx.mock
async def test_a_401_tells_the_provider_which_credential_was_refused() -> None:
    respx.get("https://api.mistral.ai/v1/models").mock(return_value=httpx.Response(401))
    provider = _UserToken(f"{OIDC_PRINCIPAL_PREFIX}a", token="sent-token")
    await credential_request(provider, "GET", "https://api.mistral.ai/v1/models")
    assert provider.rejected == ["sent-token"]


@pytest.mark.asyncio
async def test_no_credential_raises_before_any_request(monkeypatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(PlatformCredentialUnavailable):
        await credential_request(
            ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY"),
            "GET",
            "https://api.mistral.ai/v1/models",
        )
