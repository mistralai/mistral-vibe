from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
from threading import Event
import time
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

from authlib.integrations.httpx_client import AsyncOAuth2Client
import httpx2
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey
import keyring
from keyring.errors import KeyringError
from pydantic import SecretStr, ValidationError
import pytest

from vibe.setup.auth import _oidc_client, _oidc_runtime, _oidc_service
from vibe.setup.auth._oidc_callback import OidcCallback
from vibe.setup.auth._oidc_client import OidcClient, TokenResponse
from vibe.setup.auth._oidc_config import OidcConfig, OidcError
from vibe.setup.auth._oidc_credentials import OidcCredentialStore, OidcTokens
from vibe.setup.auth._oidc_service import OidcAttempt, OidcService

ISSUER = "https://issuer.example"
CLIENT_ID = "vibe-public-cli"
AUDIENCE = "https://api.example"
SUBJECT = "subject-123"
ACCESS_TOKEN = "opaque-access-secret"
REFRESH_TOKEN = "opaque-refresh-secret"


class FakeStore(OidcCredentialStore):
    def __init__(self, raw: str | None = None) -> None:
        self.raw = raw
        self.saved: list[OidcTokens] = []
        self.clears = 0

    def _load(self) -> OidcTokens | None:
        return (
            OidcTokens.model_validate_json(self.raw) if self.raw is not None else None
        )

    def _save(self, tokens: OidcTokens) -> None:
        self.raw = tokens.model_dump_json()
        self.saved.append(tokens)

    def _clear(self) -> None:
        self.raw = None
        self.clears += 1

    async def expire(self) -> OidcTokens:
        tokens = await self.load()
        assert tokens is not None
        expired = tokens.model_copy(update={"expires_at": time.time() - 60})
        self.raw = expired.model_dump_json()
        return expired


class FakeCallback(OidcCallback):
    def __init__(self, redirect_uri: str, state: str) -> None:
        super().__init__(redirect_uri, state)
        self.closed = False

    async def __aenter__(self) -> FakeCallback:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True
        if not self.result.done():
            self.result.cancel()


@dataclass
class FakeIssuer:
    key: RSAKey
    metadata: dict[str, Any] = field(
        default_factory=lambda: {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
            "userinfo_endpoint": f"{ISSUER}/userinfo",
            "id_token_signing_alg_values_supported": ["RS256"],
        }
    )
    requests: list[httpx2.Request] = field(default_factory=list)
    authorization: dict[str, list[str]] = field(default_factory=dict)
    initial_response: dict[str, Any] | None = None
    refresh_response: dict[str, Any] = field(
        default_factory=lambda: {
            "access_token": "refreshed-access-secret",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
    )
    refresh_status: int = 200
    refresh_error: Callable[[httpx2.Request], Exception] | None = None
    userinfo: dict[str, Any] = field(default_factory=lambda: {"sub": SUBJECT})
    userinfo_status: int = 200

    def sign(self, **overrides: Any) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": SUBJECT,
            "iat": now,
            "exp": now + 3600,
            "nonce": self.authorization.get("nonce", ["expected-nonce"])[0],
            **overrides,
        }
        return jwt.encode({"alg": "RS256", "kid": "issuer-key"}, claims, self.key)

    def token_response(self) -> dict[str, Any]:
        return {
            "access_token": ACCESS_TOKEN,
            "refresh_token": REFRESH_TOKEN,
            "id_token": self.sign(),
            "token_type": "Bearer",
            "expires_in": 3600,
        }

    def reply(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        assert request.url.host == "issuer.example", "Unexpected external request"
        if request.url.path == "/.well-known/openid-configuration":
            assert "authorization" not in request.headers
            return httpx2.Response(200, json=self.metadata)
        if request.url.path == "/jwks":
            assert "authorization" not in request.headers
            return httpx2.Response(200, json={"keys": [self.key.as_dict()]})
        if request.url.path == "/token":
            assert request.method == "POST"
            assert "authorization" not in request.headers
            assert request.headers["content-type"].startswith(
                "application/x-www-form-urlencoded"
            )
            form = parse_qs(request.content.decode())
            assert form["client_id"] == [CLIENT_ID]
            assert "client_secret" not in form
            if form["grant_type"] == ["authorization_code"]:
                assert form["code"] == ["authorization-code-secret"]
                assert form["redirect_uri"] == [self.authorization["redirect_uri"][0]]
                verifier = form["code_verifier"][0]
                assert 43 <= len(verifier) <= 128
                challenge = (
                    base64
                    .urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                    .rstrip(b"=")
                    .decode()
                )
                assert self.authorization["code_challenge"] == [challenge]
                assert verifier not in urlencode(self.authorization, doseq=True)
                payload = self.initial_response
                return httpx2.Response(
                    200,
                    json=(payload if payload is not None else self.token_response()),
                )
            assert form["grant_type"] == ["refresh_token"]
            assert form["refresh_token"] == [REFRESH_TOKEN]
            if self.refresh_error is not None:
                raise self.refresh_error(request)
            return httpx2.Response(self.refresh_status, json=self.refresh_response)
        if request.url.path == "/userinfo":
            return httpx2.Response(self.userinfo_status, json=self.userinfo)
        raise AssertionError(f"Unexpected issuer endpoint: {request.url.path}")

    def count(self, path: str) -> int:
        return sum(request.url.path == path for request in self.requests)


@dataclass
class Flow:
    client: OidcClient
    issuer: FakeIssuer
    store: FakeStore
    service: OidcService
    callbacks: list[FakeCallback]
    sessions: list[httpx2.AsyncClient]
    platform_transports: list[httpx2.AsyncBaseTransport | None] = field(
        default_factory=list
    )

    async def start(self) -> tuple[OidcAttempt, FakeCallback]:
        attempt = await self.service.start()
        async with asyncio.timeout(2):
            while self.service.poll(attempt.attempt_id).authorization_url is None:
                assert attempt.status == "waiting", attempt.detail
                await asyncio.sleep(0)
        assert attempt.authorization_url is not None
        self.issuer.authorization = parse_qs(urlsplit(attempt.authorization_url).query)
        return attempt, self.callbacks[-1]

    async def login(self) -> OidcAttempt:
        attempt, callback = await self.start()
        callback.result.set_result(
            f"{callback.redirect_uri}?"
            + urlencode({"code": "authorization-code-secret", "state": callback.state})
        )
        assert attempt.task is not None
        await asyncio.wait_for(attempt.task, 2)
        return attempt


@pytest.fixture(autouse=True)
def isolated_oidc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("VIBE_CLI_OIDC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("VIBE_CLI_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("VIBE_CLI_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("VIBE_CLI_OIDC_REDIRECT_URI", "http://127.0.0.1:8766/callback")

    def no_keyring(*args: object, **kwargs: object) -> None:
        pytest.fail("OIDC flow tests must use the fake store, not keyring")

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, no_keyring)
    original_open = Path.open

    def no_dotenv(self: Path, *args: Any, **kwargs: Any):
        assert self.name != ".env", "OIDC must not read a .env file"
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_dotenv)

    async def no_network(*args: object, **kwargs: object) -> httpx2.Response:
        pytest.fail("OIDC tests must not use a real HTTP transport")

    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", no_network)


@pytest.fixture(scope="module")
def rsa_key() -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": "issuer-key"})


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch, rsa_key: RSAKey) -> Flow:
    issuer = FakeIssuer(rsa_key)
    callbacks: list[FakeCallback] = []
    sessions: list[httpx2.AsyncClient] = []

    def make_session(**kwargs: Any) -> AsyncOAuth2Client:
        session = AsyncOAuth2Client(
            transport=httpx2.MockTransport(issuer.reply), **kwargs
        )
        sessions.append(cast(httpx2.AsyncClient, session))
        return session

    def make_callback(redirect_uri: str, state: str) -> FakeCallback:
        callback = FakeCallback(redirect_uri, state)
        callbacks.append(callback)
        return callback

    platform_client = _oidc_service.MistralPlatformHTTPClient
    platform_transports: list[httpx2.AsyncBaseTransport | None] = []

    def make_platform_client(**kwargs: Any):
        platform_transports.append(kwargs.pop("transport", None))
        return platform_client(transport=httpx2.MockTransport(issuer.reply), **kwargs)

    monkeypatch.setattr(
        _oidc_service, "MistralPlatformHTTPClient", make_platform_client
    )
    monkeypatch.setattr(_oidc_client, "AsyncOAuth2Client", make_session)
    monkeypatch.setattr(_oidc_service, "OidcCallback", make_callback)
    client = OidcClient(OidcConfig.from_env())
    store = FakeStore()
    return Flow(
        client,
        issuer,
        store,
        OidcService(client, store),
        callbacks,
        sessions,
        platform_transports,
    )


