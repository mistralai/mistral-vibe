"""Ctrl+G opens $VISUAL on the draft and loads the saved file back into the composer."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_CTRL_G = "\x07"

# The editor upper-cases the draft it receives and appends a line; $VISUAL wins over $EDITOR.
env = {
    "VISUAL": (
        'sh -c \'tr a-z A-Z < "$1" > "$1.new"'
        ' && printf "\\nsecond line\\n" >> "$1.new"'
        ' && mv "$1.new" "$1"\' editor'
    ),
    "EDITOR": "false",
}

capture_startup = False
screen_contains = {"rust": ("HELLO WORLD", "second line")}
screen_excludes = {"rust": ("hello world",)}

timeline: Timeline = ["hello world", _CTRL_G]
