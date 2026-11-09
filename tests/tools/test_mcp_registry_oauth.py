from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from unittest.mock import AsyncMock, patch

import anyio
import httpx2
from mcp.client.auth import OAuthFlowError
import pytest

from vibe.core.config import MCPOAuth, MCPStreamableHttp
from vibe.core.tools.mcp import MCPRegistry, RemoteTool, tools as mcp_tools
from vibe.core.tools.mcp.authorization import (
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationResult,
    MCPAuthorizationSnapshot,
)
from vibe.core.tools.mcp.tools import is_authorization_rejection


def _oauth_server() -> MCPStreamableHttp:
    return MCPStreamableHttp(
        name="linear",
        transport="streamable-http",
        url="https://mcp.example.com/mcp",
        auth=MCPOAuth(type="oauth", scopes=["read"]),
    )


def _reference() -> MCPAuthorizationRef:
    return MCPAuthorizationRef(
        server_name="linear",
        server_fingerprint="fingerprint",
        kind="oauth",
        descriptor_revision="descriptor-1",
    )


def _snapshot(
    connection_revision: str = "connection-1",
    descriptor_revision: str = "descriptor-1",
    *,
    headers: Mapping[str, str] | None = None,
    expires_at: datetime | None = None,
) -> MCPAuthorizationSnapshot:
    return MCPAuthorizationSnapshot(
        headers=headers or {"Authorization": "Bearer token"},
        connection_revision=connection_revision,
        descriptor_revision=descriptor_revision,
        expires_at=expires_at,
    )


@dataclass
class FakeAuthorizationProvider:
    resolved: MCPAuthorizationResult
    rejected: MCPAuthorizationResult | None = None
    resolve_calls: list[MCPAuthorizationRef] = field(default_factory=list)
    reject_calls: list[tuple[MCPAuthorizationRef, str, str]] = field(
        default_factory=list
    )

    async def resolve(self, reference: MCPAuthorizationRef) -> MCPAuthorizationResult:
        self.resolve_calls.append(reference)
        return self.resolved

    async def reject(
        self,
        reference: MCPAuthorizationRef,
        *,
        observed_connection_revision: str,
        reason: Literal["http_unauthorized", "mcp_unauthorized"],
    ) -> MCPAuthorizationResult:
        self.reject_calls.append((reference, observed_connection_revision, reason))
        return self.rejected or MCPAuthorizationRequired(
            reason="rejected",
            descriptor_revision=self.resolved.descriptor_revision,
            observed_connection_revision=observed_connection_revision,
        )


def _configure(
    registry: MCPRegistry,
    provider: FakeAuthorizationProvider,
    *,
    sink: AsyncMock | None = None,
) -> None:
    registry.configure_authorization(
        provider, {"linear": _reference()}, required_sink=sink
    )


def _http_status_error(status_code: int) -> httpx2.HTTPStatusError:
    request = httpx2.Request("POST", "https://mcp.example.com/mcp")
    response = httpx2.Response(status_code, request=request)
    return httpx2.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


def _unauthorized() -> httpx2.HTTPStatusError:
    return _http_status_error(401)


def test_authorization_rejection_uses_typed_invalid_credential_failures_only() -> None:
    """*Prepare*: Typed 401, typed 403, OAuth-flow, and misleading string failures.
    *Do*: Classify each failure at the legacy MCP transport boundary.
    *Assert*: Only invalid-credential types trigger credential rejection.
    """
    # Prepare
    failures = (
        _http_status_error(401),
        _http_status_error(403),
        OAuthFlowError("interactive authorization is required"),
        RuntimeError("authorization failed with status 401"),
    )

    # Do
    classified = tuple(is_authorization_rejection(failure) for failure in failures)

    # Assert
    assert classified == (True, False, True, False)


class TestMCPRegistryAuthorizationProvider:
    @pytest.mark.asyncio
    async def test_disabled_source_skips_authorization_and_discovery_until_refresh(
        self,
    ) -> None:
        server = _oauth_server().model_copy(update={"disabled": True})
        provider = FakeAuthorizationProvider(_snapshot())
        registry = MCPRegistry()
        _configure(registry, provider)
        discover = AsyncMock(return_value=[RemoteTool(name="search")])

        with patch("vibe.core.tools.mcp.registry.list_tools_http", new=discover):
            ordinary = await registry.get_tools_async([server])
            registry.clear()
            refreshed = await registry.get_tools_async([server])

        assert ordinary == {}
        assert set(refreshed) == {"linear_search"}
        assert provider.resolve_calls == [_reference()]
        discover.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_authorization_withdraws_source_and_emits_signal(
        self,
    ) -> None:
        required = MCPAuthorizationRequired(
            reason="missing", descriptor_revision="descriptor-1"
        )
        provider = FakeAuthorizationProvider(required)
        sink = AsyncMock()
        registry = MCPRegistry()
        _configure(registry, provider, sink=sink)

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http", new=AsyncMock()
        ) as discover:
            tools = await registry.get_tools_async([_oauth_server()])

        assert tools == {}
        assert registry.needs_auth == {"linear"}
        assert registry.descriptor_revision("linear") == "descriptor-1"
        discover.assert_not_awaited()
        sink.assert_awaited_once_with("linear", required)

    @pytest.mark.asyncio
    async def test_valid_authorization_is_injected_into_discovery_and_proxy(
        self,
    ) -> None:
        provider = FakeAuthorizationProvider(_snapshot())
        registry = MCPRegistry()
        _configure(registry, provider)
        remote = RemoteTool(name="create_issue")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            new=AsyncMock(return_value=[remote]),
        ) as discover:
            tools = await registry.get_tools_async([_oauth_server()])

        assert set(tools) == {"linear_create_issue"}
        assert registry.needs_auth == set()
        assert registry.descriptor_revision("linear") == "descriptor-1"
        await_args = discover.await_args
        assert await_args is not None
        assert await_args.kwargs["headers"] == {"Authorization": "Bearer token"}

    @pytest.mark.asyncio
    async def test_stale_rejection_retries_discovery_with_new_authorization(
        self,
    ) -> None:
        provider = FakeAuthorizationProvider(
            _snapshot(),
            rejected=_snapshot(
                "connection-2", headers={"Authorization": "Bearer replacement"}
            ),
        )
        registry = MCPRegistry()
        _configure(registry, provider)
        discover = AsyncMock(side_effect=[_unauthorized(), [RemoteTool(name="search")]])

        with patch("vibe.core.tools.mcp.registry.list_tools_http", new=discover):
            tools = await registry.get_tools_async([_oauth_server()])

        assert set(tools) == {"linear_search"}
        assert provider.reject_calls == [
            (_reference(), "connection-1", "http_unauthorized")
        ]
        assert discover.await_args_list[1].kwargs["headers"] == {
            "Authorization": "Bearer replacement"
        }

    @pytest.mark.asyncio
    async def test_current_rejection_withdraws_source_and_emits_signal(self) -> None:
        required = MCPAuthorizationRequired(
            reason="rejected",
            descriptor_revision="descriptor-2",
            observed_connection_revision="connection-1",
        )
        provider = FakeAuthorizationProvider(_snapshot(), rejected=required)
        sink = AsyncMock()
        registry = MCPRegistry()
        _configure(registry, provider, sink=sink)

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            new=AsyncMock(side_effect=_unauthorized()),
        ):
            tools = await registry.get_tools_async([_oauth_server()])

        assert tools == {}
        assert registry.needs_auth == {"linear"}
        sink.assert_awaited_once_with("linear", required)


