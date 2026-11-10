from __future__ import annotations

import asyncio
import os
import time
from typing import Any, cast

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
import httpx2
import keyring
from keyring.errors import PasswordDeleteError
from pydantic import SecretStr
import pytest

from vibe.setup.auth._oidc_client import OidcClient, OidcMetadata
from vibe.setup.auth._oidc_config import OidcConfig, OidcError
from vibe.setup.auth._oidc_credentials import OidcCredentialStore, OidcTokens
from vibe.setup.auth._oidc_service import OidcService

ISSUER = "https://issuer.example"
CLIENT_ID = "vibe-public-cli"
AUDIENCE = "https://api.example"
REDIRECT_URI = "http://127.0.0.1:8766/callback"
SUBJECT = "subject-123"
ACCESS_TOKEN = "access-secret"
REFRESH_TOKEN = "refresh-secret"
REFRESHED_ACCESS_TOKEN = "refreshed-access-secret"
EXPIRES_IN = 3600
_SERVICE = "ai.mistral.vibe.oidc"
_ENTRY = "cli-oidc:v1:tokens"


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.values:
            raise PasswordDeleteError()
        del self.values[service, username]


class StubSession:
    def __init__(self) -> None:
        self.refresh_calls: list[dict[str, Any]] = []
        self.response: dict[str, Any] = {
            "access_token": REFRESHED_ACCESS_TOKEN,
            "token_type": "Bearer",
            "expires_in": EXPIRES_IN,
        }
        self.error: Exception | None = None
        self.closed = False

    async def __aenter__(self) -> StubSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    async def refresh_token(
        self, url: str, *, refresh_token: str, scope: Any = None
    ) -> dict[str, Any]:
        self.refresh_calls.append({
            "url": url,
            "refresh_token": refresh_token,
            "scope": scope,
        })
        if self.error is not None:
            raise self.error
        return dict(self.response)


class StubClient(OidcClient):
    def __init__(self, config: OidcConfig) -> None:
        super().__init__(config)
        self.stub_session = StubSession()
        self.session_calls = 0
        self.discover_calls = 0

    def session(self) -> AsyncOAuth2Client:
        self.session_calls += 1
        return cast(AsyncOAuth2Client, self.stub_session)

    async def discover(self, session: AsyncOAuth2Client) -> OidcMetadata:
        self.discover_calls += 1
        return OidcMetadata(
            issuer=self.config.issuer,
            authorization_endpoint=f"{self.config.issuer}/authorize",
            token_endpoint=f"{self.config.issuer}/token",
            jwks_uri=f"{self.config.issuer}/jwks",
        )


