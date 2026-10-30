from __future__ import annotations

import asyncio
import base64
import time
from typing import cast
from unittest.mock import AsyncMock

import httpx2
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import SecretStr
import pytest

from tests.conftest import build_test_vibe_config
from vibe.app_server.config import AudioProviderView, TTSModelConfigView
from vibe.cli.tts import MistralTTSClient
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    MistralPlatformHTTPClient,
)
from vibe.setup.auth import _oidc_runtime
from vibe.setup.auth._oidc_config import OidcError
from vibe.setup.auth._oidc_credentials import OidcCredentialStore
from vibe.setup.auth._oidc_runtime import OIDC_API_KEY, oidc_headers
from vibe.setup.auth._oidc_service import OidcService
from vibe.utils.api_keys import (
    ApiKeySource,
    resolve_api_key,
    resolve_api_key_with_origin,
    session_credentials,
)
from vibe.utils.http import (
    VibeAsyncHTTPClient,
    build_ssl_context,
    configure_ssl_context,
)


def test_oidc_ignores_every_api_key_source(monkeypatch, tokens):
    monkeypatch.setenv("MISTRAL_API_KEY", "env-key")
    monkeypatch.setattr(
        "vibe.utils.api_keys.get_api_key_from_keyring",
        lambda _: pytest.fail("API keyring read"),
    )
    with session_credentials({"MISTRAL_API_KEY": "session-key"}):
        resolved = resolve_api_key_with_origin("MISTRAL_API_KEY")
    assert resolved is not None
    assert resolved[0] == OIDC_API_KEY
    assert resolved[1].source == ApiKeySource.OIDC
    assert resolved[1].describe() == "the CLI OIDC session"


@pytest.mark.parametrize("missing", [True, False])
def test_oidc_never_falls_back_when_missing_or_configuration_changed(
    monkeypatch, tokens, missing
):
    monkeypatch.setenv("MISTRAL_API_KEY", "env-key")
    if missing:
        monkeypatch.setattr(OidcCredentialStore, "_load", lambda self: None)
    else:
        monkeypatch.setenv("VIBE_CLI_OIDC_CLIENT_ID", "different-client")
    assert resolve_api_key("MISTRAL_API_KEY") is None


def test_unreadable_oidc_storage_reads_as_signed_out(monkeypatch, tokens):
    from vibe.setup.auth._oidc_credentials import OidcStorageError

    def unreadable(self):
        raise OidcStorageError("Could not read OIDC credentials from keyring")

    monkeypatch.setattr(OidcCredentialStore, "_load", unreadable)
    assert resolve_api_key("MISTRAL_API_KEY") is None


def test_invalid_oidc_configuration_reads_as_signed_out(monkeypatch, tokens):
    monkeypatch.setenv("VIBE_CLI_OIDC_ISSUER", "http://issuer.example")
    assert resolve_api_key("MISTRAL_API_KEY") is None


@pytest.mark.asyncio
async def test_expired_oidc_session_requires_platform_reauthentication(
    monkeypatch, tokens
):
    from vibe.core.platform_auth import (
        PlatformCredentialRequired,
        PlatformCredentialTarget,
    )

    async def refused(url: str) -> dict[str, str]:
        raise OidcError("OIDC re-login required: refresh token rejected")

    monkeypatch.setattr(_oidc_runtime, "oidc_headers", refused)
    target = PlatformCredentialTarget(base_url="https://api.mistral.ai")
    result = await ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY").resolve(target)
    assert result == PlatformCredentialRequired(reason="reauthentication_required")


def test_api_key_mode_does_not_read_oidc(monkeypatch, tokens):
    monkeypatch.delenv("VIBE_CLI_OIDC_ENABLED")
    monkeypatch.setenv("MISTRAL_API_KEY", "env-key")
    monkeypatch.setattr(
        OidcCredentialStore, "_load", lambda self: pytest.fail("OIDC read")
    )
    assert resolve_api_key("MISTRAL_API_KEY") == "env-key"


def test_other_providers_keep_their_credentials(monkeypatch, tokens):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    assert resolve_api_key("ANTHROPIC_API_KEY") == "anthropic-key"


