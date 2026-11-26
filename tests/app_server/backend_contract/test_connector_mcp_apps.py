from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import tomllib

import httpx
import pytest
import respx
import tomli_w

from tests.app_server.backend_contract.conftest import (
    BackendContractConnection,
    connect_backend_contract_host,
    wait_for_background_integrations,
)
from tests.constants import CONNECTORS_BOOTSTRAP_PATH, MISTRAL_BASE_URL
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.events import CallbackRequested
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    CompletedEffectState,
    GenericEffectDetail,
    McpAppReference,
    PublicEffectEntry,
)
from vibe.app_server.protocol import ClientCapabilities, SessionOptions
from vibe.utils.tool_presentation import EffectCallDisplay

pytestmark = [pytest.mark.both_paths, pytest.mark.usefixtures("connectors_enabled")]

_CONNECTOR_ID = "charts-id"


@pytest.fixture
def backend_contract_session_options() -> SessionOptions:
    return SessionOptions(auto_approve=True)


_GATEWAY_URL = f"{MISTRAL_BASE_URL}/v1/connectors-gateway/{_CONNECTOR_ID}/mcp"


def _bootstrap() -> dict[str, object]:
    # Tool `_meta.ui` as the connectors bootstrap serves it:
    # integrations/integrations/schemas/api/mcp_apps.py (MCPUIToolMeta).
    return {
        "connectors": [
            {
                "id": _CONNECTOR_ID,
                "name": "charts",
                "display_name": "Charts",
                "protocol": "mcp",
                "status": {"is_ready": True},
                "tools": [
                    {
                        "name": "show_chart",
                        "description": "Show a chart",
                        "inputSchema": {"type": "object", "properties": {}},
                        "_meta": {"ui": {"resourceUri": "ui://charts/chart"}},
                    },
                    {
                        "name": "refresh_chart",
                        "description": "Refresh a chart",
                        "inputSchema": {"type": "object", "properties": {}},
                        "_meta": {
                            "ui": {
                                "resourceUri": "ui://charts/chart",
                                "visibility": ["app"],
                            }
                        },
                    },
                ],
            }
        ]
    }


def _gateway(request: httpx.Request) -> httpx.Response:
    """A connector gateway answering one MCP tool call over streamable HTTP."""
    if request.method != "POST":
        return httpx.Response(405)
    message = json.loads(request.content)
    if "id" not in message:
        return httpx.Response(202)
    results: dict[str, object] = {
        "initialize": {
            "protocolVersion": message.get("params", {}).get("protocolVersion"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "charts", "version": "1"},
        },
        # The MCP client lists tools to check structured results against them.
        "tools/list": {
            "tools": [{"name": "show_chart", "inputSchema": {"type": "object"}}]
        },
        "tools/call": {
            "content": [{"type": "text", "text": "Chart ready"}],
            "structuredContent": {"points": [1, 2, 3]},
        },
    }
    result = results[message["method"]]
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
    )


def _script(build: Callable[..., httpx.Response]) -> Callable[..., httpx.Response]:
    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "call_chart" in json.dumps(payload.get("messages", [])):
            return build("Here is the chart.")
        tools = json.dumps(payload.get("tools", []))
        # The model never sees a tool only its app may call.
        assert "refresh_chart" not in tools
        return build(
            "",
            tool_calls=[
                {
                    "id": "call_chart",
                    "index": 0,
                    "function": {
                        "name": "run_typescript",
                        "arguments": json.dumps({
                            "code": (
                                "async function main() {"
                                " return tools.connector_charts.show_chart({}); }"
                            )
                        }),
                    },
                }
            ],
        )

    return respond


