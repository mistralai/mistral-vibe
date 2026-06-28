from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

type OnCommandsChanged = Callable[[], Awaitable[None]]


type CommandAvailability = Callable[[bool], bool]


@dataclass(frozen=True)
class AcpCommand:
    """Command advertised to ACP clients via available_commands_update."""

    name: str
    description: str
    handler: str
    input_hint: str | None = None
    is_available: CommandAvailability | None = None


@dataclass
class AcpCommandRegistry:
    """Registry of ACP commands. Notifies listeners when commands change."""

    vibe_code_enabled: bool = False
    _commands: dict[str, AcpCommand] = field(default_factory=dict)
    _on_changed: OnCommandsChanged | None = None

    def __post_init__(self) -> None:
        if not self._commands:
            self._commands = {
                name: command
                for name, command in _build_commands().items()
                if self._is_available(command)
            }

    def _is_available(self, command: AcpCommand) -> bool:
        if command.is_available is None:
            return True
        return command.is_available(self.vibe_code_enabled)

    def set_on_changed(self, callback: OnCommandsChanged) -> None:
        self._on_changed = callback

    @property
    def commands(self) -> dict[str, AcpCommand]:
        return self._commands

    def get(self, name: str) -> AcpCommand | None:
        return self._commands.get(name)

    async def notify_changed(self) -> None:
        if self._on_changed is not None:
            await self._on_changed()


def _build_commands() -> dict[str, AcpCommand]:
    return {
        "help": AcpCommand(
            name="help",
            description="Show available commands and keyboard shortcuts",
            handler="_handle_help",
        ),
        "compact": AcpCommand(
            name="compact",
            description="Compact conversation history by summarizing. Optionally pass instructions to guide the summary",
            handler="_handle_compact",
            input_hint="Optional instructions to guide the compaction summary",
        ),
        "reload": AcpCommand(
            name="reload",
            description="Reload configuration, agent instructions, and skills from disk",
            handler="_handle_reload",
        ),
        "log": AcpCommand(
            name="log",
            description="Show path to current session log directory",
            handler="_handle_log",
        ),
        "mcp": AcpCommand(
            name="mcp",
            description="Show MCP OAuth status, login guidance, or log out an OAuth MCP server",
            handler="_handle_mcp",
            input_hint="status | login <alias> | logout <alias>",
        ),
        "teleport": AcpCommand(
            name="teleport",
            description="Teleport session to Vibe Code Web",
            handler="_handle_teleport",
            is_available=lambda vibe_code_enabled: vibe_code_enabled,
        ),
        "proxy-setup": AcpCommand(
            name="proxy-setup",
            description="Configure proxy and SSL certificate settings",
            handler="_handle_proxy_setup",
            input_hint="KEY value to set, KEY to unset, or empty for help",
        ),
        "leanstall": AcpCommand(
            name="leanstall",
            description="Install the Lean 4 agent (leanstral)",
            handler="_handle_leanstall",
        ),
        "unleanstall": AcpCommand(
            name="unleanstall",
            description="Uninstall the Lean 4 agent",
            handler="_handle_unleanstall",
        ),
        "data-retention": AcpCommand(
            name="data-retention",
            description="Show data retention information",
            handler="_handle_data_retention",
        ),
    }
