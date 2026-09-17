"""Test scaffolding, not UI: what client-e2e needs from the apps, inert without it."""

from __future__ import annotations

from vibe.cli.textual_ui.replay_harness._dialog_marker import ReplayDialogIdleMarker
from vibe.cli.textual_ui.replay_harness._idle_marker import ReplayIdleMarker
from vibe.cli.textual_ui.replay_harness._protocol import (
    footer_cwd,
    footer_pid_label,
    replaying,
    settle_busy,
)

__all__ = [
    "ReplayDialogIdleMarker",
    "ReplayIdleMarker",
    "footer_cwd",
    "footer_pid_label",
    "replaying",
    "settle_busy",
]