@pytest.mark.parametrize("issuer", [ISSUER, f"{ISSUER}/", f"{ISSUER}/realm/"])
def test_config_from_env_preserves_exact_issuer_identity(monkeypatch, issuer) -> None:
    monkeypatch.setenv("VIBE_CLI_OIDC_ISSUER", issuer)
    config = OidcConfig.from_env()
    assert (config.issuer, config.client_id, config.audience) == (
        issuer,
        CLIENT_ID,
        AUDIENCE,
    )
    # Albe OIDC identity verification requires email (and sid), while profile
    # supplies the optional display-name claims: services/auth/services/oidc_id_token.py.
    assert set(config.scope.split()) == {"openid", "offline_access", "email", "profile"}
    assert config.redirect_uri == "http://127.0.0.1:8766/callback"


@pytest.mark.parametrize("enabled", ["", "false", "0", "yes"])
def test_config_requires_experiment_flag(monkeypatch, enabled) -> None:
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", enabled)
    with pytest.raises(OidcError, match="disabled"):
        OidcConfig.from_env()


@pytest.mark.parametrize(
    "redirect",
    [
        "http://0.0.0.0:8766/callback",
        "http://localhost:8766/callback",
        "https://127.0.0.1:8766/callback",
        "http://127.0.0.1/callback",
        "http://127.0.0.1:0/callback",
        "http://127.0.0.1:99999/callback",
        "http://user:password@127.0.0.1:8766/callback",
        "http://127.0.0.1:8766/callback?other=1",
        "http://127.0.0.1:8766/callback#fragment",
    ],
)
def test_config_rejects_unbounded_or_unsafe_redirect(monkeypatch, redirect) -> None:
    monkeypatch.setenv("VIBE_CLI_OIDC_REDIRECT_URI", redirect)
    with pytest.raises(OidcError):
        OidcConfig.from_env()


