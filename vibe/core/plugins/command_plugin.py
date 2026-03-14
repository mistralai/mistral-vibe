"""Extension point for plugins that wish to add custom slash commands.

Plugins implementing :class:`CommandPlugin` can register new commands
that will appear in the autocompletion menu and be handled by Vibe's
command system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.cli.commands import CommandRegistry


class CommandPlugin:
    """Base class for plugins that add custom slash commands."""

    async def register_commands(self, registry: CommandRegistry) -> None:
        """Register custom commands into the global command registry.

        This method is called during plugin setup, before the UI starts.
        Use it to add new commands that users can invoke via `/command`.

        Parameters
        ----------
        registry : CommandRegistry
            The central registry of all slash commands in Vibe. Call
            ``registry.commands["my_cmd"] = Command(...)` directly or use
            helper methods if available.
        """
        pass
