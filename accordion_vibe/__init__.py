"""Accordion bridge for the mistral-vibe fork.

Three in-tree seams call into this package and nothing else does:

* ``vibe/app_server/_runtime.py`` builds its ``AgentLoop`` through
  :func:`build_agent_loop`.
* ``vibe/cli/commands.py`` asks :func:`extension_commands` what extra slash
  commands to publish.
* ``vibe/cli/textual_ui/app.py`` dispatches those through
  :func:`run_extension_command`.

With neither ``ACCORDION_REPO`` nor ``[accordion] repo`` set, all three are
no-ops and vibe behaves exactly as upstream.

Attributes are resolved lazily so importing this package from the CLI's
command registry does not drag the agent loop into startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from accordion_vibe._bridge import AccordionBridge, active_bridges, primary_bridge
    from accordion_vibe._commands import extension_commands, run_extension_command
    from accordion_vibe._config import resolve_accordion_repo
    from accordion_vibe._loop import AccordionAgentLoop, build_agent_loop

__all__ = [
    "AccordionAgentLoop",
    "AccordionBridge",
    "active_bridges",
    "build_agent_loop",
    "extension_commands",
    "primary_bridge",
    "resolve_accordion_repo",
    "run_extension_command",
]

_LAZY = {
    "AccordionAgentLoop": "accordion_vibe._loop",
    "AccordionBridge": "accordion_vibe._bridge",
    "active_bridges": "accordion_vibe._bridge",
    "build_agent_loop": "accordion_vibe._loop",
    "extension_commands": "accordion_vibe._commands",
    "primary_bridge": "accordion_vibe._bridge",
    "resolve_accordion_repo": "accordion_vibe._config",
    "run_extension_command": "accordion_vibe._commands",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_name), name)