@pytest.mark.parametrize(
    "name,value",
    [
        ("VIBE_CLI_OIDC_ISSUER", "http://issuer.example"),
        ("VIBE_CLI_OIDC_ISSUER", "https://user:secret@issuer.example"),
        ("VIBE_CLI_OIDC_CLIENT_ID", ""),
        ("VIBE_CLI_OIDC_SCOPE", "profile email"),
    ],
)
def test_config_rejects_invalid_issuer_client_or_scope(
    monkeypatch, name, value
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(OidcError):
        OidcConfig.from_env()


@pytest.mark.asyncio
async def test_start_returns_attempt_before_allocating_resources(flow: Flow) -> None:
    attempt = await flow.service.start()
    assert attempt.attempt_id
    assert flow.service.poll(attempt.attempt_id) is attempt
    assert attempt.status == "waiting"
    assert attempt.authorization_url is None
    assert attempt.detail is None
    assert flow.sessions == []
    assert flow.callbacks == []
    assert flow.issuer.requests == []
    await asyncio.wait_for(flow.service.cancel(attempt.attempt_id), 0.5)
    assert attempt.task is not None
    assert attempt.task.cancelled()
    assert flow.sessions == []
    assert flow.callbacks == []
    assert flow.store.saved == []


@pytest.mark.parametrize("failure", ["session", "discovery", "callback", "url"])
@pytest.mark.asyncio
async def test_initialization_failure_is_polled_and_closes_resources(
    flow: Flow, monkeypatch, failure: str
) -> None:
    def fail_session():
        raise RuntimeError(ACCESS_TOKEN)

    async def fail_discovery(session):
        raise RuntimeError(ACCESS_TOKEN)

    async def fail_callback(self):
        raise RuntimeError(ACCESS_TOKEN)

    def fail_url(self, *args, **kwargs):
        raise RuntimeError(ACCESS_TOKEN)

    if failure == "session":
        monkeypatch.setattr(flow.client, "session", fail_session)
    elif failure == "discovery":
        monkeypatch.setattr(flow.client, "discover", fail_discovery)
    elif failure == "callback":
        monkeypatch.setattr(FakeCallback, "__aenter__", fail_callback)
    else:
        monkeypatch.setattr(AsyncOAuth2Client, "create_authorization_url", fail_url)
    attempt = await flow.service.start()
    assert attempt.status == "waiting"
    assert attempt.task is not None
    await asyncio.wait_for(attempt.task, 1)
    assert flow.service.poll(attempt.attempt_id).status == "failed"
    assert attempt.authorization_url is None
    assert attempt.detail is not None
    assert ACCESS_TOKEN not in attempt.detail
    assert all(session.is_closed for session in flow.sessions)
    assert all(callback.closed for callback in flow.callbacks)
    assert all(callback.result.cancelled() for callback in flow.callbacks)
    assert flow.store.saved == []
    assert flow.issuer.count("/token") == 0


@pytest.mark.asyncio
async def test_whole_attempt_timeout_includes_discovery(
    flow: Flow, monkeypatch
) -> None:
    discovering = asyncio.Event()
    release = asyncio.Event()
    timeout = asyncio.timeout

    def short_timeout(delay):
        assert delay == 300
        return timeout(0.01)

    async def blocked_discovery(session):
        discovering.set()
        await release.wait()
        pytest.fail("Timed-out discovery was resumed")

    monkeypatch.setattr(_oidc_service.asyncio, "timeout", short_timeout)
    monkeypatch.setattr(flow.client, "discover", blocked_discovery)
    attempt = await flow.service.start()
    assert attempt.task is not None
    await asyncio.wait_for(discovering.wait(), 1)
    await asyncio.wait_for(attempt.task, 1)
    assert flow.service.poll(attempt.attempt_id).status == "failed"
    assert attempt.authorization_url is None
    assert flow.callbacks == []
    assert all(session.is_closed for session in flow.sessions)
    assert flow.store.saved == []


@pytest.mark.asyncio
async def test_repeated_start_cancels_previous_callback_before_replacement(
    flow: Flow,
) -> None:
    first, callback = await flow.start()
    second = await flow.service.start()
    try:
        assert second.attempt_id != first.attempt_id
        assert flow.service.poll(second.attempt_id) is second
        assert first.task is not None
        assert first.task.cancelled()
        assert callback.closed
        assert callback.result.cancelled()
        assert flow.sessions[0].is_closed
        with pytest.raises(OidcError, match="Unknown"):
            flow.service.poll(first.attempt_id)
        await flow.service.cancel(first.attempt_id)
        assert flow.service.poll(second.attempt_id) is second
    finally:
        await flow.service.cancel()
    assert all(session.is_closed for session in flow.sessions)
    assert all(callback.closed for callback in flow.callbacks)
    assert flow.store.saved == []


@pytest.mark.asyncio
async def test_pkce_login_persists_and_restart_reuses_bundle(flow: Flow) -> None:
    attempt = await flow.login()
    assert attempt.status == "completed", attempt.detail
    assert flow.service.poll(attempt.attempt_id) is attempt
    query = flow.issuer.authorization
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [CLIENT_ID]
    assert query["audience"] == [AUDIENCE]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["nonce"][0]) >= 32
    assert len(query["state"][0]) >= 32
    assert query["nonce"] != query["state"]
    assert query["state"] == [flow.callbacks[0].state]
    tokens = await flow.store.load()
    assert tokens is not None
    assert (tokens.issuer, tokens.subject, tokens.client_id, tokens.audience) == (
        ISSUER,
        SUBJECT,
        CLIENT_ID,
        AUDIENCE,
    )
    assert tokens.access_token.get_secret_value() == ACCESS_TOKEN
    assert tokens.refresh_token == SecretStr(REFRESH_TOKEN)
    assert tokens.expires_at > time.time() + 3500
    assert tokens.id_token_expires_at is not None
    assert tokens.id_token_expires_at > time.time() + 3500
    assert len(flow.store.saved) == 1
    assert flow.callbacks[0].closed
    assert all(session.is_closed for session in flow.sessions)
    assert all(
        not session.follow_redirects and not session.trust_env
        for session in flow.sessions
    )
    assert flow.sessions[0].timeout.read == 20
    request_count = len(flow.issuer.requests)
    restart_store = FakeStore(flow.store.raw)
    restarted = OidcService(OidcClient(flow.client.config), restart_store)
    assert await restarted.access_tokens() == tokens
    assert len(flow.issuer.requests) == request_count
    assert restart_store.saved == []
    for value in (tokens, TokenResponse.model_validate(flow.issuer.token_response())):
        for secret in (ACCESS_TOKEN, REFRESH_TOKEN, tokens.id_token.get_secret_value()):
            assert secret not in repr(value)
            assert secret not in str(value)


@pytest.mark.asyncio
async def test_state_mismatch_is_rejected_before_token_exchange(flow: Flow) -> None:
    attempt, callback = await flow.start()
    callback.result.set_result(
        f"{callback.redirect_uri}?code=authorization-code-secret&state=wrong"
    )
    assert attempt.task is not None
    await asyncio.wait_for(attempt.task, 2)
    assert attempt.status == "failed"
    assert flow.issuer.count("/token") == 0
    assert flow.store.saved == []
    assert callback.closed
    assert "authorization-code-secret" not in (attempt.detail or "")


