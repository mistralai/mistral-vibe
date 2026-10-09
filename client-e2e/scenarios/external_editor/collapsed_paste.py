"""The editor sees a collapsed paste in full; left intact, it collapses again."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_CTRL_G = "\x07"
_TEXT = "\n".join(f"row {number}" for number in range(12))

# The editor prepends a prompt and checks it received the expanded paste.
env = {
    "VISUAL": (
        'sh -c \'grep -q "row 11" "$1" || exit 1;'
        ' { printf "summarize "; cat "$1"; } > "$1.new" && mv "$1.new" "$1"\' editor'
    )
}

capture_startup = False
screen_contains = {"rust": ("summarize [Pasted 73 characters]",)}
screen_excludes = {"rust": ("row 5",)}

timeline: Timeline = [paste(_TEXT), _CTRL_G]
