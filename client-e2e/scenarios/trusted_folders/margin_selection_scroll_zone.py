"""Margin drags belong to the file list and never highlight or copy the footer."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.trusted_folders.selection_autoscroll import handshake as handshake

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_contains = ("• file00.md", "• file01.md")
clipboard_excludes = ("Trust folder", "/home/user/untrusted", "Setting will be saved")

timeline: Timeline = [
    "\x1b[<0;30;14M\x1b[<32;80;14M\x1b[<0;80;14m",
    "\x1b[<0;91;15M\x1b[<32;40;15M\x1b[<0;40;15m",
    "\x1b[<0;1;14M\x1b[<32;80;23M\x1b[<0;80;23m",
]