@pytest.mark.asyncio
async def test_cancel_closes_callback_and_session_without_persistence(
    flow: Flow,
) -> None:
    attempt, callback = await flow.start()
    await asyncio.sleep(0)
    assert not callback.result.done()
    await asyncio.wait_for(flow.service.cancel(attempt.attempt_id), 0.5)
    assert callback.result.cancelled()
    assert callback.closed
    assert all(session.is_closed for session in flow.sessions)
    assert flow.store.saved == []
    with pytest.raises(OidcError, match="Unknown"):
        flow.service.poll(attempt.attempt_id)


@pytest.mark.parametrize(
    "defect",
    [
        "wrong-signature",
        "missing-signature",
        "missing-nonce",
        "wrong-nonce",
        "missing-iss",
        "wrong-iss",
        "missing-aud",
        "wrong-aud",
        "missing-sub",
        "expired",
        "future-iat",
        "multiple-aud-without-azp",
        "wrong-azp",
        "wrong-at-hash",
    ],
)
@pytest.mark.asyncio
async def test_signed_id_token_checks_are_independent(flow: Flow, defect: str) -> None:
    claims = jwt.decode(flow.issuer.sign(), flow.issuer.key).claims
    if defect.startswith("missing-") and defect != "missing-signature":
        del claims[defect.removeprefix("missing-")]
    elif defect == "wrong-nonce":
        claims["nonce"] = "replayed-nonce"
    elif defect == "wrong-iss":
        claims["iss"] = "https://other-issuer.example"
    elif defect == "wrong-aud":
        claims["aud"] = AUDIENCE
    elif defect == "expired":
        claims["exp"] = int(time.time()) - 120
    elif defect == "future-iat":
        claims["iat"] = int(time.time()) + 120
    elif defect == "multiple-aud-without-azp":
        claims["aud"] = [CLIENT_ID, "other-client"]
    elif defect == "wrong-azp":
        claims["azp"] = "other-client"
    elif defect == "wrong-at-hash":
        claims["at_hash"] = "not-the-access-token-hash"
    signing_key = (
        RSAKey.generate_key(2048) if defect == "wrong-signature" else flow.issuer.key
    )
    signed = jwt.encode({"alg": "RS256", "kid": "issuer-key"}, claims, signing_key)
    if defect == "missing-signature":
        signed = signed.rsplit(".", 1)[0] + "."
    raw = {**flow.issuer.token_response(), "id_token": signed}
    session = flow.client.session()
    async with cast(httpx2.AsyncClient, session):
        metadata = await flow.client.discover(session)
        with pytest.raises(JoseError):
            await flow.client.validated_tokens(
                session, metadata, raw, nonce="expected-nonce"
            )
    assert flow.store.saved == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("access_token", ""),
        ("token_type", "MAC"),
        ("expires_in", 0),
        ("expires_in", -1),
        ("expires_in", float("nan")),
        ("expires_in", float("inf")),
        ("id_token", None),
    ],
)
@pytest.mark.asyncio
async def test_token_response_constraints_are_not_inferred_from_id_token(
    flow, field, value
) -> None:
    raw = {**flow.issuer.token_response(), field: value}
    session = flow.client.session()
    async with cast(httpx2.AsyncClient, session):
        metadata = await flow.client.discover(session)
        with pytest.raises(OidcError):
            await flow.client.validated_tokens(
                session, metadata, raw, nonce="expected-nonce"
            )
    assert flow.issuer.count("/jwks") == 0


@pytest.mark.parametrize("field", ["access_token", "token_type", "expires_in"])
@pytest.mark.asyncio
async def test_required_token_response_fields(flow, field) -> None:
    raw = flow.issuer.token_response()
    del raw[field]
    session = flow.client.session()
    async with cast(httpx2.AsyncClient, session):
        metadata = await flow.client.discover(session)
        with pytest.raises(ValidationError):
            await flow.client.validated_tokens(
                session, metadata, raw, nonce="expected-nonce"
            )
    assert flow.issuer.count("/jwks") == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "https://other.example"),
        ("token_endpoint", "http://insecure.example/token"),
        ("jwks_uri", "https://user:password@issuer.example/jwks"),
        ("userinfo_endpoint", "https://other.example/userinfo"),
        ("userinfo_endpoint", "https://issuer.example:8443/userinfo"),
    ],
)
@pytest.mark.asyncio
async def test_discovery_rejects_issuer_and_endpoint_substitution(
    flow, field, value
) -> None:
    flow.issuer.metadata[field] = value
    session = flow.client.session()
    async with cast(httpx2.AsyncClient, session):
        with pytest.raises(OidcError):
            await flow.client.discover(session)
    assert len(flow.issuer.requests) == 1


@pytest.mark.parametrize("advertised", [["none"], ["HS256"], []])
@pytest.mark.asyncio
async def test_no_unsigned_or_symmetric_id_token_fallback(flow, advertised) -> None:
    flow.issuer.metadata["id_token_signing_alg_values_supported"] = advertised
    session = flow.client.session()
    async with cast(httpx2.AsyncClient, session):
        metadata = await flow.client.discover(session)
        with pytest.raises(OidcError, match="no supported"):
            await flow.client.validated_tokens(
                session, metadata, flow.issuer.token_response(), nonce="expected-nonce"
            )


