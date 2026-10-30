from __future__ import annotations

import asyncio
import json
import time
from typing import cast
from urllib.parse import parse_qs, urlsplit

from authlib.integrations.httpx_client import AsyncOAuth2Client
import httpx2
from joserfc import jwt
from joserfc.jwk import RSAKey
import pytest

from tests.conftest import build_test_vibe_config
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._setup_oidc import OidcStartResponse, SetupOidcHandler
from vibe.app_server.protocol import ClientInfo
from vibe.core.telemetry.send import TelemetryClient
from vibe.setup.auth import _oidc_client, _oidc_service
from vibe.setup.auth._oidc_client import OidcClient
from vibe.setup.auth._oidc_config import OidcConfig, OidcError
from vibe.setup.auth._oidc_credentials import OidcCredentialStore, OidcTokens
from vibe.setup.auth._oidc_service import OidcService
from vibe.utils.http import VibeAsyncHTTPClient

_SEND_EVENT = TelemetryClient.send_telemetry_event
_ISSUER = "https://auth.mistral.ai"


class FakeStore(OidcCredentialStore):
    def __init__(self) -> None:
        self.tokens: OidcTokens | None = None

    async def load(self) -> OidcTokens | None:
        return self.tokens

    async def save(
        self, tokens: OidcTokens, *, complete_on_cancel: bool = False
    ) -> None:
        self.tokens = tokens

    async def clear(self) -> None:
        self.tokens = None


class FakeCallback:
    def __init__(self, redirect_uri: str, state: str) -> None:
        self.redirect_uri = redirect_uri
        self.state = state
        self.result = asyncio.get_running_loop().create_future()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if not self.result.done():
            self.result.cancel()