@pytest.mark.asyncio
async def test_a_401_from_the_mcp_server_reads_as_an_authorization_rejection() -> None:
    """*Prepare*: An MCP server that answers the handshake with HTTP 401.
    *Do*: List its tools through the real MCP SDK and Vibe's MCP HTTP client.
    *Assert*: The failure reads as an authorization rejection. The SDK alone
        reports any HTTP error as one generic protocol error, which would leave
        a rejected credential in place instead of asking for a new one.
    """
    # Prepare
    original = mcp_tools.create_vibe_mcp_http_client

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, request=request)

    def build_client(
        headers: dict[str, str] | None, *, auth: httpx2.Auth | None = None
    ) -> httpx2.AsyncClient:
        client = original(headers, auth=auth)
        client._transport = httpx2.MockTransport(respond)
        return client

    # Do
    with (
        patch.object(mcp_tools, "create_vibe_mcp_http_client", build_client),
        pytest.raises(BaseException) as raised,
    ):
        await mcp_tools.list_tools_http(
            "https://mcp.example.com/mcp", startup_timeout_sec=5
        )

    # Assert
    assert is_authorization_rejection(raised.value)


@pytest.mark.asyncio
async def test_an_oauth_provider_answers_a_401_before_it_reads_as_a_rejection() -> None:
    """*Prepare*: An auth that answers a 401 with a fresh credential.
    *Do*: Send the request through Vibe's MCP HTTP client.
    *Assert*: The retried request succeeds: the 401 reaches the auth first.
    """

    # Prepare
    class _FreshCredential(httpx2.Auth):
        async def async_auth_flow(
            self, request: httpx2.Request
        ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
            response = yield request
            if response.status_code == httpx2.codes.UNAUTHORIZED:
                request.headers["Authorization"] = "Bearer fresh"
                yield request

    def respond(request: httpx2.Request) -> httpx2.Response:
        fresh = request.headers.get("Authorization") == "Bearer fresh"
        return httpx2.Response(200 if fresh else 401, request=request)

    client = mcp_tools.create_vibe_mcp_http_client({}, auth=_FreshCredential())
    client._transport = httpx2.MockTransport(respond)

    # Do
    async with client:
        response = await client.post("https://mcp.example.com/mcp")

    # Assert
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_an_auth_is_released_after_a_failed_request() -> None:
    """*Prepare*: An auth that holds an anyio lock while its flow runs, as the
        MCP OAuth provider does, and a server that is down once.
    *Do*: Send two requests through Vibe's MCP HTTP client.
    *Assert*: The second request succeeds: the first released the lock.
    """
    # Prepare
    lock = anyio.Lock()

    class _LockingAuth(httpx2.Auth):
        async def async_auth_flow(
            self, request: httpx2.Request
        ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
            async with lock:
                yield request

    answered: list[int] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        if not answered:
            answered.append(0)
            raise httpx2.ConnectError("down", request=request)
        return httpx2.Response(200, request=request)

    client = mcp_tools.create_vibe_mcp_http_client({}, auth=_LockingAuth())
    client._transport = httpx2.MockTransport(respond)

    # Do
    async with client:
        with pytest.raises(httpx2.ConnectError):
            await client.post("https://mcp.example.com/mcp")
        response = await asyncio.wait_for(
            client.post("https://mcp.example.com/mcp"), timeout=5
        )

    # Assert
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_refused_session_end_is_not_an_error() -> None:
    """*Prepare*: A server that refuses to end a session (405 on DELETE).
    *Do*: End the session through Vibe's MCP HTTP client.
    *Assert*: The 405 comes back as a response: only a POST's HTTP error is
        raised, so the SDK's normal teardown logs no warning.
    """

    # Prepare
    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(405, request=request)

    client = mcp_tools.create_vibe_mcp_http_client({})
    client._transport = httpx2.MockTransport(respond)

    # Do
    async with client:
        response = await client.delete("https://mcp.example.com/mcp")

    # Assert
    assert response.status_code == 405
