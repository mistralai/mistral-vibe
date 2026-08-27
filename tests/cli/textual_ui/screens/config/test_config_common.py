from __future__ import annotations

from pydantic import JsonValue

from vibe.app_server.protocol import (
    ConfigFieldKind,
    ConfigFieldWire,
    ConfigLayerValueWire,
)
from vibe.cli.textual_ui.screens.config._common import (
    filter_field_views,
    format_value,
    is_enforced,
    origin_label,
)


def _view(
    name: str,
    description: str = "",
    *,
    kind: ConfigFieldKind = ConfigFieldKind.STR,
    value: JsonValue = "",
    layer_values: list[ConfigLayerValueWire] | None = None,
) -> ConfigFieldWire:
    return ConfigFieldWire(
        name=name,
        kind=kind,
        description=description,
        value=value,
        path=f"/{name}",
        layer_values=layer_values or [],
    )


def test_filter_boost_wins_near_ties() -> None:
    views = [_view("foobar"), _view("foobare")]
    ordered = filter_field_views(views, "foobar", boost_names=frozenset({"foobare"}))
    assert ordered[0].name == "foobare"


def test_filter_boost_does_not_bury_strong_match() -> None:
    # Exact name hit on an unboosted field beats a weak description hit on a
    # boosted field.
    views = [
        _view("otel_redaction"),
        _view("default_agent", description="the redaction agent profile"),
    ]
    ordered = filter_field_views(
        views, "redaction", boost_names=frozenset({"default_agent"})
    )
    assert ordered[0].name == "otel_redaction"


def test_filter_field_views_fuzzy_matches_name() -> None:
    views = [
        _view("theme", "Colors"),
        _view("api_timeout", "network delay", kind=ConfigFieldKind.FLOAT, value=1),
    ]
    assert [v.name for v in filter_field_views(views, "the")] == ["theme"]
    assert [v.name for v in filter_field_views(views, "timeout")] == ["api_timeout"]
    # Non-contiguous subsequence still matches (a-p-t-o -> api_timeout).
    assert [v.name for v in filter_field_views(views, "apto")] == ["api_timeout"]
    assert filter_field_views(views, "") == views


def test_filter_field_views_searches_description() -> None:
    views = [
        _view("theme", "Colors"),
        _view("api_timeout", "network delay", kind=ConfigFieldKind.FLOAT, value=1),
    ]
    assert [v.name for v in filter_field_views(views, "network")] == ["api_timeout"]


def test_filter_field_views_ranks_name_match_above_description() -> None:
    views = [
        _view("color_depth", "terminal colors", kind=ConfigFieldKind.INT, value=8),
        _view("theme", "color scheme"),
    ]
    # "color" hits color_depth by name and theme by description; name wins.
    assert [v.name for v in filter_field_views(views, "color")][0] == "color_depth"


def test_is_enforced_true_when_admin_is_top_layer() -> None:
    view = _view(
        "theme",
        layer_values=[
            ConfigLayerValueWire(layer="admin", value="dark"),
            ConfigLayerValueWire(layer="user-toml", value="light"),
        ],
    )
    assert is_enforced(view) is True


def test_is_enforced_false_when_admin_is_shadowed_or_absent() -> None:
    shadowed = _view(
        "theme",
        layer_values=[
            ConfigLayerValueWire(layer="overrides", value="dark"),
            ConfigLayerValueWire(layer="admin", value="light"),
        ],
    )
    plain = _view("theme", layer_values=[ConfigLayerValueWire(layer="default")])
    assert is_enforced(shadowed) is False
    assert is_enforced(plain) is False


def test_origin_label_admin() -> None:
    assert origin_label("admin") == "your administrator"


def test_format_value_uses_labels_when_provided() -> None:
    labels = {"": "default (currently target-testing-model-alias)"}
    assert format_value("", labels) == "default (currently target-testing-model-alias)"
    # Unlabelled values fall through to the normal formatting.
    assert (
        format_value("target-testing-model-alias", labels)
        == "target-testing-model-alias"
    )
    assert format_value("", None) == '""'


def test_format_value_renders_kinds() -> None:
    assert format_value(True) == "True"
    assert format_value(False) == "False"
    assert format_value("") == '""'
    assert format_value("hi") == "hi"
    assert format_value([1, 2, 3]) == "[3 items]"
    assert format_value([1]) == "[1 item]"
    assert format_value({"a": 1, "b": 2}) == "{2 entries}"
    assert format_value({"a": 1}) == "{1 entry}"
    assert format_value(None) == "—"
    assert format_value(42) == "42"
