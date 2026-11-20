from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

import pytest

from vibe.app_server._config_introspect import (
    DEFAULT_ORIGIN,
    HIDDEN_SETTINGS,
    POPULAR_SETTINGS,
    build_field_wires,
    classify_annotation,
    collect_layer_values,
    effective_config_response,
    locking_layers,
    shadowed_writes,
)
from vibe.app_server.protocol import (
    ConfigFieldKind,
    ConfigFieldWire,
    ConfigLayerValueWire,
    ConfigShadowedWrite,
)
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.models import ModelConfig, OtelRedactionMode
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.experiments.active import ExperimentName


def test_popular_settings_are_valid_fields() -> None:
    unknown = POPULAR_SETTINGS - set(VibeConfigSchema.model_fields)
    assert not unknown, f"POPULAR_SETTINGS names not in schema: {sorted(unknown)}"


def test_hidden_settings_are_valid_fields() -> None:
    unknown = HIDDEN_SETTINGS - set(VibeConfigSchema.model_fields)
    assert not unknown, f"HIDDEN_SETTINGS names not in schema: {sorted(unknown)}"


@pytest.mark.parametrize(
    ("annotation", "kind"),
    [
        (bool, ConfigFieldKind.BOOL),
        (int, ConfigFieldKind.INT),
        (float, ConfigFieldKind.FLOAT),
        (str, ConfigFieldKind.STR),
        (str | None, ConfigFieldKind.STR),
        (list[str], ConfigFieldKind.LIST),
        (list[int], ConfigFieldKind.LIST),
        (dict[str, int], ConfigFieldKind.COMPLEX),
        (list[ModelConfig], ConfigFieldKind.COMPLEX),
        (ModelConfig | None, ConfigFieldKind.COMPLEX),
    ],
)
def test_classify_annotation(annotation: object, kind: ConfigFieldKind) -> None:
    assert classify_annotation(annotation)[0] is kind


def test_classify_enum_lists_choices() -> None:
    kind, choices = classify_annotation(OtelRedactionMode)
    assert kind is ConfigFieldKind.ENUM
    assert set(choices) == {mode.value for mode in OtelRedactionMode}


