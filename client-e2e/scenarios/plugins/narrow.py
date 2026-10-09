"""A narrow terminal clips the hint, folds long paths, wraps details, and j scrolls a tall detail."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, dropped, entry
from e2e.app_server.scenario import Timeline, resize

_LONG = entry(
    "long-description",
    "fb36cc89b5fe19578a8459ae261ae690",
    description=(
        "A deliberately long description that wraps across several lines in the"
        " detail view without overflowing the border."
    ),
    components=[
        ("skill", "alpha"),
        ("skill", "beta"),
        ("skill", "gamma"),
        ("tool", "delta"),
    ],
)

_DROPPED = dropped(
    "/opt/vibe/plugins/an-unusually-long-plugin-directory-name/plugin.json",
    "invalid manifest",
)

handshake = {"plugin_catalog/read": catalog([_LONG], [_DROPPED])}
request_methods = METHODS

timeline: Timeline = [resize(30, 48), "/plugins\r", "\r", "jjj"]

screen_contains = {"rust": ("long-description", "● Tools: delta")}
