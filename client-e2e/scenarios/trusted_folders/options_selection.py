"""Selecting across all trust options preserves the first and middle labels."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.trusted_folders.repo_gate import handshake as handshake

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_contains = ("Trust full repo", "Trust folder", "Don't trust")

timeline: Timeline = [
    "\x1b[<0;38;26M\x1b[<32;88;26M\x1b[<0;88;26m",
    "\x1b[<0;88;26M\x1b[<32;38;26M\x1b[<0;38;26m",
]
