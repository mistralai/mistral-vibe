from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import build_test_app_server
from vibe.app_server._config_write import config_write_targets, model_config_write_ops
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    ClientInfo,
    ConfigWriteOpWire,
    ConfigWriteParams,
    SessionStartParams,
)
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config import ModelConfig, build_default_orchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.observability.logging import get_log_level_chain, set_config_log_level


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
async def test_config_write_materializes_a_model_that_is_not_active_yet() -> None:
    """*Prepare*: A routed default, and a second model no durable layer declares.
    *Do*: Pick that second model and set a field on it in one write, the way a
    pick naming a model and a thinking level does.
    *Assert*: The model is materialized with its identity. The write lands
    before it becomes the active one, and an entry holding a single field is
    the whole definition of that model once its layer stops declaring it.
    """
    config = build_test_vibe_config(
        active_model="",
        routed_default_model="glm-5-2",
        routed_model_config=_routed_model().model_dump_json(),
        routed_extra_models=[
            ModelConfig(
                name="glm-5.3", provider="mistral", alias="glm-5-3", thinking="off"
            )
        ],
    )

    models = await _write_model_ops(
        config=config,
        ops=[
            ConfigWriteOpWire(op="set", path="/active_model", value="glm-5-3"),
            ConfigWriteOpWire(op="set", path="/models/glm-5-3/thinking", value="high"),
        ],
    )

    persisted = models["glm-5-3"]
    assert persisted["thinking"] == "high"
    assert persisted["name"] == "glm-5.3"
    assert persisted["provider"] == "mistral"
    assert persisted["alias"] == "glm-5-3"


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
    # "local" is a built-in default, so DefaultConfigLayer reconstructs it
    # on restart: the write must stay sparse (only the changed field), never
    # materializing identity fields into the user's config.
    config = build_test_vibe_config(active_model="local")
    persisted = await _write_model_field(
        config=config, alias="local", field="thinking", value="low"
    )

    # Only the changed field is persisted; alias is back-filled from the map key.
    # Identity fields (name, provider) are NOT materialized — they come from
    # DefaultConfigLayer at merge time.
    assert persisted == {"thinking": "low", "alias": "local"}


