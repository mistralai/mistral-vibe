"""In vim Insert mode Esc enters Normal mode, so the loading line offers only Ctrl+C to interrupt."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"runtime/read": {"runtime": {"config": {"vimKeybindings": True}}}}

timeline: Timeline = ["first\r"]

screen_contains = ("Ctrl+C to interrupt",)
screen_excludes = ("Esc/Ctrl+C",)
