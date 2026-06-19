from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from tests.agent_loop.e2e.conftest import MistralAPI, build_e2e_agent_loop, e2e_config
from tests.backend.data.mistral import mistral_completion
from tests.constants import CONNECTORS_BOOTSTRAP_PATH
from vibe.core.config import ConnectorConfig


def _connector(
    *,
    connector_id: str = "conn-1",
    name: str = "wiki",
    is_ready: bool = True,
    tools: list[dict[str, Any]] | None = None,
    auth_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": connector_id,
        "name": name,
        "status": {"is_ready": is_ready},
        "tools": tools or [],
        "auth_action": auth_action,
    }


def _tool(name: str = "search", description: str = "Search docs") -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }


def _set_bootstrap(router: respx.MockRouter, connectors: list[dict[str, Any]]) -> None:
    router.get(CONNECTORS_BOOTSTRAP_PATH).mock(
        return_value=httpx.Response(200, json={"connectors": connectors})
    )


def _connectors_config(*aliases: str) -> Any:
    return e2e_config(
        connectors=[ConnectorConfig(name=alias, disabled=False) for alias in aliases]
    )


async def _offered_tool_names(
    mistral_api: MistralAPI, *enabled_connectors: str
) -> set[str]:
    # Drive one turn and read which tools the AgentLoop serialized into the
    # outgoing chat-completions request — the only connector-visible surface.
    mistral_api.reply(mistral_completion("ok"))
    agent = build_e2e_agent_loop(config=_connectors_config(*enabled_connectors))
    _ = [event async for event in agent.act("hello")]
    return {t["function"]["name"] for t in mistral_api.request_json.get("tools", [])}


@pytest.mark.asyncio
async def test_connector_tools_are_offered_to_the_model(
    mistral_api: MistralAPI, mock_mistral: respx.MockRouter
) -> None:
    _set_bootstrap(
        mock_mistral, [_connector(name="wiki", tools=[_tool("search"), _tool("read")])]
    )

    names = await _offered_tool_names(mistral_api, "wiki")

    assert "connector_wiki_search" in names
    assert "connector_wiki_read" in names


@pytest.mark.asyncio
async def test_colliding_connector_aliases_are_disambiguated(
    mistral_api: MistralAPI, mock_mistral: respx.MockRouter
) -> None:
    _set_bootstrap(
        mock_mistral,
        [
            _connector(connector_id="c-1", name="mcp", tools=[_tool("a")]),
            _connector(connector_id="c-2", name="mcp", tools=[_tool("b")]),
        ],
    )

    names = await _offered_tool_names(mistral_api, "mcp", "mcp_2")

    assert "connector_mcp_a" in names
    assert "connector_mcp_2_b" in names


@pytest.mark.asyncio
async def test_not_ready_connector_offers_no_tools(
    mistral_api: MistralAPI, mock_mistral: respx.MockRouter
) -> None:
    _set_bootstrap(
        mock_mistral,
        [
            _connector(
                name="linear",
                is_ready=False,
                tools=[_tool("search")],
                auth_action={"type": "oauth"},
            )
        ],
    )

    names = await _offered_tool_names(mistral_api, "linear")

    assert not any(name.startswith("connector_linear") for name in names)


@pytest.mark.asyncio
async def test_disabled_connector_tools_are_withheld(
    mistral_api: MistralAPI, mock_mistral: respx.MockRouter
) -> None:
    _set_bootstrap(mock_mistral, [_connector(name="wiki", tools=[_tool("search")])])

    # No ConnectorConfig entry → the connector stays disabled by default.
    names = await _offered_tool_names(mistral_api)

    assert "connector_wiki_search" not in names


@pytest.mark.asyncio
async def test_bootstrap_failure_still_lets_the_agent_run(
    mistral_api: MistralAPI, mock_mistral: respx.MockRouter
) -> None:
    mock_mistral.get(CONNECTORS_BOOTSTRAP_PATH).mock(return_value=httpx.Response(500))

    names = await _offered_tool_names(mistral_api, "wiki")

    assert not any(name.startswith("connector_") for name in names)
