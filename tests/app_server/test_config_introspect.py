from __future__ import annotations

from collections.abc import Callable
import json

import pytest

from vibe.app_server._config_introspect import (
    DEFAULT_ORIGIN,
    HIDDEN_SETTINGS,
    POPULAR_SETTINGS,
    build_field_wires,
    classify_annotation,
    collect_layer_values,
)
from vibe.app_server.protocol import ConfigFieldKind, ConfigLayerValueWire
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

    assert by_name.keys() == set(type(config).model_fields) - HIDDEN_SETTINGS
    assert not HIDDEN_SETTINGS & by_name.keys()
    assert by_name["autocopy_to_clipboard"].kind is ConfigFieldKind.BOOL
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


def test_build_field_wires_keeps_admin_lock_for_global_compaction_threshold(
    make_config: Callable[..., VibeConfigSchema],
) -> None:
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
                ConfigLayerValueWire(layer="admin", value=50_000)
            ],
            "/models/custom/auto_compact_threshold": [
                ConfigLayerValueWire(layer="user-toml", value=200_000),
                ConfigLayerValueWire(layer=DEFAULT_ORIGIN, value=200_000),
            ],
        },
    )

    threshold = next(wire for wire in wires if wire.name == "auto_compact_threshold")
    assert threshold.value == 50_000
    assert threshold.path == "/auto_compact_threshold"
    assert threshold.origin == "admin"


@pytest.mark.asyncio
async def test_collect_layer_values_groups_fields_by_priority(tmp_path) -> None:
    missing = UserConfigLayer(path=tmp_path / "missing.toml", name="missing")
    low = OverridesLayer(data={"theme": "a"}, name="user-toml")
    high = OverridesLayer(data={"theme": "b", "api_timeout": 1.0}, name="overrides")

    values = await collect_layer_values([missing, low, high])

    assert [(entry.layer, entry.value) for entry in values["theme"]] == [
        ("overrides", "b"),
        ("user-toml", "a"),
    ]
    assert [(entry.layer, entry.value) for entry in values["api_timeout"]] == [
        ("overrides", 1.0)
    ]


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
        ExperimentName.CLI_MODEL_ROUTING.value: json.dumps({
            "active_model": "routed",
            "model_config": routed,
        })
    })

    values = await collect_layer_values([growthbook])
    config = make_config(
        active_model="",
        routed_default_model="routed",
        routed_model_config=json.dumps(routed),
    )
    threshold = next(
        wire
        for wire in build_field_wires(config, values)
        if wire.name == "auto_compact_threshold"
    )

    assert threshold.value == 168_000
    assert threshold.path == "/models/routed/auto_compact_threshold"
    assert threshold.origin == "growthbook"