@pytest.fixture(autouse=True)
def oidc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("VIBE_CLI_OIDC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.delenv("VIBE_TEST_DISABLE_KEYRING", raising=False)


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    backend = FakeKeyring()
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


@pytest.fixture
def config() -> OidcConfig:
    return OidcConfig(ISSUER, CLIENT_ID, AUDIENCE, REDIRECT_URI)


@pytest.fixture
def store() -> OidcCredentialStore:
    return OidcCredentialStore()


@pytest.fixture
def client(config: OidcConfig) -> StubClient:
    return StubClient(config)


@pytest.fixture
def service(client: StubClient, store: OidcCredentialStore) -> OidcService:
    return OidcService(client, store)


@pytest.fixture
def make_tokens(config: OidcConfig):
    def factory(
        *,
        expires_in: float = EXPIRES_IN,
        refresh_token: str | None = REFRESH_TOKEN,
        **overrides: Any,
    ) -> OidcTokens:
        fields: dict[str, Any] = {
            "issuer": config.issuer,
            "client_id": config.client_id,
            "audience": config.audience,
            "subject": SUBJECT,
            "access_token": SecretStr(ACCESS_TOKEN),
            "refresh_token": (
                SecretStr(refresh_token) if refresh_token is not None else None
            ),
            "id_token": SecretStr("id-token"),
            "expires_at": time.time() + expires_in,
        }
        fields.update(overrides)
        return OidcTokens(**fields)

    return factory


@pytest.mark.asyncio
async def test_unexpired_token_is_returned_without_refresh(
    service: OidcService, client: StubClient, store: OidcCredentialStore, make_tokens
) -> None:
    stored = make_tokens(expires_in=EXPIRES_IN)
    await store.save(stored)
    result = await service.access_tokens()
    assert result == stored
    assert client.session_calls == 0
    assert client.stub_session.refresh_calls == []
    assert await store.load() == stored


@pytest.mark.parametrize("expires_in,expect_refresh", [(29, True), (31, False)])
@pytest.mark.asyncio
async def test_expiry_skew_boundary_decides_refresh(
    service: OidcService,
    client: StubClient,
    store: OidcCredentialStore,
    make_tokens,
    expires_in: float,
    expect_refresh: bool,
) -> None:
    stored = make_tokens(expires_in=expires_in)
    await store.save(stored)
    result = await service.access_tokens()
    if expect_refresh:
        assert len(client.stub_session.refresh_calls) == 1
        assert result.access_token == SecretStr(REFRESHED_ACCESS_TOKEN)
        assert result.expires_at > time.time() + EXPIRES_IN - 60
        assert await store.load() == result
    else:
        assert client.stub_session.refresh_calls == []
        assert result == stored


@pytest.mark.asyncio
async def test_force_refresh_renews_unexpired_token(
    service: OidcService, client: StubClient, store: OidcCredentialStore, make_tokens
) -> None:
    stored = make_tokens(expires_in=EXPIRES_IN)
    await store.save(stored)
    result = await service.access_tokens(force_refresh=True)
    assert len(client.stub_session.refresh_calls) == 1
    assert result.access_token == SecretStr(REFRESHED_ACCESS_TOKEN)
    assert result.expires_at > time.time() + EXPIRES_IN - 60
    assert await store.load() == result


@pytest.mark.asyncio
async def test_refresh_posts_refresh_token_without_scope(
    service: OidcService, client: StubClient, store: OidcCredentialStore, make_tokens
) -> None:
    stored = make_tokens(expires_in=-60)
    await store.save(stored)
    await service.access_tokens()
    assert client.stub_session.refresh_calls == [
        {"url": f"{ISSUER}/token", "refresh_token": REFRESH_TOKEN, "scope": None}
    ]


@pytest.mark.parametrize("rotated", [None, "rotated-refresh-secret"])
@pytest.mark.asyncio
async def test_refresh_rotates_or_preserves_refresh_token(
    service: OidcService,
    client: StubClient,
    store: OidcCredentialStore,
    make_tokens,
    rotated: str | None,
) -> None:
    stored = make_tokens(expires_in=-60)
    await store.save(stored)
    if rotated is not None:
        client.stub_session.response["refresh_token"] = rotated
    result = await service.access_tokens()
    assert result.access_token == SecretStr(REFRESHED_ACCESS_TOKEN)
    assert result.refresh_token == SecretStr(rotated or REFRESH_TOKEN)
    assert result.subject == stored.subject
    assert result.id_token == stored.id_token
    assert result.expires_at > time.time() + EXPIRES_IN - 60
    assert await store.load() == result


@pytest.mark.asyncio
async def test_stale_memory_copy_is_rescued_by_reload_from_keyring(
    service: OidcService,
    client: StubClient,
    store: OidcCredentialStore,
    fake_keyring: FakeKeyring,
    make_tokens,
) -> None:
    stale = make_tokens(expires_in=-60, access_token=SecretStr("stale-access"))
    await store.save(stale)
    fresh = make_tokens(expires_in=EXPIRES_IN, access_token=SecretStr("fresh-access"))
    fake_keyring.values[_SERVICE, _ENTRY] = fresh.model_dump_json()
    result = await service.access_tokens()
    assert result == fresh
    assert client.session_calls == 0
    assert client.stub_session.refresh_calls == []


@pytest.mark.asyncio
async def test_tokens_written_by_another_process_are_picked_up_by_reload(
    service: OidcService, client: StubClient, fake_keyring: FakeKeyring, make_tokens
) -> None:
    fresh = make_tokens(expires_in=EXPIRES_IN)
    fake_keyring.values[_SERVICE, _ENTRY] = fresh.model_dump_json()
    result = await service.access_tokens()
    assert result == fresh
    assert client.session_calls == 0


@pytest.mark.asyncio
async def test_missing_stored_credentials_require_relogin(
    service: OidcService, client: StubClient
) -> None:
    with pytest.raises(OidcError, match="no stored credentials"):
        await service.access_tokens()
    assert client.session_calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "https://other.example"),
        ("client_id", "other-client"),
        ("audience", "other-audience"),
    ],
)
@pytest.mark.asyncio
async def test_changed_configuration_requires_relogin(
    service: OidcService,
    client: StubClient,
    store: OidcCredentialStore,
    make_tokens,
    field: str,
    value: str,
) -> None:
    stored = make_tokens(expires_in=EXPIRES_IN, **{field: value})
    await store.save(stored)
    with pytest.raises(OidcError, match="configuration changed"):
        await service.access_tokens()
    assert client.stub_session.refresh_calls == []
    assert await store.load() == stored