@pytest.mark.asyncio
async def test_cached_http_clients_swap_current_id_token_and_tenant(
    monkeypatch, tokens
):
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "org")
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "workspace")
    current = [tokens.id_token.get_secret_value()]
    monkeypatch.setattr(_oidc_runtime, "_resolve_id_token", lambda: current[0])
    requests = []
    async with VibeAsyncHTTPClient(
        transport=httpx2.MockTransport(
            lambda request: requests.append(request) or httpx2.Response(200)
        ),
        headers={"Authorization": f"Bearer {OIDC_API_KEY}"},
    ) as client:
        await client.get("https://api.mistral.ai/v1/models")
        current[0] = "renewed-id-token"
        await client.get("https://api.mistral.ai/v1/models")
    assert [r.headers["Authorization"] for r in requests] == [
        f"Bearer {tokens.id_token.get_secret_value()}",
        "Bearer renewed-id-token",
    ]
    for request in requests:
        assert request.headers["x-external-organization-id"] == "org"
        assert request.headers["x-external-workspace-id"] == "workspace"
        assert "access-secret" not in str(request.headers)


@pytest.mark.asyncio
async def test_existing_platform_client_uses_the_same_swap(monkeypatch, tokens):
    requests = []
    async with MistralPlatformHTTPClient(
        base_url="https://api.mistral.ai/v1",
        credentials=ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY"),
        transport=httpx2.MockTransport(
            lambda request: requests.append(request) or httpx2.Response(200)
        ),
    ) as client:
        await client.get("users/me")
    assert (
        requests[0].headers["Authorization"]
        == f"Bearer {tokens.id_token.get_secret_value()}"
    )


@pytest.mark.asyncio
async def test_mistral_sdk_request_uses_id_token(monkeypatch, tokens):
    requests = []
    original = VibeAsyncHTTPClient.__init__

    def init(self, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(
            lambda request: (
                requests.append(request)
                or httpx2.Response(
                    200, json={"audio_data": base64.b64encode(b"audio").decode()}
                )
            )
        )
        original(self, **kwargs)

    monkeypatch.setattr(VibeAsyncHTTPClient, "__init__", init)
    client = MistralTTSClient(
        AudioProviderView(
            api_base="https://api.mistral.ai",
            api_key_env_var="MISTRAL_API_KEY",
            client="mistral",
        ),
        TTSModelConfigView(name="model", voice="voice", response_format="wav"),
    )
    try:
        result = await client.speak("hello")
    finally:
        await client.close()
    assert result.audio_data == b"audio"
    assert (
        requests[0].headers["Authorization"]
        == f"Bearer {tokens.id_token.get_secret_value()}"
    )


@pytest.mark.asyncio
async def test_unrelated_bearer_credentials_are_not_rewritten(monkeypatch, tokens):
    monkeypatch.setattr(
        _oidc_runtime, "_resolve_id_token", lambda: pytest.fail("OIDC resolution")
    )
    async with VibeAsyncHTTPClient(
        transport=httpx2.MockTransport(lambda r: httpx2.Response(200))
    ) as client:
        response = await client.get(
            "https://other.example", headers={"Authorization": "Bearer custom-key"}
        )
    assert response.request.headers["Authorization"] == "Bearer custom-key"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://other.example",
        "https://api.globalaegis.net/v1/models",
        "http://api.mistral.ai/v1/models",
        "https://api.mistral.ai:8443/v1/models",
    ],
)
async def test_oidc_marker_cannot_authorize_an_untrusted_destination(
    monkeypatch, tokens, url
):
    monkeypatch.setattr(
        _oidc_runtime,
        "_resolve_id_token",
        lambda: pytest.fail("token resolved before origin check"),
    )
    with pytest.raises(OidcError, match="destination"):
        await oidc_headers(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://api.mistral.ai:443/v1/models",
        "wss://api.mistral.ai:443/v1/audio",
        "wss://api.mistral.ai/v1/audio",
        "https://chat.mistral.ai:443/api",
    ],
)
async def test_oidc_destination_treats_explicit_default_port_as_same_origin(
    monkeypatch, tokens, url
):
    # httpx2.URL drops a scheme-default port (port is None for :443 on https
    # and wss), so an explicit default port is the audience's origin.
    monkeypatch.setattr(_oidc_runtime, "_resolve_id_token", lambda: "id-token")
    headers = await oidc_headers(url)
    assert headers["Authorization"] == "Bearer id-token"


