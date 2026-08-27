from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from unittest.mock import AsyncMock, patch

import httpx
from mcp.client.auth import OAuthFlowError
import pytest

from vibe.core.config import MCPOAuth, MCPStreamableHttp
from vibe.core.tools.base import BaseToolConfig, InvokeContext, ToolError
from vibe.core.tools.mcp import MCPRegistry, MCPToolResult, RemoteTool
from vibe.core.tools.mcp.authorization import (
    MCPAuthorizationRef,
    MCPAuthorizationRequired,
    MCPAuthorizationResult,
    MCPAuthorizationSnapshot,
)
from vibe.core.tools.mcp.tools import (
    MCPHttpAuthorizationRuntime,
    _OpenArgs,
    create_mcp_http_proxy_tool_class,
    is_authorization_rejection,
)


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


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mcp.example.com/mcp")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


def _unauthorized() -> httpx.HTTPStatusError:
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
    async def test_live_proxy_rejection_marks_source_before_emitting_signal(
        self,
    ) -> None:
        """*Prepare*: A discovered legacy proxy whose current credential is rejected.
        *Do*: Invoke the proxy and observe the authorization-required sink.
        *Assert*: The registry is already NEEDS_AUTH when the event is published.
        """
        # Prepare
        required = MCPAuthorizationRequired(
            reason="rejected",
            descriptor_revision="descriptor-2",
            observed_connection_revision="connection-1",
        )
        provider = FakeAuthorizationProvider(_snapshot(), rejected=required)
        observed_states: list[tuple[set[str], str]] = []
        registry = MCPRegistry()

        async def observe_state(
            _name: str, _required: MCPAuthorizationRequired
        ) -> None:
            observed_states.append((
                registry.needs_auth,
                registry.descriptor_revision("linear"),
            ))

        sink = AsyncMock(side_effect=observe_state)
        _configure(registry, provider, sink=sink)
        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            new=AsyncMock(return_value=[RemoteTool(name="search")]),
        ):
            tools = await registry.get_tools_async([_oauth_server()])
        tool = tools["linear_search"].from_config(lambda: BaseToolConfig())

        # Do
        with (
            patch(
                "vibe.core.tools.mcp.tools.call_tool_http",
                new=AsyncMock(side_effect=_unauthorized()),
            ),
            pytest.raises(ToolError, match="rejected authentication"),
        ):
            async for _ in tool.run(
                _OpenArgs(), InvokeContext(tool_call_id="tool-call")
            ):
                pass

        # Assert
        assert observed_states == [({"linear"}, "descriptor-2")]
        assert registry.needs_auth == {"linear"}
        assert registry.descriptor_revision("linear") == "descriptor-2"
        sink.assert_awaited_once_with("linear", required)


class TestMCPHttpAuthorizationProxy:
    @pytest.mark.asyncio
    async def test_proxy_resolves_authorization_for_each_call(self) -> None:
        provider = FakeAuthorizationProvider(_snapshot())
        tool_cls = create_mcp_http_proxy_tool_class(
            url="https://mcp.example.com/mcp",
            remote=RemoteTool(name="search"),
            alias="linear",
            authorization_runtime=MCPHttpAuthorizationRuntime(
                provider=provider, reference=_reference()
            ),
        )
        tool = tool_cls.from_config(lambda: BaseToolConfig())
        call = AsyncMock(return_value=MCPToolResult(server="linear", tool="search"))

        with patch("vibe.core.tools.mcp.tools.call_tool_http", new=call):
            results = [
                result
                async for result in tool.run(
                    _OpenArgs(), InvokeContext(tool_call_id="tc")
                )
            ]

        assert results == [MCPToolResult(server="linear", tool="search")]
        await_args = call.await_args
        assert await_args is not None
        assert await_args.kwargs["headers"] == {"Authorization": "Bearer token"}

    @pytest.mark.asyncio
    async def test_proxy_missing_authorization_emits_typed_signal(self) -> None:
        required = MCPAuthorizationRequired(
            reason="missing", descriptor_revision="descriptor-1"
        )
        provider = FakeAuthorizationProvider(required)
        sink = AsyncMock()
        tool_cls = create_mcp_http_proxy_tool_class(
            url="https://mcp.example.com/mcp",
            remote=RemoteTool(name="search"),
            alias="linear",
            authorization_runtime=MCPHttpAuthorizationRuntime(
                provider=provider, reference=_reference(), required_sink=sink
            ),
        )
        tool = tool_cls.from_config(lambda: BaseToolConfig())

        with pytest.raises(ToolError, match="needs re-authentication"):
            async for _ in tool.run(_OpenArgs(), InvokeContext(tool_call_id="tc")):
                pass

        sink.assert_awaited_once_with("linear", required)

    @pytest.mark.asyncio
    async def test_proxy_retries_once_for_stale_rejection(self) -> None:
        provider = FakeAuthorizationProvider(
            _snapshot(),
            rejected=_snapshot(
                "connection-2", headers={"Authorization": "Bearer replacement"}
            ),
        )
        tool_cls = create_mcp_http_proxy_tool_class(
            url="https://mcp.example.com/mcp",
            remote=RemoteTool(name="search"),
            alias="linear",
            authorization_runtime=MCPHttpAuthorizationRuntime(
                provider=provider, reference=_reference()
            ),
        )
        tool = tool_cls.from_config(lambda: BaseToolConfig())
        call = AsyncMock(
            side_effect=[_unauthorized(), MCPToolResult(server="linear", tool="search")]
        )

        with patch("vibe.core.tools.mcp.tools.call_tool_http", new=call):
            results = [
                result
                async for result in tool.run(
                    _OpenArgs(), InvokeContext(tool_call_id="tc")
                )
            ]

        assert results == [MCPToolResult(server="linear", tool="search")]
        assert call.await_args_list[1].kwargs["headers"] == {
            "Authorization": "Bearer replacement"
        }
