from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import build_test_app_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    ClientInfo,
    ConfigWriteOpWire,
    ConfigWriteParams,
    SessionStartParams,
)
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config import ModelConfig
from vibe.core.config.vibe_schema import VibeConfigSchema


async def _write_model_ops(
    *, config: VibeConfigSchema, ops: list[ConfigWriteOpWire]
) -> dict[str, Any]:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop(config=config)
    server = build_test_app_server(agent_loop, server_transport)
    client = AppServerClient(client_transport, run_peer=server.serve)

    try:
        await client.initialize(ClientInfo(name="config-write-test", version="1"))
        await client.notify("initialized")
        await client.request("session/start", SessionStartParams())
        response = await client.request(
            "config/write", ConfigWriteParams(session_id=agent_loop.session_id, ops=ops)
        )
    finally:
        await client_transport.close()
        await server_transport.close()
        await agent_loop.aclose()

    assert response["rejected"] is False
    persisted = await agent_loop.config_orchestrator.load_persistence_layer()
    return persisted.model_dump()["models"]


async def _write_model_field(
    *, config: VibeConfigSchema, alias: str, field: str, value: Any
) -> dict[str, Any]:
    models = await _write_model_ops(
        config=config,
        ops=[ConfigWriteOpWire(op="set", path=f"/models/{alias}/{field}", value=value)],
    )
    return models[alias]


def _routed_model() -> ModelConfig:
    return ModelConfig(
        name="glm-5.2",
        provider="mistral",
        alias="glm-5-2",
        temperature=1.0,
        thinking="off",
        supports_images=True,
    )


def _routed_config() -> VibeConfigSchema:
    return build_test_vibe_config(
        active_model="",
        routed_default_model="glm-5-2",
        routed_model_config=_routed_model().model_dump_json(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [("thinking", "low"), ("temperature", 0.7), ("supports_images", False)],
)
async def test_config_write_model_field_materializes_routed_default(
    field: str, value: Any
) -> None:
    persisted = await _write_model_field(
        config=_routed_config(), alias="glm-5-2", field=field, value=value
    )

    assert persisted[field] == value
    assert persisted["name"] == "glm-5.2"
    assert persisted["provider"] == "mistral"
    assert persisted["alias"] == "glm-5-2"
    # Only required fields + the changed field are persisted; admin-layer values
    # (temperature, input_price, etc.) are NOT baked into the user's config.
    assert set(persisted.keys()) == {"name", "provider", "alias", field}


@pytest.mark.asyncio
async def test_config_write_batch_model_fields_accumulate_into_one_upsert() -> None:
    models = await _write_model_ops(
        config=_routed_config(),
        ops=[
            ConfigWriteOpWire(op="set", path="/models/glm-5-2/thinking", value="low"),
            ConfigWriteOpWire(op="set", path="/models/glm-5-2/temperature", value=0.7),
        ],
    )
    persisted = models["glm-5-2"]

    assert persisted["thinking"] == "low"
    assert persisted["temperature"] == 0.7
    assert persisted["name"] == "glm-5.2"
    assert persisted["provider"] == "mistral"
    assert persisted["alias"] == "glm-5-2"
    assert set(persisted.keys()) == {
        "name",
        "provider",
        "alias",
        "thinking",
        "temperature",
    }


@pytest.mark.asyncio
async def test_config_write_model_field_sparse_when_model_in_durable_layer() -> None:
    # devstral-small is a built-in default, so DefaultConfigLayer reconstructs it
    # on restart: the write must stay sparse (only the changed field), never
    # materializing identity fields into the user's config.
    config = build_test_vibe_config(active_model="devstral-small")
    persisted = await _write_model_field(
        config=config, alias="devstral-small", field="thinking", value="low"
    )

    # Only the changed field is persisted; alias is back-filled from the map key.
    # Identity fields (name, provider) are NOT materialized — they come from
    # DefaultConfigLayer at merge time.
    assert persisted == {"thinking": "low", "alias": "devstral-small"}