@pytest.mark.asyncio
async def test_oidc_does_not_follow_redirects(tokens):
    requests = []
    async with VibeAsyncHTTPClient(
        follow_redirects=True,
        transport=httpx2.MockTransport(
            lambda r: (
                requests.append(r)
                or httpx2.Response(302, headers={"Location": "https://other.example"})
            )
        ),
    ) as client:
        response = await client.get(
            "https://api.mistral.ai/v1/models",
            headers={"Authorization": f"Bearer {OIDC_API_KEY}"},
        )
    assert response.status_code == 302
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", [None, float("nan"), 0.0])
async def test_id_token_expiry_forces_refresh(monkeypatch, tokens, expires_at):
    expired = jwt.encode(
        {"alg": "RS256"}, {"exp": time.time() - 60}, RSAKey.generate_key(2048)
    )
    # OidcClient.validated_tokens stores the verified exp; older bundles lack it.
    old = tokens.model_copy(
        update={"id_token": SecretStr(expired), "id_token_expires_at": expires_at}
    )
    refresh = AsyncMock(side_effect=[old, tokens])
    monkeypatch.setattr(OidcService, "access_tokens", refresh)
    assert (await oidc_headers("https://api.mistral.ai"))[
        "Authorization"
    ] == f"Bearer {tokens.id_token.get_secret_value()}"
    assert refresh.call_args_list[1].kwargs == {"force_refresh": True}


@pytest.mark.asyncio
async def test_refresh_without_a_new_id_token_requires_login(monkeypatch, tokens):
    expired = jwt.encode(
        {"alg": "RS256"}, {"exp": time.time() - 60}, RSAKey.generate_key(2048)
    )
    old = tokens.model_copy(
        update={"id_token": SecretStr(expired), "id_token_expires_at": time.time() - 60}
    )
    monkeypatch.setattr(OidcService, "access_tokens", AsyncMock(return_value=old))
    with pytest.raises(OidcError, match="not renewed"):
        await oidc_headers("https://api.mistral.ai")


@pytest.mark.asyncio
async def test_oidc_failure_does_not_expose_token_payload(monkeypatch, tokens):
    monkeypatch.setattr(
        OidcService,
        "access_tokens",
        AsyncMock(side_effect=RuntimeError("access-secret refresh-secret")),
    )
    with pytest.raises(OidcError) as caught:
        await oidc_headers("https://api.mistral.ai")
    assert "access-secret" not in str(caught.value)
    assert "refresh-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_unified_provider_resolves_rotating_id_tokens(monkeypatch, tokens):
    from mistralai_vibe_local_harness.vibe import ProviderCredentialSnapshot
    from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
    from vibe.app_server._provider_credentials import ProviderCredentialService

    monkeypatch.setenv("MISTRAL_API_KEY", "ignored-api-key")
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "org")
    current = [tokens.id_token.get_secret_value()]
    monkeypatch.setattr(_oidc_runtime, "_resolve_id_token", lambda: current[0])
    service = ProviderCredentialService(
        FakeConfigOrchestrator(build_test_vibe_config())
    )
    first = await service.resolve()
    assert isinstance(first, ProviderCredentialSnapshot)
    assert first.token == current[0]
    assert first.headers["x-external-organization-id"] == "org"
    assert first.api_key_source == "the CLI OIDC session"
    assert first.follow_redirects is False
    current[0] = "renewed-id-token"
    second = await service.resolve()
    assert isinstance(second, ProviderCredentialSnapshot)
    assert second.token == "renewed-id-token"
    assert second.revision != first.revision
    assert second.follow_redirects is False


@pytest.mark.asyncio
@pytest.mark.parametrize("rotated", [False, True])
async def test_unified_oidc_rejection_compares_the_sent_token(
    monkeypatch, tokens, rotated
):
    from mistralai_vibe_local_harness.vibe import (
        ProviderAuthRequired,
        ProviderCredentialSnapshot,
    )
    from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
    from vibe.app_server._provider_credentials import ProviderCredentialService

    current = [tokens.id_token.get_secret_value()]
    monkeypatch.setattr(_oidc_runtime, "_resolve_id_token", lambda: current[0])
    service = ProviderCredentialService(
        FakeConfigOrchestrator(build_test_vibe_config())
    )
    sent = await service.resolve()
    assert isinstance(sent, ProviderCredentialSnapshot)
    if rotated:
        current[0] = "renewed-id-token"
    await service.reject(observed_revision=sent.revision, reason="http_unauthorized")
    result = await service.resolve()
    if rotated:
        assert isinstance(result, ProviderCredentialSnapshot)
        assert result.token == current[0]
        assert result.revision != sent.revision
    else:
        assert isinstance(result, ProviderAuthRequired)
        assert result.reason == "rejected"
        current[0] = "renewed-id-token"
        recovered = await service.resolve()
        assert isinstance(recovered, ProviderCredentialSnapshot)
        assert recovered.revision != sent.revision


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_oidc_rejection_keeps_original_failure_when_refresh_fails(
    monkeypatch, tokens, cancelled
):
    from mistralai_vibe_local_harness.vibe import (
        ProviderAuthRequired,
        ProviderCredentialSnapshot,
    )
    from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
    from vibe.app_server._provider_credentials import ProviderCredentialService

    token = tokens.id_token.get_secret_value()
    monkeypatch.setattr(_oidc_runtime, "_resolve_id_token", lambda: token)
    service = ProviderCredentialService(
        FakeConfigOrchestrator(build_test_vibe_config())
    )
    sent = await service.resolve()
    assert isinstance(sent, ProviderCredentialSnapshot)

    def fail_refresh():
        if cancelled:
            raise asyncio.CancelledError
        raise OidcError("OIDC refresh rejected; re-login required")

    monkeypatch.setattr(_oidc_runtime, "_resolve_id_token", fail_refresh)
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await service.reject(
                observed_revision=sent.revision, reason="http_unauthorized"
            )
        assert service._rejected == {}
        return
    await service.reject(observed_revision=sent.revision, reason="http_unauthorized")
    monkeypatch.setattr(_oidc_runtime, "_resolve_id_token", lambda: token)
    rejected = await service.resolve()
    assert isinstance(rejected, ProviderAuthRequired)
    assert rejected.reason == "rejected"


