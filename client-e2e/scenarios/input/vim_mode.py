"""Vim keybindings edit the prompt with Normal-mode motions and operators; only Insert mode is labelled."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"runtime/read": {"runtime": {"config": {"vimKeybindings": True}}}}

ESC = "\x1b[27u"

timeline: Timeline = [
    "hello big world",
    # Esc enters Normal on the last character.
    ESC,
    # Line start, delete a word: "big world".
    "0dw",
    # Word end, append "!", back to Normal: "big! world".
    f"ea!{ESC}",
    # Plain letters never insert in Normal; `$X` deletes before the last character.
    "q$X",
]

screen_contains = ("big! word",)
screen_excludes = ("INSERT", "NORMAL")