@pytest.mark.asyncio
async def test_expired_token_without_refresh_token_requires_relogin(
    service: OidcService, client: StubClient, store: OidcCredentialStore, make_tokens
) -> None:
    stored = make_tokens(expires_in=-60, refresh_token=None)
    await store.save(stored)
    with pytest.raises(OidcError, match="no refresh token"):
        await service.access_tokens()
    assert client.stub_session.refresh_calls == []
    assert await store.load() == stored


@pytest.mark.parametrize(
    "error,match",
    [
        ("invalid_grant", "re-login required"),
        ("temporarily_unavailable", "retry the probe later"),
    ],
)
@pytest.mark.asyncio
async def test_refresh_oauth_errors_are_sanitized_and_preserve_store(
    service: OidcService,
    client: StubClient,
    store: OidcCredentialStore,
    make_tokens,
    error: str,
    match: str,
) -> None:
    stored = make_tokens(expires_in=-60)
    await store.save(stored)
    client.stub_session.error = OAuthError(error, f"description leaks {REFRESH_TOKEN}")
    with pytest.raises(OidcError, match=match) as raised:
        await service.access_tokens()
    assert REFRESH_TOKEN not in str(raised.value)
    assert await store.load() == stored


@pytest.mark.asyncio
async def test_transient_refresh_failure_can_be_retried(
    service: OidcService, client: StubClient, store: OidcCredentialStore, make_tokens
) -> None:
    stored = make_tokens(expires_in=-60)
    await store.save(stored)
    client.stub_session.error = OAuthError("temporarily_unavailable", "try later")
    with pytest.raises(OidcError, match="retry the probe later"):
        await service.access_tokens()
    client.stub_session.error = None
    result = await service.access_tokens()
    assert result.access_token == SecretStr(REFRESHED_ACCESS_TOKEN)
    assert await store.load() == result


@pytest.mark.asyncio
async def test_non_oauth_refresh_failure_propagates_without_touching_store(
    service: OidcService, client: StubClient, store: OidcCredentialStore, make_tokens
) -> None:
    stored = make_tokens(expires_in=-60)
    await store.save(stored)
    request = httpx2.Request("POST", f"{ISSUER}/token")
    client.stub_session.error = httpx2.ReadTimeout("transient", request=request)
    with pytest.raises(httpx2.ReadTimeout):
        await service.access_tokens()
    assert await store.load() == stored


@pytest.mark.asyncio
async def test_concurrent_access_serializes_into_a_single_refresh(
    service: OidcService, client: StubClient, store: OidcCredentialStore, make_tokens
) -> None:
    stored = make_tokens(expires_in=-60)
    await store.save(stored)
    first, second = await asyncio.gather(
        service.access_tokens(), service.access_tokens()
    )
    assert first == second
    assert len(client.stub_session.refresh_calls) == 1
    assert first.access_token == SecretStr(REFRESHED_ACCESS_TOKEN)
    assert await store.load() == first


@pytest.mark.asyncio
async def test_refresh_conflict_with_concurrent_writer_requires_retry(
    service: OidcService,
    client: StubClient,
    store: OidcCredentialStore,
    fake_keyring: FakeKeyring,
    make_tokens,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = make_tokens(expires_in=-60)
    await store.save(stored)
    replacement = make_tokens(expires_in=EXPIRES_IN, access_token=SecretStr("other"))
    original = store.save_if_current

    async def conflicting_save(tokens: OidcTokens, *, expected: OidcTokens) -> bool:
        fake_keyring.values[_SERVICE, _ENTRY] = replacement.model_dump_json()
        return await original(tokens, expected=expected)

    monkeypatch.setattr(store, "save_if_current", conflicting_save)
    with pytest.raises(OidcError, match="changed during refresh"):
        await service.access_tokens()
    assert len(client.stub_session.refresh_calls) == 1
    assert await store.load() == replacement
