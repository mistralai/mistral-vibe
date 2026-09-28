from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from vibe.app_server.client_state import ClientSessionState
from vibe.app_server.models import MCPState
from vibe.app_server.protocol import RuntimeSnapshot

_STUDIO_URL = "https://console.mistral.ai/build/connectors?shareContext=%7B%7D"


def _snapshot(mcp: MCPState) -> RuntimeSnapshot:
    # apply_runtime reads a wide slice of the snapshot; a namespace keeps the
    # test focused on the mcp field without materializing a full RuntimeSnapshot.
    return cast(
        RuntimeSnapshot,
        SimpleNamespace(
            config=SimpleNamespace(active_model=SimpleNamespace(alias="devstral")),
            active_agent="vibe",
            agents=[],
            skills=[],
            tools=[],
            stats=SimpleNamespace(token_usage=None),
            context_window=128_000,
            issues=[],
            hooks_count=0,
            connectors=SimpleNamespace(),
            mcp=mcp,
            bypass_tool_permissions=False,
            experimental_harness=False,
        ),
    )


def _client(mcp: MCPState | None) -> ClientSessionState:
    client = ClientSessionState.__new__(ClientSessionState)
    if mcp is not None:
        client.mcp = mcp
    client.projection = cast(
        Any, SimpleNamespace(state=SimpleNamespace(session=SimpleNamespace()))
    )
    return client


def test_apply_runtime_carries_manage_url_when_snapshot_omits_it() -> None:
    client = _client(MCPState(manage_connectors_url=_STUDIO_URL))

    client.apply_runtime(_snapshot(MCPState()))

    assert client.mcp.manage_connectors_url == _STUDIO_URL


def test_apply_runtime_prefers_snapshot_manage_url() -> None:
    client = _client(MCPState(manage_connectors_url=_STUDIO_URL))
    incoming = "https://console.example/build/connectors"

    client.apply_runtime(_snapshot(MCPState(manage_connectors_url=incoming)))

    assert client.mcp.manage_connectors_url == incoming


def test_apply_runtime_on_bootstrap_has_no_manage_url() -> None:
    # Bootstrap calls apply_runtime before self.mcp exists; the carry-forward
    # must not raise on the missing attribute.
    client = _client(None)

    client.apply_runtime(_snapshot(MCPState()))

    assert client.mcp.manage_connectors_url is None
