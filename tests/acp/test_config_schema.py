from __future__ import annotations

import pytest

from vibe.acp.agent import VibeAcpAgent


@pytest.mark.asyncio
async def test_config_schema_returns_runtime_schema(
    acp_agent_loop: VibeAcpAgent,
) -> None:
    response = await acp_agent_loop.ext_method("config/schema", {})

    assert response["version"].startswith("sha256:")
    assert acp_agent_loop.sessions == {}
    schema = response["schema"]
    assert schema["title"] == "VibeConfigSchema"
    assert {
        "active_model",
        "disabled_tools",
        "mcp_servers",
        "models",
        "providers",
    } <= schema["properties"].keys()


@pytest.mark.asyncio
async def test_config_schema_preserves_mcp_transport_discriminator(
    acp_agent_loop: VibeAcpAgent,
) -> None:
    response = await acp_agent_loop.ext_method("config/schema", {})

    discriminator = response["schema"]["properties"]["mcp_servers"]["items"][
        "discriminator"
    ]
    assert discriminator == {
        "mapping": {
            "http": "#/$defs/MCPHttp",
            "stdio": "#/$defs/MCPStdio",
            "streamable-http": "#/$defs/MCPStreamableHttp",
        },
        "propertyName": "transport",
    }