@pytest.mark.asyncio
async def test_config_write_log_level_applies_to_the_running_process() -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop(config=build_test_vibe_config())
    server = build_test_app_server(agent_loop, server_transport)
    client = AppServerClient(client_transport, run_peer=server.serve)

    try:
        await client.initialize(ClientInfo(name="log-level-test", version="1"))
        await client.notify("initialized")
        await client.request("session/start", SessionStartParams())
        await client.request(
            "config/write",
            ConfigWriteParams(
                session_id=agent_loop.session_id,
                ops=[ConfigWriteOpWire(op="set", path="/log_level", value="DEBUG")],
            ),
        )
        assert get_log_level_chain().config == "DEBUG"

        await client.request(
            "config/write",
            ConfigWriteParams(
                session_id=agent_loop.session_id,
                ops=[ConfigWriteOpWire(op="remove", path="/log_level")],
            ),
        )
        assert get_log_level_chain().config is None
    finally:
        set_config_log_level(None)
        await client_transport.close()
        await server_transport.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_config_write_targets_offer_user_project_and_session(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    project_vibe_dir = tmp_working_directory / ".vibe"
    project_vibe_dir.mkdir(parents=True, exist_ok=True)
    (project_vibe_dir / "config.toml").write_text('theme = "project-theme"\n')
    trusted_folders_manager.add_trusted(project_vibe_dir)

    orchestrator = await build_default_orchestrator()

    assert config_write_targets(orchestrator) == [
        "user-toml",
        "project-toml",
        "overrides",
    ]


@pytest.mark.asyncio
async def test_config_write_targets_skip_untrusted_project(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    project_vibe_dir = tmp_working_directory / ".vibe"
    project_vibe_dir.mkdir(parents=True, exist_ok=True)
    (project_vibe_dir / "config.toml").write_text('theme = "project-theme"\n')

    orchestrator = await build_default_orchestrator()

    assert config_write_targets(orchestrator) == ["user-toml", "overrides"]


@pytest.mark.asyncio
async def test_config_write_targets_skip_undiscovered_project(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    orchestrator = await build_default_orchestrator()

    assert config_write_targets(orchestrator) == ["user-toml", "overrides"]


def test_picking_the_default_model_is_written_as_an_empty_alias() -> None:
    """*Prepare*: A configuration offering two models, pinned to one.
    *Do*: Pick the default, which the picker sends as an empty alias.
    *Assert*: It writes an empty `/active_model` instead of being refused as an
    unknown model. Empty is how this configuration says "follow the default",
    and writing it is the only way to unpin a session.
    """
    config = build_test_vibe_config(
        models=[
            ModelConfig(name="model-a", provider="mistral", alias="alpha"),
            ModelConfig(name="model-b", provider="mistral", alias="beta"),
        ],
        active_model="alpha",
    )

    ops = model_config_write_ops(config, model_alias="", reasoning_effort=None)

    assert [(op.path, op.value) for op in ops] == [("/active_model", "")]


def test_the_default_gets_the_thinking_written_with_it() -> None:
    """*Prepare*: A configuration pinned to a model that is not the default.
    *Do*: Unpin and set a thinking level in one write.
    *Assert*: The level lands on the default -- the model this write leaves
    active -- and not on the one being unpinned. Nothing sends both today, but
    this function's promise is that the two travel together.
    """
    config = build_test_vibe_config(
        models=[
            ModelConfig(name="model-a", provider="mistral", alias="alpha"),
            ModelConfig(name="model-b", provider="mistral", alias="beta"),
        ],
        active_model="beta",
    )
    default = config.resolve_default_model_alias()

    ops = model_config_write_ops(config, model_alias="", reasoning_effort="low")

    assert [(op.path, op.value) for op in ops] == [
        ("/active_model", ""),
        (f"/models/{default}/thinking", "low"),
    ]


@pytest.mark.asyncio
async def test_a_profile_owned_model_is_not_session_writable() -> None:
    """*Prepare*: A session on a model an agent profile declares, as Lean does.
    *Do*: Ask whether the session may scope that model's fields, write the
    level it would have written anyway, then leave the profile.
    *Assert*: It may not, and leaving is survivable. Lean sets the level for
    the model it declares, so the level is the profile's to give.
    """
    from vibe.app_server._session_model import (
        model_thinking_is_session_writable,
        set_session_reasoning_effort_override,
    )
    from vibe.core.agents.models import ASK, LEAN
    from vibe.core.agents.registry import apply_profile_overrides
    from vibe.core.config.layers.agent_profile import AgentProfileLayer
    from vibe.core.config.layers.default import DefaultConfigLayer
    from vibe.core.config.layers.overrides import OverridesLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    # Prepare
    overrides = OverridesLayer(data={})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[
            DefaultConfigLayer(schema=VibeConfigSchema),
            overrides,
            AgentProfileLayer(),
        ],
        default_layer_resolver=lambda: overrides,
    )
    apply_profile_overrides(orchestrator, LEAN.overrides)
    owned = orchestrator.config.get_active_model()

    # Do
    writable = model_thinking_is_session_writable(orchestrator, owned.alias)
    failures = await set_session_reasoning_effort_override(
        orchestrator, "low", reason="test"
    )
    apply_profile_overrides(orchestrator, ASK.overrides)

    # Assert
    assert writable is False
    assert failures == []
    assert owned.alias not in orchestrator.config.models
    assert orchestrator.config.get_active_model().thinking is not None


@pytest.mark.asyncio
async def test_a_model_the_session_owns_stays_session_writable() -> None:
    """*Prepare*: A session whose active model comes from the default layer.
    *Do*: Ask whether the session may scope that model's fields, and write.
    *Assert*: It may, and the level is the one the session runs.
    """
    from vibe.app_server._session_model import (
        model_thinking_is_session_writable,
        set_session_reasoning_effort_override,
    )
    from vibe.core.config.layers.agent_profile import AgentProfileLayer
    from vibe.core.config.layers.default import DefaultConfigLayer
    from vibe.core.config.layers.overrides import OverridesLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    # Prepare
    overrides = OverridesLayer(data={})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[
            DefaultConfigLayer(schema=VibeConfigSchema),
            overrides,
            AgentProfileLayer(),
        ],
        default_layer_resolver=lambda: overrides,
    )
    alias = orchestrator.config.get_active_model().alias

    # Do
    writable = model_thinking_is_session_writable(orchestrator, alias)
    failures = await set_session_reasoning_effort_override(
        orchestrator, "low", reason="test"
    )

    # Assert
    assert writable is True
    assert failures == []
    assert orchestrator.config.models[alias].thinking == "low"


@pytest.mark.asyncio
async def test_a_profile_that_sets_no_level_leaves_it_to_the_session() -> None:
    """*Prepare*: A profile that pins a model without saying how hard it thinks.
    *Do*: Scope a level to the session.
    *Assert*: It lands and it is what the model runs. Pinning a model is not
    claiming its level, and refusing here would drop the pick into the user's
    own configuration, where every other session reads it.
    """
    from vibe.app_server._session_model import (
        model_thinking_is_session_writable,
        set_session_reasoning_effort_override,
    )
    from vibe.core.agents.registry import apply_profile_overrides
    from vibe.core.config.layers.agent_profile import AgentProfileLayer
    from vibe.core.config.layers.default import DefaultConfigLayer
    from vibe.core.config.layers.overrides import OverridesLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    # Prepare
    overrides = OverridesLayer(data={})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[
            DefaultConfigLayer(schema=VibeConfigSchema),
            overrides,
            AgentProfileLayer(),
        ],
        default_layer_resolver=lambda: overrides,
    )
    apply_profile_overrides(
        orchestrator,
        {
            "active_model": "profile-pinned",
            "models": [
                {
                    "name": "profile-pinned-model",
                    "provider": "mistral",
                    "alias": "profile-pinned",
                }
            ],
        },
    )
    alias = orchestrator.config.get_active_model().alias

    # Do
    writable = model_thinking_is_session_writable(orchestrator, alias)
    failures = await set_session_reasoning_effort_override(
        orchestrator, "low", reason="test"
    )

    # Assert
    assert writable is True
    assert failures == []
    assert orchestrator.config.models[alias].thinking == "low"


