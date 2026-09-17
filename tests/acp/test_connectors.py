from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio

from tests.stubs.fake_client import FakeClient
from tests.stubs.fake_mcp_resource import FakeMCPResource
from vibe.acp.agent import VibeAcpAgent
from vibe.acp.exceptions import (
    InvalidRequestError,
    NotImplementedMethodError,
    SessionNotFoundError,
)
from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    MCPToolSummary,
)


def build_state() -> MCPState:
    return MCPState(
        sources=[
            MCPSourceSummary(
                name="github",
                kind=MCPSourceKind.CONNECTOR,
                transport="streamable-http",
                status=MCPSourceStatus.CONNECTED,
                tools=[
                    MCPToolSummary(
                        name="create_issue", description="Create an issue", enabled=True
                    ),
                    MCPToolSummary(
                        name="delete_issue",
                        description="Delete an issue",
                        enabled=False,
                    ),
                ],
            ),
            MCPSourceSummary(
                name="linear",
                kind=MCPSourceKind.CONNECTOR,
                transport="streamable-http",
                status=MCPSourceStatus.NEEDS_AUTH,
                error="token expired",
            ),
            # Rides the same mcp/read payload and must not leak into the list.
            MCPSourceSummary(
                name="local-fs",
                kind=MCPSourceKind.SERVER,
                transport="stdio",
                status=MCPSourceStatus.CONNECTED,
            ),
        ],
        connector_error="bootstrap failed",
    )


# What the resource recomputes once linear finishes authenticating.
def build_mutated_state() -> MCPState:
    return MCPState(
        sources=[
            MCPSourceSummary(
                name="linear",
                kind=MCPSourceKind.CONNECTOR,
                transport="streamable-http",
                status=MCPSourceStatus.CONNECTED,
                tools=[MCPToolSummary(name="search", description="Search")],
            )
        ]
    )


# `kind` and `transport` are app-server internals and never reach the client.
def expected_connector(
    name: str,
    reachability: str,
    tools: list[dict[str, object]] | None = None,
    error: str | None = None,
    enabled: bool = True,
) -> dict[str, object]:
    return {
        "name": name,
        "enabled": enabled,
        "reachability": reachability,
        "tools": tools if tools is not None else [],
        "error": error,
    }


MUTATED_PAYLOAD = {
    "connectors": [
        expected_connector(
            "linear",
            "connected",
            [{"name": "search", "description": "Search", "enabled": True}],
        )
    ],
    "error": None,
}