@pytest.mark.asyncio
async def test_a_connector_call_names_the_mcp_app_that_renders_it(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    respx_mock: respx.MockRouter,
) -> None:
    """*Prepare*: A connector whose tool declares an MCP App, a gateway that
      answers it, and a model that calls it.
    *Do*: Run a turn.
    *Assert*: The call's public effect names the app and its connector, and
      keeps the tool's own result.
    """
    # Prepare
    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json=_bootstrap())
    )
    respx_mock.route(url=_GATEWAY_URL).mock(side_effect=_gateway)
    backend_contract_mistral_api.mock(
        side_effect=_script(backend_contract_mistral_response)
    )
    session = await backend_contract_connection.host.open_session()
    try:
        await session.resources.runtime.wait_until_ready()
        await wait_for_background_integrations()

        # Do
        _ = [event async for event in session.act("Show the chart")]

        # Assert
        [effect] = [
            entry
            for entry in session.history
            if isinstance(entry, PublicEffectEntry)
            and entry.detail.tool_name == "connector_charts.show_chart"
        ]
        assert isinstance(effect.detail, GenericEffectDetail)
        assert effect.detail.mcp_app == McpAppReference(
            resource_uri="ui://charts/chart",
            connector_id=_CONNECTOR_ID,
            connector_name="charts",
            connector_display_name="Charts",
        )
        assert isinstance(effect.state, CompletedEffectState)
        assert effect.state.output_text == "Chart ready"
        assert isinstance(effect.state.output, dict)
        assert effect.state.output["structured_content"] == {"points": [1, 2, 3]}
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_resumed_session_shows_the_mcp_apps_of_its_past_calls(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    config_dir: Path,
    tmp_path: Path,
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A stored session with a call to a connector tool that has an app.
    *Do*: Resume it from a new host.
    *Assert*: The past call names its app: the session opens on the cached
      catalog, so its routes are known before the history is shown.
    """
    # Prepare
    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json=_bootstrap())
    )
    respx_mock.route(url=_GATEWAY_URL).mock(side_effect=_gateway)
    backend_contract_mistral_api.mock(
        side_effect=_script(backend_contract_mistral_response)
    )
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {"enabled": True, "save_dir": str(tmp_path)}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(auto_approve=True),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    session = await connection.host.open_session()
    try:
        await session.resources.runtime.wait_until_ready()
        await wait_for_background_integrations()
        _ = [event async for event in session.act("Show the chart")]
        session_id = session.session_id
    finally:
        await session.close()
        await connection.host.close()

    # Do
    resumed_connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    resumed = await resumed_connection.host.resume_session(session_id)
    try:
        # Assert
        [effect] = [
            entry
            for entry in resumed.history
            if isinstance(entry, PublicEffectEntry)
            and entry.detail.tool_name == "connector_charts.show_chart"
        ]
        assert isinstance(effect.detail, GenericEffectDetail)
        assert effect.detail.mcp_app is not None
        assert effect.detail.mcp_app.resource_uri == "ui://charts/chart"
    finally:
        await resumed.close()
        await resumed_connection.host.close()


@pytest.mark.asyncio
async def test_a_connector_call_approval_reads_as_running_the_tool(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A connector tool with no wording of its own, and a model that
      calls it in a Session that asks before each tool call.
    *Do*: Run a turn, approving every call.
    *Assert*: The connector call's approval reads as running the tool.
    """
    # Prepare
    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json=_bootstrap())
    )
    respx_mock.route(url=_GATEWAY_URL).mock(side_effect=_gateway)
    backend_contract_mistral_api.mock(
        side_effect=_script(backend_contract_mistral_response)
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(),
        capabilities=ClientCapabilities(callback_kinds=["approval"]),
        process_services=process_services,
    )
    session = await connection.host.open_session()
    try:
        await session.resources.runtime.wait_until_ready()
        await wait_for_background_integrations()

        # Do
        approvals: list[ApprovalCallbackDetail] = []
        async for event in session.act("Show the chart"):
            if not isinstance(event, CallbackRequested):
                continue
            assert isinstance(event.callback.detail, ApprovalCallbackDetail)
            approvals.append(event.callback.detail)
            await session.respond_to_callback(
                event.callback.callback_id,
                ApprovalCallbackOutput(
                    decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
                ),
            )

        # Assert
        [approval] = [
            approval
            for approval in approvals
            if approval.effect.tool_name == "connector_charts.show_chart"
        ]
        assert approval.effect.display == EffectCallDisplay(
            summary="connector_charts.show_chart",
            verb="Run",
            message="connector_charts.show_chart",
            settled_verb="Ran",
            settled_message="connector_charts.show_chart",
            status_text="Waiting for approval to run connector_charts.show_chart",
        )
    finally:
        await session.close()
        await connection.host.close()
