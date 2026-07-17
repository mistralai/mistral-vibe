from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from vibe import __version__
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop, _get_vibe_config_json_schema
from vibe.core.config import VibeConfigSchema


@pytest.fixture(autouse=True)
def clear_config_schema_cache() -> Iterator[None]:
    _get_vibe_config_json_schema.cache_clear()
    yield
    _get_vibe_config_json_schema.cache_clear()


@pytest.mark.asyncio
async def test_config_schema_returns_runtime_schema() -> None:
    response = await VibeAcpAgentLoop().ext_method("config/schema", {})

    assert response["version"] == __version__
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
async def test_config_schema_preserves_mcp_transport_discriminator() -> None:
    response = await VibeAcpAgentLoop().ext_method("config/schema", {})

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


@pytest.mark.asyncio
async def test_config_schema_is_generated_once() -> None:
    schema = {"type": "object"}
    with patch.object(
        VibeConfigSchema, "model_json_schema", return_value=schema
    ) as model_json_schema:
        agent = VibeAcpAgentLoop()

        first_response = await agent.ext_method("config/schema", {})
        second_response = await agent.ext_method("config/schema", {})

    model_json_schema.assert_called_once_with(mode="serialization", by_alias=True)
    assert (
        first_response == second_response == {"version": __version__, "schema": schema}
    )