@pytest.mark.asyncio
async def test_unified_api_key_provider_keeps_redirects_with_oidc_flag_enabled(
    monkeypatch, tokens
):
    """*Prepare*: OIDC is enabled and an independent gateway has its own API key.
    *Do*: Resolve the gateway's model credential.
    *Assert*: Its API-key redirect policy is unchanged.
    """
    from mistralai_vibe_local_harness.vibe import ProviderCredentialSnapshot
    from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
    from vibe.app_server._provider_credentials import ProviderCredentialService

    # Prepare
    monkeypatch.setenv("GATEWAY_API_KEY", "gateway-key")
    config = build_test_vibe_config(
        providers=[
            {
                "name": "mistral",
                "api_base": "https://gateway.example/v1",
                "api_key_env_var": "GATEWAY_API_KEY",
                "backend": "mistral",
            }
        ]
    )
    service = ProviderCredentialService(FakeConfigOrchestrator(config))

    # Do
    credential = await service.resolve()

    # Assert
    assert isinstance(credential, ProviderCredentialSnapshot)
    assert credential.token == "gateway-key"
    assert credential.headers == {"Authorization": "Bearer gateway-key"}
    assert credential.follow_redirects is True


@pytest.mark.asyncio
async def test_realtime_transcription_gets_oidc_headers(monkeypatch, tokens):
    from unittest.mock import MagicMock

    from vibe.app_server.config import AudioProviderView, TranscribeModelConfigView
    from vibe.cli.transcribe import MistralTranscribeClient

    calls = []

    async def stream(**kwargs):
        calls.append(kwargs)
        return
        yield

    async def audio():
        return
        yield b""

    client = MistralTranscribeClient(
        AudioProviderView(
            api_base="wss://api.mistral.ai",
            api_key_env_var="MISTRAL_API_KEY",
            client="mistral",
        ),
        TranscribeModelConfigView(
            name="model",
            encoding="pcm_s16le",
            sample_rate=16000,
            language="en",
            target_streaming_delay_ms=200,
        ),
    )
    sdk = MagicMock()
    sdk.audio.realtime.transcribe_stream = stream
    client._client = sdk
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "org")
    events = [event async for event in client.transcribe(audio())]
    assert events == []
    assert (
        calls[0]["http_headers"]["Authorization"]
        == f"Bearer {tokens.id_token.get_secret_value()}"
    )
    assert calls[0]["http_headers"]["x-external-organization-id"] == "org"


@pytest.mark.asyncio
async def test_concurrent_requests_share_token_rotation(monkeypatch, tokens):
    active = 0
    maximum = 0

    async def resolve(self, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return tokens

    monkeypatch.setattr(OidcService, "access_tokens", resolve)
    headers = await asyncio.gather(
        *(oidc_headers("https://api.mistral.ai") for _ in range(4))
    )
    assert maximum == 1
    assert all(
        value["Authorization"] == f"Bearer {tokens.id_token.get_secret_value()}"
        for value in headers
    )


def test_oidc_uses_effective_tls_policy(monkeypatch, tokens):
    configure_ssl_context(enable_system_trust_store=True)
    session = cast(httpx2.AsyncClient, OidcService.from_env().client.session())
    transport = cast(httpx2.AsyncHTTPTransport, session._transport)
    assert transport._pool._ssl_context is build_ssl_context()
    asyncio.run(session.aclose())