@pytest_asyncio.fixture
async def connectors_agent(
    tmp_path: Path,
) -> AsyncIterator[tuple[VibeAcpAgent, str, FakeMCPResource]]:
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    response = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    session_id = response.session_id

    mcp = FakeMCPResource(
        build_state(),
        auth_url="https://auth.mistral.ai/github",
        tool_count=7,
        mutated_state=build_mutated_state(),
    )
    session = agent.sessions[session_id]
    # Swapping the session's own resource keeps the test on the ACP layer.
    session.app_server.resources.mcp = cast(object, mcp)  # pyright: ignore[reportAttributeAccessIssue]

    try:
        yield agent, session_id, mcp
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_list_returns_only_connector_sources(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, mcp = connectors_agent

    result = await agent.ext_method("connectors/list", {"sessionId": session_id})

    assert mcp.read_calls == 1
    assert result == {
        "connectors": [
            expected_connector(
                "github",
                "connected",
                [
                    {
                        "name": "create_issue",
                        "description": "Create an issue",
                        "enabled": True,
                    },
                    {
                        "name": "delete_issue",
                        "description": "Delete an issue",
                        "enabled": False,
                    },
                ],
            ),
            expected_connector("linear", "needs_auth", error="token expired"),
        ],
        "error": "bootstrap failed",
    }


@pytest.mark.asyncio
async def test_list_splits_the_flattened_status_into_two_axes(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, mcp = connectors_agent
    mcp.state = MCPState(
        sources=[
            MCPSourceSummary(
                name=status.value,
                kind=MCPSourceKind.CONNECTOR,
                transport="streamable-http",
                status=status,
            )
            for status in MCPSourceStatus
        ]
    )

    result = await agent.ext_method("connectors/list", {"sessionId": session_id})

    assert [
        (entry["name"], entry["enabled"], entry["reachability"])
        for entry in cast(list[dict[str, object]], result["connectors"])
    ] == [
        # Never probed while off, so reachability is unknown rather than absent.
        ("disabled", False, "unknown"),
        ("connected", True, "connected"),
        ("enabled", True, "connected"),
        ("needs_auth", True, "needs_auth"),
        ("needs_setup", True, "needs_setup"),
        ("unavailable", True, "unavailable"),
    ]


@pytest.mark.asyncio
async def test_auth_url_delegates_to_resource(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, mcp = connectors_agent

    result = await agent.ext_method(
        "connectors/authUrl", {"sessionId": session_id, "name": "github"}
    )

    assert result == {"url": "https://auth.mistral.ai/github"}
    assert mcp.auth_url_calls == ["github"]


@pytest.mark.asyncio
async def test_refresh_answers_with_the_recomputed_list(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, mcp = connectors_agent

    result = await agent.ext_method(
        "connectors/refresh", {"sessionId": session_id, "names": ["github"]}
    )

    assert result == MUTATED_PAYLOAD
    assert mcp.refresh_calls == ["github"]
    # The mutation reports the new state; no follow-up read.
    assert mcp.read_calls == 0


@pytest.mark.asyncio
async def test_refresh_probes_every_name_and_answers_once(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, mcp = connectors_agent

    result = await agent.ext_method(
        "connectors/refresh", {"sessionId": session_id, "names": ["github", "linear"]}
    )

    assert result == MUTATED_PAYLOAD
    assert mcp.refresh_calls == ["github", "linear"]
    assert mcp.read_calls == 0


@pytest.mark.asyncio
async def test_refresh_without_names_is_an_invalid_request(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, mcp = connectors_agent

    with pytest.raises(InvalidRequestError):
        await agent.ext_method(
            "connectors/refresh", {"sessionId": session_id, "names": []}
        )

    assert mcp.refresh_calls == []


@pytest.mark.asyncio
async def test_toggle_targets_the_connector_source(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, mcp = connectors_agent

    result = await agent.ext_method(
        "connectors/toggle",
        {
            "sessionId": session_id,
            "name": "linear",
            "disabled": True,
            "toolName": "delete_issue",
        },
    )

    assert result == MUTATED_PAYLOAD
    assert mcp.toggle_calls == [("linear", "connector", True, "delete_issue")]
    assert mcp.read_calls == 0


@pytest.mark.asyncio
async def test_toggle_defaults_tool_name_to_none(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, mcp = connectors_agent

    await agent.ext_method(
        "connectors/toggle",
        {"sessionId": session_id, "name": "linear", "disabled": False},
    )

    assert mcp.toggle_calls == [("linear", "connector", False, None)]


@pytest.mark.asyncio
async def test_missing_name_is_an_invalid_request(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, _ = connectors_agent

    with pytest.raises(InvalidRequestError):
        await agent.ext_method("connectors/authUrl", {"sessionId": session_id})


@pytest.mark.asyncio
async def test_unknown_session_is_reported_as_not_found(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, _, _ = connectors_agent

    with pytest.raises(SessionNotFoundError):
        await agent.ext_method("connectors/list", {"sessionId": "nope"})


@pytest.mark.asyncio
async def test_unknown_connector_method_is_not_implemented(
    connectors_agent: tuple[VibeAcpAgent, str, FakeMCPResource],
) -> None:
    agent, session_id, _ = connectors_agent

    with pytest.raises(NotImplementedMethodError):
        await agent.ext_method("connectors/bogus", {"sessionId": session_id})