@pytest.mark.asyncio
async def test_a_session_level_survives_the_layer_that_declared_its_model() -> None:
    """*Prepare*: A session on a model only a runtime layer declares, as a
    routed model is, with a level scoped to the session.
    *Do*: Drop the layer that declared it, as a refresh that stops routing does.
    *Assert*: The configuration still loads. The override is written through the
    same translation a `config/write` uses, so it carries the model's identity
    rather than becoming an entry holding one field.
    """
    from vibe.app_server._session_model import set_session_reasoning_effort_override
    from vibe.core.config.layers.default import DefaultConfigLayer
    from vibe.core.config.layers.overrides import OverridesLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    # Prepare
    routed = OverridesLayer(
        data={
            "active_model": "routed",
            "models": [
                {"name": "routed-model", "provider": "mistral", "alias": "routed"}
            ],
        },
        name="routed-source",
    )
    overrides = OverridesLayer(data={})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[DefaultConfigLayer(schema=VibeConfigSchema), routed, overrides],
        default_layer_resolver=lambda: overrides,
    )
    failures = await set_session_reasoning_effort_override(
        orchestrator, "low", reason="test"
    )
    assert failures == []
    assert orchestrator.config.models["routed"].thinking == "low"

    # Do
    orchestrator.replace_or_append_layer("routed-source", OverridesLayer(data={}))
    orchestrator.rebuild()

    # Assert
    assert orchestrator.config.models["routed"].thinking == "low"
    assert orchestrator.config.models["routed"].provider == "mistral"
