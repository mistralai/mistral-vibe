"""Slash commands the sidecar's extensions register, surfaced in vibe's CLI.

Kept in its own module so ``vibe/cli/commands.py`` can reach the command list
without importing the agent-loop subclass (and everything it pulls in) at CLI
startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from accordion_vibe._bridge import primary_bridge

if TYPE_CHECKING:
    from accordion_vibe._bridge import AccordionBridge

__all__ = ["Severity", "extension_commands", "run_extension_command"]

# The sidecar's notify levels, already mapped onto the three severities every
# UI surface (Textual's App.notify included) understands.
Severity = Literal["information", "warning", "error"]
_SEVERITY: dict[str, Severity] = {
    "info": "information",
    "warning": "warning",
    "error": "error",
}

# The registry is built once, early, while the sidecar handshake may still be
# in flight. A short bounded wait keeps the command list correct without
# stalling the TUI; anything slower falls back to the static entry below.
_READY_WAIT_S = 1.0

# Accordion always registers /accordion. Announcing it even when the handshake
# has not landed yet keeps the command discoverable; invoking it while the
# sidecar is absent reports that plainly instead of silently doing nothing.
_FALLBACK: tuple[tuple[str, str], ...] = (
    ("accordion", "Open the Accordion context map for this session."),
)


def extension_commands() -> list[tuple[str, str]]:
    """Return ``(name, description)`` for every sidecar-registered command."""
    bridge = primary_bridge()
    if bridge is None:
        return []
    if not bridge.wait_ready(_READY_WAIT_S):
        return list(_FALLBACK)
    found = [
        (str(spec["name"]), str(spec.get("description", "")))
        for spec in bridge.command_specs()
        if spec.get("name")
    ]
    return found or list(_FALLBACK)


def run_extension_command(
    name: str, args: str = ""
) -> tuple[bool, str | None, list[tuple[str, Severity]]]:
    """Run one sidecar command. Returns ``(ok, error, notices)``.

    Blocking: the caller runs it off the UI thread. ``notices`` are the
    ``ctx.ui.notify`` messages the handler produced, in order.
    """
    bridge = primary_bridge()
    if bridge is None:
        return (False, "Accordion is not configured for this session.", [])
    if not bridge.ensure_started():
        return (False, "The Accordion sidecar is not running.", _drain(bridge))
    bridge.drain_notices()
    ok, error = bridge.run_command(name, args)
    return (ok, error, _drain(bridge))


def _drain(bridge: AccordionBridge) -> list[tuple[str, Severity]]:
    return [
        (text, _SEVERITY.get(level, "information"))
        for text, level in bridge.drain_notices()
    ]
