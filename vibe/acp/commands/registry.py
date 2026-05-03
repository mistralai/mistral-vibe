from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

type OnCommandsChanged = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class AcpCommand:
    """Command advertised to ACP clients via available_commands_update."""

    name: str
    description: str
    handler: str
    input_hint: str | None = None


@dataclass
class AcpCommandRegistry:
    """Registry of ACP commands. Notifies listeners when commands change."""

    _commands: dict[str, AcpCommand] = field(default_factory=dict)
    _on_changed: OnCommandsChanged | None = None

    def __post_init__(self) -> None:
        if not self._commands:
            self._commands = _build_commands()

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