@pytest.mark.parametrize("operation", ["access_tokens", "probe"])
@pytest.mark.parametrize("cancel_count", [1, 2])
@pytest.mark.asyncio
async def test_cancel_during_refresh_save_preserves_rotation_without_userinfo(
    flow: Flow, monkeypatch, operation, cancel_count
) -> None:
    assert (await flow.login()).status == "completed"
    previous = await flow.store.expire()
    flow.issuer.refresh_response["refresh_token"] = "rotated-refresh-secret"
    stored = {
        ("ai.mistral.vibe.oidc", "cli-oidc:v1:tokens"): previous.model_dump_json()
    }
    started = asyncio.Event()
    release = Event()
    loop = asyncio.get_running_loop()

    def blocked_write(service, username, password):
        loop.call_soon_threadsafe(started.set)
        release.wait()
        stored[service, username] = password

    monkeypatch.delenv("VIBE_TEST_DISABLE_KEYRING", raising=False)
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, username: stored.get((service, username)),
    )
    monkeypatch.setattr(keyring, "set_password", blocked_write)
    flow.service.store = OidcCredentialStore()
    task = asyncio.create_task(getattr(flow.service, operation)())
    try:
        await asyncio.wait_for(started.wait(), 2)
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        rotated = await flow.service.store.load()
        assert rotated is not None
        assert rotated.refresh_token == SecretStr("rotated-refresh-secret")
        assert rotated.access_token == SecretStr("refreshed-access-secret")
        assert flow.issuer.count("/userinfo") == 0
        assert not flow.service._lock.locked()
        assert all(session.is_closed for session in flow.sessions)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["clear", "login"])
async def test_runtime_refresh_cannot_overwrite_another_service_mutation(
    flow: Flow, monkeypatch, replacement
) -> None:
    assert (await flow.login()).status == "completed"
    previous = await flow.store.expire()
    stored = {
        ("ai.mistral.vibe.oidc", "cli-oidc:v1:tokens"): previous.model_dump_json()
    }
    monkeypatch.delenv("VIBE_TEST_DISABLE_KEYRING", raising=False)
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, username: stored.get((service, username)),
    )
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, username, raw: stored.update({(service, username): raw}),
    )
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, username: stored.pop((service, username)),
    )
    flow.service.store = OidcCredentialStore()
    started = asyncio.Event()
    release = Event()
    loop = asyncio.get_running_loop()
    new_login = previous.model_copy(
        update={"subject": "other-account", "access_token": SecretStr("other-access")}
    )

    async def reply(request):
        if request.url.path == "/token":
            loop.call_soon_threadsafe(started.set)
            await asyncio.to_thread(release.wait)
        return flow.issuer.reply(request)

    def runtime_service():
        client = OidcClient(flow.client.config)
        client.session = lambda: AsyncOAuth2Client(
            client_id=CLIENT_ID,
            token_endpoint_auth_method="none",
            transport=httpx2.MockTransport(reply),
            follow_redirects=False,
        )
        return OidcService(client, OidcCredentialStore())

    monkeypatch.setattr(OidcService, "from_env", runtime_service)
    task = asyncio.create_task(asyncio.to_thread(_oidc_runtime._resolve_id_token))
    try:
        await asyncio.wait_for(started.wait(), 2)
        if replacement == "clear":
            await flow.service.clear()
        else:
            # Browser login persistence uses the setup service, independently of runtime.
            async with flow.service._lock:
                await flow.service.store.save(new_login, complete_on_cancel=True)
        release.set()
        with pytest.raises(OidcError, match="changed during refresh"):
            await asyncio.wait_for(task, 2)
        assert await flow.service.store.load() == (
            None if replacement == "clear" else new_login
        )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_force_refresh_renews_id_token_before_access_token_expires(flow) -> None:
    assert (await flow.login()).status == "completed"
    stored = await flow.store.load()
    assert stored is not None
    flow.issuer.refresh_response["id_token"] = flow.issuer.sign(nonce=stored.nonce)
    updated = await flow.service.access_tokens(force_refresh=True)
    assert updated.access_token == SecretStr("refreshed-access-secret")
    assert updated.id_token == SecretStr(flow.issuer.refresh_response["id_token"])
    assert flow.issuer.count("/token") == 2
    assert flow.issuer.count("/jwks") == 2


@pytest.mark.parametrize("rotated", [None, "rotated-refresh-secret"])
@pytest.mark.asyncio
async def test_refresh_on_access_rotates_or_preserves_refresh_token(
    flow, rotated
) -> None:
    assert (await flow.login()).status == "completed"
    previous = await flow.store.expire()
    if rotated is not None:
        flow.issuer.refresh_response["refresh_token"] = rotated
    updated, concurrent = await asyncio.gather(
        flow.service.access_tokens(), flow.service.access_tokens()
    )
    assert updated == concurrent
    assert updated.access_token == SecretStr("refreshed-access-secret")
    assert updated.refresh_token == SecretStr(rotated or REFRESH_TOKEN)
    assert updated.id_token == previous.id_token
    assert updated.id_token_expires_at == previous.id_token_expires_at
    assert updated.subject == previous.subject
    assert updated.expires_at > time.time() + 3500
    assert await flow.store.load() == updated
    assert len(flow.store.saved) == 2
    assert flow.issuer.count("/token") == 2
    assert flow.issuer.count("/jwks") == 1


@pytest.mark.asyncio
async def test_refresh_retains_narrowed_grant_without_requesting_scope(
    flow: Flow, monkeypatch
) -> None:
    config = OidcConfig(
        ISSUER,
        CLIENT_ID,
        AUDIENCE,
        flow.client.config.redirect_uri,
        scope="openid offline_access email",
    )
    flow.client = OidcClient(config)
    flow.service = OidcService(flow.client, flow.store)
    original_reply = flow.issuer.reply

    def enforce_granted_scope(request: httpx2.Request) -> httpx2.Response:
        body = (
            parse_qs(request.content.decode()) if request.url.path == "/token" else {}
        )
        if body.get("grant_type") == ["refresh_token"] and "email" in (
            body.get("scope", [""])[0].split()
        ):
            return httpx2.Response(400, json={"error": "invalid_scope"})
        response = original_reply(request)
        if body.get("grant_type") == ["authorization_code"]:
            return httpx2.Response(
                200, json={**response.json(), "scope": "openid offline_access"}
            )
        return response

    monkeypatch.setattr(flow.issuer, "reply", enforce_granted_scope)
    assert (await flow.login()).status == "completed"
    assert flow.issuer.authorization["scope"] == ["openid offline_access email"]
    await flow.store.expire()
    updated = await flow.service.access_tokens()
    assert updated.access_token == SecretStr("refreshed-access-secret")
    refresh = flow.issuer.requests[-1]
    # Authlib 1.9.1 OAuth2Client.refresh_token defaults scope to self.scope.
    assert "scope" not in parse_qs(refresh.content.decode())
    assert await flow.store.load() == updated