def test_build_field_wires_covers_schema_and_defaults(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config()
    by_name = {wire.name: wire for wire in build_field_wires(config, {})}

    # ``max_context_length`` is per-model only, so it arrives as a synthetic
    # row on top of the schema's own fields.
    retired = {
        name for name, info in type(config).model_fields.items() if info.deprecated
    }
    assert {"include_model_info", "include_prompt_detail"} <= retired
    assert by_name.keys() == (
        set(type(config).model_fields) - HIDDEN_SETTINGS - retired
        | {"max_context_length"}
    )
    assert not HIDDEN_SETTINGS & by_name.keys()
    assert by_name["autocopy_to_clipboard"].kind is ConfigFieldKind.BOOL
    assert by_name["show_subagent_status_list"].kind is ConfigFieldKind.BOOL
    assert by_name["show_subagent_status_list"].value is True
    assert by_name["otel_redaction"].kind is ConfigFieldKind.ENUM
    assert by_name["models"].kind is ConfigFieldKind.COMPLEX
    assert by_name["theme"].path == "/theme"
    assert all(wire.origin == DEFAULT_ORIGIN for wire in by_name.values())


def test_tracing_settings_are_public_and_described(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config()
    by_name = {wire.name: wire for wire in build_field_wires(config, {})}

    for name in ("enable_otel", "otel_endpoint", "otel_redaction"):
        assert name in by_name
        assert by_name[name].description


def test_subagent_status_list_is_public_and_described(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config()
    by_name = {wire.name: wire for wire in build_field_wires(config, {})}

    assert by_name["show_subagent_status_list"].description


def test_build_field_wires_resolves_layers(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config()
    wires = build_field_wires(
        config, {"theme": [ConfigLayerValueWire(layer="user-toml", value="dracula")]}
    )
    by_name = {wire.name: wire for wire in wires}

    # Model/theme choices are resolved client-side, so the server keeps them STR.
    assert by_name["active_model"].kind is ConfigFieldKind.STR
    assert by_name["theme"].origin == "user-toml"
    assert by_name["theme"].layer_values[-1].layer == DEFAULT_ORIGIN


@pytest.mark.parametrize("global_layer", ["user-toml", "admin"])
def test_build_field_wires_edits_the_model_own_compaction_threshold(
    make_config: Callable[..., VibeConfigSchema], global_layer: str
) -> None:
    # The model's own threshold wins over a global one from any layer.
    config = make_config(
        active_model="custom",
        auto_compact_threshold=50_000,
        models=[
            ModelConfig(
                name="custom",
                provider="mistral",
                alias="custom",
                auto_compact_threshold=200_000,
            )
        ],
    )
    wires = build_field_wires(
        config,
        {
            "auto_compact_threshold": [
                ConfigLayerValueWire(layer=global_layer, value=50_000)
            ],
            "/models/custom/auto_compact_threshold": [
                ConfigLayerValueWire(layer="user-toml", value=200_000)
            ],
        },
    )

    threshold = next(wire for wire in wires if wire.name == "auto_compact_threshold")
    assert threshold.value == 200_000
    assert threshold.path == "/models/custom/auto_compact_threshold"
    assert threshold.origin == "user-toml"


@pytest.mark.asyncio
async def test_collect_layer_values_groups_fields_by_priority(
    tmp_path, make_config: Callable[..., VibeConfigSchema]
) -> None:
    missing = UserConfigLayer(path=tmp_path / "missing.toml", name="missing")
    low = OverridesLayer(data={"theme": "a"}, name="user-toml")
    high = OverridesLayer(data={"theme": "b", "api_timeout": 1.0}, name="overrides")

    values = await collect_layer_values([missing, low, high], make_config())

    assert [(entry.layer, entry.value) for entry in values["theme"]] == [
        ("overrides", "b"),
        ("user-toml", "a"),
    ]
    assert [(entry.layer, entry.value) for entry in values["api_timeout"]] == [
        ("overrides", 1.0)
    ]


@pytest.mark.asyncio
async def test_collect_layer_values_hides_the_unset_threshold(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    # The default layer materializes None for every threshold; it means "no
    # layer set one" and must never surface as provenance.
    defaults = DefaultConfigLayer(schema=VibeConfigSchema)

    values = await collect_layer_values([defaults], make_config())

    assert "auto_compact_threshold" not in values
    assert [path for path in values if path.endswith("/auto_compact_threshold")] == [
        "/models/mistral-large-4-0/auto_compact_threshold"
    ]
    assert "/models/mistral-medium-3.5/auto_compact_threshold" not in values


@pytest.mark.asyncio
async def test_build_field_wires_projects_growthbook_routed_model_threshold(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    routed = {
        "name": "routed-model",
        "provider": "mistral",
        "alias": "routed",
        "auto_compact_threshold": 168_000,
    }
    growthbook = GrowthbookLayer()
    growthbook.set_variants({
        ExperimentName.CLI_MODEL_ROUTING.value: {
            "active_model": "routed",
            "model_config": routed,
        }
    })

    config = make_config(
        active_model="",
        routed_default_model="routed",
        routed_model_config=json.dumps(routed),
    )
    values = await collect_layer_values([growthbook], config)
    threshold = next(
        wire
        for wire in build_field_wires(config, values)
        if wire.name == "auto_compact_threshold"
    )

    assert threshold.value == 168_000
    assert threshold.path == "/models/routed/auto_compact_threshold"
    assert threshold.origin == "growthbook"


def test_build_field_wires_surfaces_the_active_model_window(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config(
        active_model="custom",
        models=[
            ModelConfig(
                name="custom",
                provider="mistral",
                alias="custom",
                max_context_length=262_144,
            )
        ],
    )
    wires = build_field_wires(
        config,
        {
            "/models/custom/max_context_length": [
                ConfigLayerValueWire(layer="user-toml", value=262_144)
            ]
        },
    )

    window = next(wire for wire in wires if wire.name == "max_context_length")
    assert window.value == 262_144
    assert window.path == "/models/custom/max_context_length"
    assert window.origin == "user-toml"
    assert window.kind is ConfigFieldKind.INT


def test_build_field_wires_shows_an_unset_window_as_default(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    config = make_config(
        active_model="custom",
        models=[ModelConfig(name="custom", provider="mistral", alias="custom")],
    )

    window = next(
        wire
        for wire in build_field_wires(config, {})
        if wire.name == "max_context_length"
    )

    assert window.value is None
    assert window.origin == DEFAULT_ORIGIN
    assert window.path == "/models/custom/max_context_length"


def test_build_field_wires_shows_an_unset_threshold_as_default(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
    # Nothing is set anywhere: the row shows no value, and the session derives
    # the threshold from the window when it starts.
    config = make_config(
        active_model="custom",
        models=[
            ModelConfig(
                name="custom",
                provider="mistral",
                alias="custom",
                max_context_length=262_144,
            )
        ],
    )

    threshold = next(
        wire
        for wire in build_field_wires(config, {})
        if wire.name == "auto_compact_threshold"
    )

    assert threshold.value is None
    assert threshold.origin == DEFAULT_ORIGIN


def test_effective_config_hides_credentials_and_applies_approval_bypass() -> None:
    """*Prepare*: A config with header and environment secrets.
    *Do*: Build the effective-config response for a session that bypasses
    tool approval.
    *Assert*: Secret values are redacted and the session's bypass wins.
    """
    # Prepare
    config = VibeConfigSchema.model_validate({
        "providers": [
            {
                "name": "custom",
                "api_base": "https://example.invalid/v1",
                "extra_headers": {"Authorization": "Bearer secret"},
            }
        ],
        "mcp_servers": [
            {
                "name": "local",
                "transport": "stdio",
                "command": "server",
                "env": {"TOKEN": "secret"},
            }
        ],
    })

    # Do
    dumped: dict[str, Any] = effective_config_response(
        config, bypass_tool_permissions=True
    ).config

    # Assert
    assert "secret" not in str(dumped)
    provider = next(p for p in dumped["providers"] if p["name"] == "custom")
    assert provider["extra_headers"] == {"Authorization": "<redacted>"}
    assert dumped["mcp_servers"][0]["env"] == {"TOKEN": "<redacted>"}
    assert dumped["bypass_tool_permissions"] is True


def test_effective_config_hides_url_credentials_and_mcp_arguments() -> None:
    """*Prepare*: A config with credentials in a provider URL, an MCP URL and
    an MCP command line.
    *Do*: Build the effective-config response.
    *Assert*: The URLs keep their host and path but lose their user info and
    query string, and the MCP arguments are redacted.
    """
    # Prepare
    config = VibeConfigSchema.model_validate({
        "providers": [
            {
                "name": "custom",
                "api_base": "https://user:secret@example.invalid/v1?key=secret",
            }
        ],
        "mcp_servers": [
            {
                "name": "remote",
                "transport": "http",
                "url": "https://mcp.example.invalid/mcp?token=secret",
            },
            {
                "name": "local",
                "transport": "stdio",
                "command": "server",
                "args": ["--token", "secret"],
            },
        ],
    })

    # Do
    dumped: dict[str, Any] = effective_config_response(
        config, bypass_tool_permissions=False
    ).config

    # Assert
    assert "secret" not in str(dumped)
    provider = next(p for p in dumped["providers"] if p["name"] == "custom")
    assert provider["api_base"] == "https://<redacted>@example.invalid/v1?<redacted>"
    remote, local = dumped["mcp_servers"]
    assert remote["url"] == "https://mcp.example.invalid/mcp?<redacted>"
    assert local["args"] == ["<redacted>", "<redacted>"]
    assert dumped["vibe_base_url"] == config.vibe_base_url


@pytest.mark.parametrize(
    ("command", "redacted"),
    [
        ("npx server --api-key=secret", "npx <redacted>"),
        (
            ["npx", "server", "--token", "secret"],
            ["npx", "<redacted>", "<redacted>", "<redacted>"],
        ),
        ("server", "server"),
        (["server"], ["server"]),
    ],
)
def test_effective_config_keeps_only_the_program_of_an_mcp_command(
    command: str | list[str], redacted: str | list[str]
) -> None:
    """*Prepare*: A local MCP server whose command line may carry a credential.
    *Do*: Build the effective-config response.
    *Assert*: The command keeps its program and loses the rest, in the form it
    was written in.
    """
    # Prepare
    config = VibeConfigSchema.model_validate({
        "mcp_servers": [{"name": "local", "transport": "stdio", "command": command}]
    })

    # Do
    dumped: dict[str, Any] = effective_config_response(
        config, bypass_tool_permissions=False
    ).config

    # Assert
    assert "secret" not in str(dumped)
    assert dumped["mcp_servers"][0]["command"] == redacted


def test_effective_config_keeps_a_command_outside_mcp_servers() -> None:
    """*Prepare*: A tool config with its own ``command`` entry.
    *Do*: Build the effective-config response.
    *Assert*: Only MCP server commands are redacted, so the tool's is kept.
    """
    # Prepare
    config = VibeConfigSchema.model_validate({
        "tools": {"custom": {"command": "make test --verbose"}}
    })

    # Do
    dumped: dict[str, Any] = effective_config_response(
        config, bypass_tool_permissions=False
    ).config

    # Assert
    assert dumped["tools"]["custom"]["command"] == "make test --verbose"


@pytest.mark.asyncio
async def test_build_field_wires_edits_the_catalog_filter_model_threshold() -> None:
    """A threshold the catalog filter sets is the model's own: the row edits
    the model's entry, where an edit wins over the filter.
    """
    from vibe.core.config import build_default_orchestrator
    from vibe.core.config.layers.model_catalog import apply_model_catalog
    from vibe.core.model_catalog import parse_model_catalog_response

    orchestrator = await build_default_orchestrator()
    growthbook = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(growthbook, GrowthbookLayer)
    growthbook.set_variants({
        ExperimentName.MODEL_CATALOG.value: True,
        ExperimentName.MODEL_CATALOG_FILTER.value: {
            "default": "large",
            "settings": {"large": {"auto_compact_threshold": 50_000}},
        },
    })
    catalog = parse_model_catalog_response({
        "models": [
            {
                "id": "large",
                "label": "Large",
                "capabilities": {"function_calling": True},
            }
        ]
    })
    assert catalog is not None
    await apply_model_catalog(orchestrator, catalog)
    await orchestrator.set_field("/active_model", "", target_layer=OverridesLayer.NAME)
    config = orchestrator.config

    values = await collect_layer_values(orchestrator.layers, config)
    threshold = next(
        wire
        for wire in build_field_wires(config, values)
        if wire.name == "auto_compact_threshold"
    )

    assert config.get_active_model().alias == "large"
    assert threshold.value == 50_000
    assert threshold.path == "/models/large/auto_compact_threshold"
    assert "model_catalog_filter" not in values


async def _orchestrator_with_admin(toml_text: str) -> Any:
    from vibe.core.config import build_default_orchestrator
    from vibe.core.config.layers.admin import admin_layer

    orchestrator = await build_default_orchestrator()
    layer = admin_layer(orchestrator)
    assert layer is not None
    layer.load_managed_toml(toml_text)
    await orchestrator.reload()
    return orchestrator


def test_field_wire_serializes_origin_and_locked() -> None:
    wire = ConfigFieldWire(
        name="enable_telemetry",
        kind=ConfigFieldKind.BOOL,
        description="",
        path="/enable_telemetry",
        layer_values=[ConfigLayerValueWire(layer="admin", value=True)],
    )

    payload = wire.model_dump(mode="json", by_alias=True)

    assert payload["origin"] == "admin"
    assert payload["locked"] is False
    # An older server sends neither: the client reads the field as writable.
    older = ConfigFieldWire.model_validate({
        "name": "theme",
        "kind": "str",
        "description": "",
        "path": "/theme",
        "layerValues": [{"layer": "user-toml", "value": "dark"}],
    })
    assert (older.origin, older.locked) == ("user-toml", False)


@pytest.mark.asyncio
async def test_locking_layers_are_the_environment_and_admin_above_the_target(
    config_dir: Path,
) -> None:
    from vibe.core.config import build_default_orchestrator

    orchestrator = await build_default_orchestrator()

    assert locking_layers(orchestrator) == {"environment", "admin"}
    # The session override sits above the environment.
    assert locking_layers(orchestrator, OverridesLayer.NAME) == {"admin"}


@pytest.mark.asyncio
async def test_build_field_wires_locks_what_the_org_or_environment_sets(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_ENABLE_NOTIFICATIONS", "false")
    orchestrator = await _orchestrator_with_admin("enable_telemetry = true\n")
    values = await collect_layer_values(orchestrator.layers, orchestrator.config)

    by_name = {
        wire.name: wire
        for wire in build_field_wires(
            orchestrator.config, values, locking_layers=locking_layers(orchestrator)
        )
    }

    assert (by_name["enable_telemetry"].origin, by_name["enable_telemetry"].locked) == (
        "admin",
        True,
    )
    notifications = by_name["enable_notifications"]
    assert (notifications.origin, notifications.locked) == ("environment", True)
    assert by_name["theme"].locked is False


@pytest.mark.asyncio
async def test_shadowed_writes_name_the_fields_a_locking_layer_keeps(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator_with_admin("enable_telemetry = true\n")

    shadowed = shadowed_writes(
        orchestrator,
        [("/enable_telemetry", None), ("/theme", None), ("/models/x/thinking", None)],
    )

    assert shadowed == [ConfigShadowedWrite(path="/enable_telemetry", origin="admin")]


@pytest.mark.asyncio
async def test_a_project_telemetry_opt_in_is_not_listed_as_a_source(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    from vibe.core.config import build_default_orchestrator
    from vibe.core.trusted_folders import trusted_folders_manager

    (config_dir / "config.toml").write_text("enable_telemetry = false\n")
    project_config = tmp_working_directory / ".vibe" / "config.toml"
    project_config.parent.mkdir(parents=True, exist_ok=True)
    project_config.write_text("enable_telemetry = true\n")
    trusted_folders_manager.add_trusted(project_config.parent)
    orchestrator = await build_default_orchestrator()

    values = await collect_layer_values(orchestrator.layers, orchestrator.config)
    telemetry = {
        wire.name: wire
        for wire in build_field_wires(
            orchestrator.config, values, locking_layers=locking_layers(orchestrator)
        )
    }["enable_telemetry"]

    assert (telemetry.value, telemetry.origin, telemetry.locked) == (
        False,
        "user-toml",
        False,
    )
    assert [entry.layer for entry in telemetry.layer_values] == ["user-toml", "default"]


def test_the_settings_screen_never_shows_the_vibebot_ceiling() -> None:
    """Vibebot is behind the closed `vibe_bot` flag: its setting stays out of /config and Desktop's settings."""
    wires = build_field_wires(VibeConfigSchema(), {})

    assert "vibe_bot_max_approval_policy" not in {wire.name for wire in wires}


def test_the_config_schema_and_the_effective_config_leave_out_the_vibebot_ceiling() -> (
    None
):
    """Vibebot is behind a closed flag: `vibe config schema` and headless exports stay as they were."""
    from vibe.app_server._host import config_schema_response

    schema = config_schema_response().config_schema
    effective = effective_config_response(
        VibeConfigSchema(), bypass_tool_permissions=False
    )
    properties = schema["properties"]
    assert isinstance(properties, dict)

    for key in (
        "vibe_bot_max_approval_policy",
        "vibe_bot_local_tools",
        "vibe_bot_local_tool_max_bytes",
    ):
        assert key not in properties
        assert key not in effective.config
