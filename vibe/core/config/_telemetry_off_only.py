"""A repository's config may turn usage telemetry off, never on.

A trusted project's ``.vibe/config.toml`` and its ``.vibe/agents/`` profiles
can come from a cloned repository, so their ``enable_telemetry = true`` must
not undo the user's opt-out. The merge drops that value and falls back to the
layers below; a ``false`` is kept. Agent profiles follow the rule wherever
they come from, so a user's own profile cannot turn telemetry on either.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

TELEMETRY_KEY = "enable_telemetry"
# Layers that may turn telemetry off but not on, with how a warning names them.
OFF_ONLY_LAYERS: Mapping[str, str] = {
    "project-toml": "The project's .vibe/config.toml",
    "agent-profile": "The active agent profile",
}

_BOOL = TypeAdapter(bool)


def ignored_telemetry_opt_in_warning(layer_name: str) -> str:
    return (
        f"{OFF_ONLY_LAYERS[layer_name]} sets enable_telemetry = true, which Vibe "
        "ignores: a project or agent config can turn usage telemetry off, never on."
    )


def sets_telemetry_on_off_only(layer_name: str, data: Mapping[str, Any]) -> bool:
    """Whether ``data``, one layer's raw values, turns telemetry on from a
    layer that may only turn it off. A value that does not read as a boolean
    is left to the schema to refuse.
    """
    if layer_name not in OFF_ONLY_LAYERS or TELEMETRY_KEY not in data:
        return False
    try:
        return _BOOL.validate_python(data[TELEMETRY_KEY])
    except ValidationError:
        return False


def without_telemetry_opt_ins(
    layers: Sequence[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """``layers`` without an off-only layer's ``enable_telemetry = true``."""
    return [
        (
            name,
            {key: value for key, value in data.items() if key != TELEMETRY_KEY}
            if sets_telemetry_on_off_only(name, data)
            else data,
        )
        for name, data in layers
    ]