@pytest.mark.asyncio
async def test_refresh_rejects_subject_change_without_overwriting_store(
    flow: Flow,
) -> None:
    assert (await flow.login()).status == "completed"
    previous = await flow.store.expire()
    flow.issuer.refresh_response["id_token"] = flow.issuer.sign(sub="different-subject")
    with pytest.raises(OidcError, match="changed the authenticated identity"):
        await flow.service.access_tokens()
    assert await flow.store.load() == previous
    assert len(flow.store.saved) == 1


@pytest.mark.parametrize("valid_nonce", [False, True])
@pytest.mark.asyncio
async def test_refreshed_id_token_nonce_matches_initial_login_after_restart(
    flow: Flow, valid_nonce: bool
) -> None:
    assert (await flow.login()).status == "completed"
    previous = await flow.store.expire()
    original_nonce = flow.issuer.authorization["nonce"][0]
    assert previous.nonce == original_nonce
    signed = flow.issuer.sign(nonce=original_nonce if valid_nonce else "replayed-nonce")
    flow.issuer.refresh_response["id_token"] = signed
    restart_store = FakeStore(flow.store.raw)
    restarted = OidcService(OidcClient(flow.client.config), restart_store)
    if valid_nonce:
        updated = await restarted.access_tokens()
        assert updated.id_token == SecretStr(signed)
        assert updated.nonce == original_nonce
        assert updated.subject == SUBJECT
        assert await restart_store.load() == updated
        assert len(restart_store.saved) == 1
    else:
        with pytest.raises(JoseError, match="nonce"):
            await restarted.access_tokens()
        assert await restart_store.load() == previous
        assert restart_store.saved == []
    assert flow.issuer.count("/token") == 2
    assert flow.issuer.count("/jwks") == 2


