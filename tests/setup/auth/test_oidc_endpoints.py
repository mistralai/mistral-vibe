from __future__ import annotations

from unittest.mock import patch

import httpx2
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import SpanExportResult
import pytest

from tests.app_server._helpers import setup_client
from tests.conftest import build_test_vibe_config
from vibe.core.tracing import setup_tracing
from vibe.setup.auth import _oidc_runtime
from vibe.setup.auth._oidc_otel import OidcOTelTransport
from vibe.utils.http import VibeAsyncHTTPClient


@pytest.mark.asyncio
async def test_oidc_mode_rejects_key_submission_without_writing(
    monkeypatch, tokens, process_services
):
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        with patch("vibe.app_server._setup.persist_api_key") as persist:
            with pytest.raises(Exception, match="OIDC mode requires browser sign-in"):
                await client.request(
                    "setup/store-credential", {"provider": "mistral", "apiKey": "key"}
                )
            persist.assert_not_called()
    finally:
        await client_transport.close()
        await server_transport.close()


def test_trace_export_swaps_each_batch_and_keeps_tenant_headers(monkeypatch, tokens):
    current = [tokens.id_token.get_secret_value()]
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "org")
    monkeypatch.setattr(_oidc_runtime, "_resolve_id_token", lambda: current[0])
    requests = []
    original = VibeAsyncHTTPClient.__init__

    def init(self, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(
            lambda request: requests.append(request) or httpx2.Response(200)
        )
        original(self, **kwargs)

    monkeypatch.setattr(VibeAsyncHTTPClient, "__init__", init)
    # Locked OTel exporter's HTTP transport seam calls request for each batch.
    exporter = OTLPSpanExporter(
        endpoint="https://api.mistral.ai/telemetry/v1/traces",
        headers={"Authorization": "Bearer vibe-cli-oidc"},
        _transport=OidcOTelTransport(),
    )
    assert exporter.export([]) == SpanExportResult.SUCCESS
    current[0] = "renewed-id-token"
    assert exporter.export([]) == SpanExportResult.SUCCESS
    exporter.shutdown()
    assert [r.headers["Authorization"] for r in requests] == [
        f"Bearer {tokens.id_token.get_secret_value()}",
        "Bearer renewed-id-token",
    ]
    assert all(r.headers["x-external-organization-id"] == "org" for r in requests)


def test_trace_export_failure_is_nonsecret_and_never_falls_back(monkeypatch, tokens):
    monkeypatch.setattr(
        _oidc_runtime,
        "_resolve_id_token",
        lambda: (_ for _ in ()).throw(RuntimeError("secret-token")),
    )
    result = OidcOTelTransport().request(
        "POST",
        "https://api.mistral.ai/telemetry/v1/traces",
        headers={"Authorization": "Bearer vibe-cli-oidc"},
        data=b"body",
    )
    assert result.error is not None
    assert "secret-token" not in str(result.error)
    assert result.status_code is None


@pytest.mark.asyncio
async def test_platform_credentials_track_token_and_tenant_revisions(
    monkeypatch, tokens
):
    from vibe.core.platform_auth import (
        ApiKeyPlatformCredentialProvider,
        PlatformCredentialSnapshot,
        PlatformCredentialTarget,
    )

    current = [tokens.id_token.get_secret_value()]
    monkeypatch.setattr(_oidc_runtime, "_resolve_id_token", lambda: current[0])
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "org-a")
    provider = ApiKeyPlatformCredentialProvider("MISTRAL_API_KEY")
    target = PlatformCredentialTarget(base_url="https://api.mistral.ai/v1")
    first = await provider.resolve(target)
    current[0] = "renewed-token"
    second = await provider.resolve(target)
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "org-b")
    third = await provider.resolve(target)
    assert isinstance(first, PlatformCredentialSnapshot)
    assert isinstance(second, PlatformCredentialSnapshot)
    assert isinstance(third, PlatformCredentialSnapshot)
    assert first.revision != second.revision != third.revision
    assert third.headers["Authorization"] == "Bearer renewed-token"
    assert third.headers["x-external-organization-id"] == "org-b"


@pytest.mark.asyncio
async def test_connector_gateway_resolves_the_connection_credential_per_call(
    monkeypatch, tokens
):
    from unittest.mock import MagicMock

    from vibe.app_server import _unified_harness_backend_adapter as adapter
    from vibe.setup.auth._cli_oidc_platform_credentials import (
        CliOidcPlatformCredentialProvider,
    )
    from vibe.setup.auth._oidc_tenant import OidcTenantResolver

    provider = CliOidcPlatformCredentialProvider(
        OidcTenantResolver(),
        id_token=lambda: tokens.id_token.get_secret_value(),
        stored_tokens=lambda: tokens,
    )
    host = MagicMock()
    context = MagicMock(
        spec=adapter.UnifiedSessionContext,
        connector_base_url="https://api.mistral.ai",
        mcp_enable_system_trust_store=True,
        platform_credentials=lambda _env: provider,
        config_orchestrator=MagicMock(config=build_test_vibe_config()),
    )
    monkeypatch.setattr(adapter, "_harness_connector_catalog", lambda value: value)
    monkeypatch.setattr(adapter, "_harness_connector_selection", lambda value: value)
    with patch.object(adapter, "HarnessConnectorGatewayClient") as gateway:
        adapter._configure_host_connectors(host, context, MagicMock(), MagicMock())
        host.configure_connectors.call_args.args[2]()
    resolve = gateway.call_args.kwargs["headers_resolver"]
    headers = await resolve("https://api.mistral.ai/v1/connectors-gateway/c/mcp")
    assert headers["Authorization"] == f"Bearer {tokens.id_token.get_secret_value()}"
    assert headers["x-external-organization-id"] == "test-org"
    with pytest.raises(Exception, match="Platform credential is invalid"):
        await resolve("https://evil.example/mcp")
    # Never the gateway's own key header: the resolver always supplies one.
    assert gateway.call_args.kwargs["api_key"] == ""


def test_setup_tracing_selects_oidc_transport_only_for_managed_auth(
    monkeypatch, tokens
):
    config = build_test_vibe_config(enable_telemetry=True, enable_otel=True)
    with (
        patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"
        ) as exporter,
        patch("opentelemetry.trace.set_tracer_provider"),
    ):
        setup_tracing(config)
    assert isinstance(exporter.call_args.kwargs["_transport"], OidcOTelTransport)
    config = config.model_copy(update={"otel_endpoint": "https://customer.example"})
    with (
        patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter"
        ) as exporter,
        patch("opentelemetry.trace.set_tracer_provider"),
    ):
        setup_tracing(config)
    assert "_transport" not in exporter.call_args.kwargs