@pytest.fixture
def oidc_analytics(monkeypatch):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "synthetic-org")
    monkeypatch.setenv("VIBE_CLI_OIDC_ISSUER", _ISSUER)
    monkeypatch.delenv("VIBE_CLI_OIDC_CLIENT_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_AUDIENCE", raising=False)
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(TelemetryClient, "send_telemetry_event", _SEND_EVENT)
    config = OidcConfig.from_env()
    key = RSAKey.generate_key(2048, parameters={"kid": "analytics"})
    callbacks: list[FakeCallback] = []
    requests: list[httpx2.Request] = []
    sessions: list[httpx2.AsyncClient] = []
    nonce = ""

    def issuer_response(request):
        assert request.url.host == "auth.mistral.ai"
        match request.url.path:
            case "/.well-known/openid-configuration":
                return httpx2.Response(
                    200,
                    json={
                        "issuer": _ISSUER,
                        "authorization_endpoint": _ISSUER + "/authorize",
                        "token_endpoint": _ISSUER + "/token",
                        "jwks_uri": _ISSUER + "/jwks",
                    },
                )
            case "/jwks":
                return httpx2.Response(200, json={"keys": [key.as_dict(private=False)]})
            case "/token":
                now = int(time.time())
                token = jwt.encode(
                    {"alg": "RS256", "kid": "analytics"},
                    {
                        "iss": _ISSUER,
                        "aud": config.client_id,
                        "sub": "synthetic-user",
                        "iat": now,
                        "exp": now + 3600,
                        "nonce": nonce,
                        "sid": "synthetic-session",
                        "email": "synthetic@example.invalid",
                    },
                    key,
                )
                return httpx2.Response(
                    200,
                    json={
                        "access_token": "synthetic-access",
                        "refresh_token": "synthetic-refresh",
                        "id_token": token,
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
        raise AssertionError(request.url)

    def session(**kwargs):
        value = AsyncOAuth2Client(
            transport=httpx2.MockTransport(issuer_response), **kwargs
        )
        sessions.append(cast(httpx2.AsyncClient, value))
        return value

    def callback(redirect_uri, state):
        value = FakeCallback(redirect_uri, state)
        callbacks.append(value)
        return value

    def analytics_response(request):
        # Producer: albe/services/auth/grants/oidc_id_token.py requires tenant
        # context; services/oidc_id_token.py requires sub/sid/email/exp claims.
        assert request.headers.get("Authorization") != "Bearer synthetic-api-key"
        assert request.headers["x-external-organization-id"] == "synthetic-org"
        token = request.headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, key, algorithms=["RS256"]).claims
        assert claims["iss"] == _ISSUER
        assert claims["aud"] == config.client_id
        assert all(claim in claims for claim in ["sub", "sid", "email", "exp"])
        requests.append(request)
        return httpx2.Response(201)

    original_http_init = VibeAsyncHTTPClient.__init__

    def http_init(self, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(analytics_response)
        original_http_init(self, **kwargs)

    monkeypatch.setattr(VibeAsyncHTTPClient, "__init__", http_init)
    monkeypatch.setattr(_oidc_client, "AsyncOAuth2Client", session)
    monkeypatch.setattr(_oidc_service, "OidcCallback", callback)
    store = FakeStore()
    # Runtime credential lookups read the same slot the sign-in writes.
    monkeypatch.setattr(OidcCredentialStore, "_load", lambda self: store.tokens)
    service = OidcService(OidcClient(config), store)
    monkeypatch.setattr(OidcService, "from_env", classmethod(lambda cls: service))

    def set_nonce(url):
        nonlocal nonce
        nonce = parse_qs(urlsplit(url).query)["nonce"][0]

    return service, store, callbacks, requests, sessions, set_nonce


async def ready(service, callbacks, set_nonce):
    async with asyncio.timeout(2):
        while service._attempt.authorization_url is None:
            await asyncio.sleep(0)
    set_nonce(service._attempt.authorization_url)
    return callbacks[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    ["success", "error", "declined", "cancelled", "disconnect", "early_cancel"],
)
@pytest.mark.parametrize("has_api_key", [False, True])
async def test_onboarding_events_follow_real_service_without_polling(
    oidc_analytics, monkeypatch, outcome, has_api_key
):
    service, store, callbacks, requests, sessions, set_nonce = oidc_analytics
    if has_api_key:
        monkeypatch.setenv("MISTRAL_API_KEY", "synthetic-api-key")
    config = build_test_vibe_config(enable_telemetry=True)

    async def factory(info):
        from vibe.app_server._setup import _launch_context

        return TelemetryClient(
            config_getter=lambda: config, launch_context=_launch_context(info)
        )

    handler = SetupOidcHandler(factory)
    try:
        result = await handler.dispatch(
            "setup/oidc-start",
            {},
            client_info=ClientInfo(name="vibe-rs", version="test"),
        )
        assert isinstance(result, OidcStartResponse)
        if outcome == "early_cancel":
            await handler.dispatch(
                "setup/oidc-cancel", {"attemptId": result.attempt_id}
            )
            await handler.close()
            payloads = [json.loads(request.content) for request in requests]
            # Runtime OIDC mode ignores stored API keys even before login.
            assert payloads == []
            return
        callback = await ready(service, callbacks, set_nonce)
        attempt = service._attempt
        if outcome == "success":
            callback.result.set_result(
                callback.redirect_uri
                + "?state="
                + callback.state
                + "&code=synthetic-code"
            )
            await asyncio.wait_for(attempt.task, 2)
        elif outcome in {"error", "declined"}:
            callback.result.set_exception(
                OidcError("OIDC authorization was declined")
                if outcome == "declined"
                else RuntimeError("sensitive-issuer-error")
            )
            await asyncio.wait_for(attempt.task, 2)
        elif outcome == "cancelled":
            await handler.dispatch(
                "setup/oidc-cancel", {"attemptId": result.attempt_id}
            )
        await handler.close()
        if outcome != "success":
            assert requests == []
            return
        payloads = [json.loads(request.content) for request in requests]
        assert [p["event"] for p in payloads] == [
            "vibe.onboarding_sign_in.started",
            "vibe.onboarding_sign_in.done",
        ]
        assert all(p["properties"]["attempt_id"] == result.attempt_id for p in payloads)
        assert payloads[1]["properties"]["status"] == (
            "cancelled"
            if outcome == "disconnect"
            else "error"
            if outcome == "declined"
            else outcome
        )
        assert isinstance(payloads[1]["properties"]["duration_ms"], int)
        for request, payload in zip(requests, payloads, strict=True):
            properties = payload["properties"]
            assert properties["auth_method"] == "oidc"
            assert properties["client_name"] == "vibe-rs"
            assert properties["call_source"] == "vibe_code"
            assert properties["is_main_call"] is False
            assert properties["metadata"] == {}
            assert "call_type" not in properties
            if payload["event"].endswith(".done") and outcome != "success":
                assert properties["error_type"] == (
                    "unexpected_error"
                    if outcome == "error"
                    else "oidc_error"
                    if outcome == "declined"
                    else "cancelled"
                )
            else:
                assert "error_type" not in properties
            assert request.url.path == "/v1/datalake/events"
            # OIDC mode ignores a stored API key: the regular client sent the
            # marker and the HTTP boundary swapped in the session's ID token.
            assert request.url.host == "api.mistral.ai"
            assert (
                request.headers["Authorization"]
                == "Bearer " + store.tokens.id_token.get_secret_value()
            )
            assert request.extensions.get("follow_redirects") is not True
            serialized = json.dumps(payload)
            for secret in [
                "synthetic-access",
                "synthetic-refresh",
                "synthetic-api-key",
                "synthetic-code",
                "sensitive-issuer-error",
                "synthetic-user",
                "synthetic-session",
                "synthetic@example.invalid",
                "synthetic-org",
            ]:
                assert secret not in serialized
            assert "authorization_url" not in properties
        assert all(session.is_closed for session in sessions)
    finally:
        await handler.close()


@pytest.mark.asyncio
async def test_disabled_telemetry_never_delivers_even_after_success(oidc_analytics):
    service, _, callbacks, requests, _, set_nonce = oidc_analytics
    config = build_test_vibe_config(enable_telemetry=False)

    async def factory(info):
        return TelemetryClient(config_getter=lambda: config)

    handler = SetupOidcHandler(factory)
    await handler.dispatch(
        "setup/oidc-start", {}, client_info=ClientInfo(name="vibe-rs", version="test")
    )
    callback = await ready(service, callbacks, set_nonce)
    callback.result.set_result(
        callback.redirect_uri + "?state=" + callback.state + "&code=synthetic-code"
    )
    await service._attempt.task
    await handler.close()
    assert requests == []


@pytest.mark.asyncio
async def test_host_telemetry_uses_initialize_identity(monkeypatch, process_services):
    from vibe.core.config.harness_files import HarnessFilesManager

    config = build_test_vibe_config(enable_telemetry=False)
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user",)), org_config=process_services.org_config
    )
    monkeypatch.setattr(
        handler, "_load_config", lambda cwd: asyncio.sleep(0, result=config)
    )
    client = await handler.setup_telemetry(ClientInfo(name="vibe-rs", version="test"))
    assert client.build_client_event_metadata()["client_name"] == "vibe-rs"
    assert not client.is_active()
    await client.aclose()


@pytest.mark.asyncio
async def test_server_routes_initialize_identity_and_drains_telemetry(
    oidc_analytics, monkeypatch, process_services
):
    from tests.app_server._helpers import setup_client

    service, _, callbacks, requests, _, set_nonce = oidc_analytics
    config = build_test_vibe_config(enable_telemetry=True)
    monkeypatch.setattr(
        HostRequestHandler,
        "_load_config",
        lambda self, cwd: asyncio.sleep(0, result=config),
    )
    client, client_transport, server_transport = await setup_client(
        entrypoint="cli", process_services=process_services
    )
    try:
        result = await client.request("setup/oidc-start", {})
        callback = await ready(service, callbacks, set_nonce)
        callback.result.set_result(
            callback.redirect_uri + "?state=" + callback.state + "&code=synthetic-code"
        )
        await service._attempt.task
        await client.close()
        payloads = [json.loads(request.content) for request in requests]
        assert [p["event"] for p in payloads] == [
            "vibe.onboarding_sign_in.started",
            "vibe.onboarding_sign_in.done",
        ]
        assert all(
            p["properties"]["attempt_id"] == result["attemptId"] for p in payloads
        )
        assert all(p["properties"]["client_name"] == "setup-test" for p in payloads)
        assert all(p["properties"]["agent_entrypoint"] == "cli" for p in payloads)
    finally:
        await client.close()
        await client_transport.close()
        await server_transport.close()


@pytest.mark.asyncio
async def test_broken_telemetry_setup_does_not_prevent_login(oidc_analytics):
    service, _, callbacks, requests, _, set_nonce = oidc_analytics

    async def factory(*args):
        raise RuntimeError("analytics unavailable")

    handler = SetupOidcHandler(factory)
    try:
        await handler.dispatch(
            "setup/oidc-start",
            {},
            client_info=ClientInfo(name="vibe-rs", version="test"),
        )
        callback = await ready(service, callbacks, set_nonce)
        callback.result.set_result(
            callback.redirect_uri + "?state=" + callback.state + "&code=synthetic-code"
        )
        await service._attempt.task
        assert service._attempt.status == "completed"
        assert requests == []
    finally:
        await handler.close()


@pytest.mark.asyncio
async def test_retry_keeps_events_on_their_own_attempt(oidc_analytics):
    service, _, callbacks, requests, _, set_nonce = oidc_analytics
    config = build_test_vibe_config(enable_telemetry=True)

    async def factory(info):
        return TelemetryClient(config_getter=lambda: config)

    handler = SetupOidcHandler(factory)
    try:
        first = await handler.dispatch(
            "setup/oidc-start",
            {},
            client_info=ClientInfo(name="vibe-rs", version="test"),
        )
        assert isinstance(first, OidcStartResponse)
        callback = await ready(service, callbacks, set_nonce)
        callback.result.set_exception(RuntimeError("declined"))
        await service._attempt.task
        # No credential yet; let the best-effort deliveries fail before retrying.
        await asyncio.sleep(0)
        second = await handler.dispatch(
            "setup/oidc-start",
            {},
            client_info=ClientInfo(name="vibe-rs", version="test"),
        )
        callback = await ready(service, callbacks, set_nonce)
        callback.result.set_result(
            callback.redirect_uri + "?state=" + callback.state + "&code=synthetic-code"
        )
        await service._attempt.task
        await handler.close()
        payloads = [json.loads(request.content) for request in requests]
        assert len(payloads) == 2
        assert isinstance(second, OidcStartResponse)
        assert all(p["properties"]["attempt_id"] == second.attempt_id for p in payloads)
        assert all(p["properties"]["attempt_id"] != first.attempt_id for p in payloads)
        assert payloads[-1]["properties"]["status"] == "success"
    finally:
        await handler.close()