@pytest.mark.parametrize(
    "error,status,message",
    [
        ("invalid_grant", 400, "re-login required"),
        ("temporarily_unavailable", 400, "retry"),
    ],
)
@pytest.mark.asyncio
async def test_refresh_oauth_errors_preserve_credentials_and_hide_description(
    flow, error, status, message
) -> None:
    assert (await flow.login()).status == "completed"
    previous = await flow.store.expire()
    flow.issuer.refresh_status = status
    flow.issuer.refresh_response = {"error": error, "error_description": REFRESH_TOKEN}
    with pytest.raises(OidcError, match=message) as raised:
        await flow.service.access_tokens()
    assert REFRESH_TOKEN not in str(raised.value)
    assert await flow.store.load() == previous
    assert len(flow.store.saved) == 1
    assert flow.store.clears == 0
    if error != "invalid_grant":
        flow.issuer.refresh_status = 200
        flow.issuer.refresh_response = {
            "access_token": "retry-access",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
        assert (await flow.service.access_tokens()).access_token == SecretStr(
            "retry-access"
        )


@pytest.mark.asyncio
async def test_refresh_network_failure_does_not_destroy_stored_credentials(
    flow: Flow,
) -> None:
    assert (await flow.login()).status == "completed"
    previous = await flow.store.expire()
    flow.issuer.refresh_error = lambda request: httpx2.ReadTimeout(
        "transient", request=request
    )
    with pytest.raises(httpx2.ReadTimeout):
        await flow.service.access_tokens()
    assert await flow.store.load() == previous
    assert len(flow.store.saved) == 1
    flow.issuer.refresh_error = None
    assert (await flow.service.access_tokens()).expires_at > time.time() + 3500


@pytest.mark.asyncio
async def test_refresh_http_503_preserves_credentials_and_can_retry(flow: Flow) -> None:
    assert (await flow.login()).status == "completed"
    previous = await flow.store.expire()
    flow.issuer.refresh_status = 503
    flow.issuer.refresh_response = {
        "error": "temporarily_unavailable",
        "error_description": REFRESH_TOKEN,
    }
    with pytest.raises(httpx2.HTTPStatusError) as raised:
        await flow.service.access_tokens()
    assert raised.value.response.status_code == 503
    assert REFRESH_TOKEN not in str(raised.value)
    assert await flow.store.load() == previous
    assert len(flow.store.saved) == 1
    assert flow.store.clears == 0
    flow.issuer.refresh_status = 200
    flow.issuer.refresh_response = {
        "access_token": "retry-access",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    assert (await flow.service.access_tokens()).access_token == SecretStr(
        "retry-access"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("issuer", "https://other.example"),
        ("client_id", "other-client"),
        ("audience", "other-audience"),
        ("refresh_token", None),
    ],
)
@pytest.mark.asyncio
async def test_restart_rejects_changed_configuration_or_missing_refresh(
    flow, field, value
) -> None:
    assert (await flow.login()).status == "completed"
    previous = await flow.store.expire()
    flow.store.raw = previous.model_copy(update={field: value}).model_dump_json()
    requests = len(flow.issuer.requests)
    with pytest.raises(OidcError, match="re-login required"):
        await OidcService(flow.client, flow.store).access_tokens()
    assert len(flow.issuer.requests) == requests
    assert len(flow.store.saved) == 1


@pytest.mark.asyncio
async def test_probe_uses_bearer_access_token_and_returns_only_verified_identity(
    flow: Flow,
) -> None:
    assert (await flow.login()).status == "completed"
    await flow.store.expire()
    assert await flow.service.probe() == {"issuer": ISSUER, "subject": SUBJECT}
    request = flow.issuer.requests[-1]
    assert request.url.path == "/userinfo"
    assert request.headers["authorization"] == "Bearer refreshed-access-secret"
    tokens = await flow.store.load()
    assert tokens is not None
    assert tokens.id_token.get_secret_value() not in request.headers["authorization"]
    assert REFRESH_TOKEN not in request.headers["authorization"]
    assert not request.url.query


@pytest.mark.asyncio
async def test_probe_uses_direct_tls_transport(flow: Flow) -> None:
    from vibe.utils import http

    assert (await flow.login()).status == "completed"
    await flow.service.probe()
    [transport] = flow.platform_transports
    assert type(transport) is httpx2.AsyncHTTPTransport
    assert transport._pool._ssl_context is http.build_ssl_context()


@pytest.mark.parametrize("identity", [{"sub": "other-subject"}, {}, {"sub": 123}])
@pytest.mark.asyncio
async def test_probe_rejects_userinfo_subject_mismatch(flow, identity) -> None:
    assert (await flow.login()).status == "completed"
    flow.issuer.userinfo = identity
    with pytest.raises(OidcError, match="subject mismatch"):
        await flow.service.probe()
    assert len(flow.store.saved) == 1


@pytest.mark.asyncio
async def test_probe_unauthorized_requires_login_without_exposing_tokens(
    flow: Flow,
) -> None:
    assert (await flow.login()).status == "completed"
    flow.issuer.userinfo_status = 401
    with pytest.raises(OidcError, match="re-login required") as raised:
        await flow.service.probe()
    assert ACCESS_TOKEN not in str(raised.value)
    assert len(flow.store.saved) == 1


@pytest.mark.asyncio
async def test_failed_login_never_persists_or_exposes_token_payload(flow: Flow) -> None:
    flow.issuer.initial_response = {
        "access_token": ACCESS_TOKEN,
        "refresh_token": REFRESH_TOKEN,
    }
    attempt = await flow.login()
    assert attempt.status == "failed"
    assert attempt.detail is not None
    assert ACCESS_TOKEN not in attempt.detail
    assert REFRESH_TOKEN not in attempt.detail
    assert flow.store.saved == []
    assert flow.callbacks[-1].closed
    assert all(session.is_closed for session in flow.sessions)


@pytest.mark.asyncio
async def test_declined_authorization_preserves_safe_detail(flow: Flow) -> None:
    attempt, callback = await flow.start()
    callback.result.set_exception(OidcError("OIDC authorization was declined"))
    assert attempt.task is not None
    await asyncio.wait_for(attempt.task, 2)
    assert attempt.status == "failed"
    assert attempt.detail == "OIDC authorization was declined"
    assert attempt.error_type == "oidc_error"
    assert flow.store.saved == []
    assert flow.issuer.count("/token") == 0


@pytest.mark.parametrize("write_fails", [False, True])
@pytest.mark.parametrize("cancel_count", [1, 2])
@pytest.mark.asyncio
async def test_cancel_during_login_save_reports_storage_outcome(
    flow: Flow, monkeypatch, write_fails, cancel_count
) -> None:
    started = asyncio.Event()
    release = Event()
    loop = asyncio.get_running_loop()
    stored = {}
    outcomes = []

    def blocked_write(service, username, password):
        loop.call_soon_threadsafe(started.set)
        release.wait()
        if write_fails:
            raise KeyringError(ACCESS_TOKEN)
        stored[service, username] = password

    monkeypatch.delenv("VIBE_TEST_DISABLE_KEYRING", raising=False)
    monkeypatch.setattr(keyring, "set_password", blocked_write)
    flow.service.store = OidcCredentialStore()
    flow.service.on_attempt_done = lambda attempt, status: outcomes.append(status)
    attempt, callback = await flow.start()
    callback.result.set_result(
        f"{callback.redirect_uri}?"
        + urlencode({"code": "authorization-code-secret", "state": callback.state})
    )
    assert attempt.task is not None
    cancel_task = None
    try:
        await asyncio.wait_for(started.wait(), 2)
        cancel_task = asyncio.create_task(flow.service.cancel(attempt.attempt_id))
        await asyncio.sleep(0)
        for _ in range(cancel_count - 1):
            attempt.task.cancel()
            await asyncio.sleep(0)
        assert not cancel_task.done()
        assert stored == {}
        release.set()
        await asyncio.wait_for(cancel_task, 2)
        assert not attempt.task.cancelled()
        assert attempt.status == ("failed" if write_fails else "completed")
        assert outcomes == (["error"] if write_fails else ["success"])
        assert bool(stored) is not write_fails
        if write_fails:
            assert attempt.error_type == "storage_error"
            assert ACCESS_TOKEN not in (attempt.detail or "")
        assert callback.closed
        assert all(session.is_closed for session in flow.sessions)
    finally:
        release.set()
        if cancel_task is not None:
            await asyncio.gather(cancel_task, return_exceptions=True)
        await flow.service.cancel()


@pytest.mark.asyncio
async def test_store_failure_never_marks_login_completed(
    flow: Flow, monkeypatch
) -> None:
    async def failing_save(
        tokens: OidcTokens, *, complete_on_cancel: bool = False
    ) -> None:
        raise RuntimeError(f"backend failed: {tokens.access_token.get_secret_value()}")

    monkeypatch.setattr(flow.store, "save", failing_save)
    attempt = await flow.login()
    assert flow.issuer.count("/token") == 1
    assert flow.issuer.count("/jwks") == 1
    assert attempt.status == "failed"
    assert attempt.detail is not None
    assert ACCESS_TOKEN not in attempt.detail
    assert await flow.store.load() is None
    assert flow.store.saved == []
    assert flow.callbacks[-1].closed
    assert all(session.is_closed for session in flow.sessions)


@pytest.mark.asyncio
async def test_probe_off_origin_userinfo_never_receives_access_token(
    flow: Flow,
) -> None:
    assert (await flow.login()).status == "completed"
    flow.issuer.metadata["userinfo_endpoint"] = "https://attacker.example/userinfo"
    with pytest.raises(OidcError, match="issuer origin"):
        await flow.service.probe()
    assert flow.issuer.count("/userinfo") == 0
    assert all(request.url.host == "issuer.example" for request in flow.issuer.requests)


@pytest.mark.asyncio
async def test_clear_waits_for_inflight_refresh_and_prevents_resave(
    flow: Flow, monkeypatch
) -> None:
    assert (await flow.login()).status == "completed"
    await flow.store.expire()
    refreshing = asyncio.Event()
    release = asyncio.Event()
    clearing = asyncio.Event()
    operations = []
    original_reply = flow.issuer.reply
    original_save = flow.store._save
    original_clear = flow.store.clear
    original_service_clear = flow.service.clear

    async def blocked_reply(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/token":
            refreshing.set()
            await release.wait()
        return original_reply(request)

    def record_save(tokens: OidcTokens) -> None:
        operations.append("save")
        original_save(tokens)

    async def record_clear() -> None:
        operations.append("clear")
        await original_clear()

    async def record_service_clear() -> None:
        clearing.set()
        await original_service_clear()

    monkeypatch.setattr(flow.issuer, "reply", blocked_reply)
    monkeypatch.setattr(flow.store, "_save", record_save)
    monkeypatch.setattr(flow.store, "clear", record_clear)
    monkeypatch.setattr(flow.service, "clear", record_service_clear)
    refresh_task = asyncio.create_task(flow.service.access_tokens())
    clear_task = None
    try:
        await asyncio.wait_for(refreshing.wait(), 1)
        clear_task = asyncio.create_task(flow.service.clear())
        await asyncio.wait_for(clearing.wait(), 1)
        assert not clear_task.done()
        assert flow.store.clears == 0
        release.set()
        updated, _ = await asyncio.wait_for(asyncio.gather(refresh_task, clear_task), 1)
        assert updated.access_token == SecretStr("refreshed-access-secret")
        assert operations == ["save", "clear"]
        assert await flow.store.load() is None
        assert flow.store.clears == 1
        requests = len(flow.issuer.requests)
        with pytest.raises(OidcError, match="no stored credentials"):
            await flow.service.access_tokens()
        await asyncio.sleep(0)
        assert len(flow.issuer.requests) == requests
        assert operations == ["save", "clear"]
        assert await flow.store.load() is None
    finally:
        release.set()
        tasks = [refresh_task, *([clear_task] if clear_task is not None else [])]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_clear_cancels_pending_login_before_clearing_store(flow: Flow) -> None:
    attempt, callback = await flow.start()
    await asyncio.sleep(0)
    await asyncio.wait_for(flow.service.clear(), 0.5)
    assert callback.closed
    assert callback.result.cancelled()
    assert all(session.is_closed for session in flow.sessions)
    assert await flow.store.load() is None
    assert flow.store.clears == 1
    assert flow.store.saved == []
    with pytest.raises(OidcError, match="Unknown"):
        flow.service.poll(attempt.attempt_id)


@pytest.mark.parametrize("action", ["cancel", "clear", "restart"])
@pytest.mark.asyncio
async def test_blocked_discovery_is_owned_and_cancelled_without_callback_or_save(
    flow: Flow, monkeypatch, action: str
) -> None:
    discovering = asyncio.Event()
    release = asyncio.Event()
    original_reply = flow.issuer.reply

    async def blocked_reply(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/.well-known/openid-configuration":
            discovering.set()
            await release.wait()
        return original_reply(request)

    monkeypatch.setattr(flow.issuer, "reply", blocked_reply)
    attempt = await flow.service.start()
    try:
        await asyncio.wait_for(discovering.wait(), 1)
        assert flow.service.poll(attempt.attempt_id).authorization_url is None
        assert attempt.status == "waiting"
        assert flow.callbacks == []
        assert len(flow.sessions) == 1
        assert not flow.sessions[0].is_closed
        if action == "clear":
            await asyncio.wait_for(flow.service.clear(), 0.5)
            assert flow.store.clears == 1
        elif action == "restart":
            replacement = await asyncio.wait_for(flow.service.start(), 0.5)
            assert replacement.attempt_id != attempt.attempt_id
            assert flow.service.poll(replacement.attempt_id) is replacement
            await flow.service.cancel(attempt.attempt_id)
            assert flow.service.poll(replacement.attempt_id) is replacement
        else:
            await asyncio.wait_for(flow.service.cancel(attempt.attempt_id), 0.5)
        assert not release.is_set()
        assert attempt.task is not None
        assert attempt.task.cancelled()
        assert flow.sessions[0].is_closed
        assert flow.callbacks == []
        assert await flow.store.load() is None
        assert flow.store.saved == []
        assert flow.issuer.count("/token") == 0
        with pytest.raises(OidcError, match="Unknown"):
            flow.service.poll(attempt.attempt_id)
    finally:
        await flow.service.cancel()
        release.set()
    assert all(session.is_closed for session in flow.sessions)
    assert flow.callbacks == []
    assert flow.store.saved == []


@pytest.mark.parametrize("first", ["start", "clear"])
@pytest.mark.asyncio
async def test_concurrent_start_and_clear_follow_lock_order(
    flow: Flow, first: str
) -> None:
    await flow.service._lock.acquire()
    operations = (
        (flow.service.start(), flow.service.clear())
        if first == "start"
        else (flow.service.clear(), flow.service.start())
    )
    tasks = [asyncio.create_task(operation) for operation in operations]
    try:
        await asyncio.sleep(0)
        flow.service._lock.release()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 1)
        attempt = results[0] if first == "start" else results[1]
        assert attempt is not None
        assert flow.store.clears == 1
        if first == "start":
            with pytest.raises(OidcError, match="Unknown"):
                flow.service.poll(attempt.attempt_id)
            assert attempt.task is not None
            assert attempt.task.cancelled()
        else:
            assert flow.service.poll(attempt.attempt_id) is attempt
    finally:
        if flow.service._lock.locked():
            flow.service._lock.release()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await flow.service.cancel()
    assert all(session.is_closed for session in flow.sessions)
    assert all(callback.closed for callback in flow.callbacks)
    assert flow.store.saved == []
