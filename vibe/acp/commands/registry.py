from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto


class AcpCommandKind(StrEnum):
    HELP = auto()
    COMPACT = auto()
    RELOAD = auto()
    LOG = auto()
    MCP = auto()
    TELEPORT = auto()
    PROXY_SETUP = auto()
    RETRY = auto()
    LEANSTALL = auto()
    UNLEANSTALL = auto()
    DATA_RETENTION = auto()


@dataclass(frozen=True)
class AcpCommand:
    """Command advertised to ACP clients via available_commands_update."""

    name: str
    description: str
    kind: AcpCommandKind
    input_hint: str | None = None


@dataclass
class AcpCommandRegistry:
    """Registry of ACP commands. Notifies listeners when commands change."""

    _commands: dict[str, AcpCommand] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._commands = _build_commands()

    @property
    def commands(self) -> dict[str, AcpCommand]:
        return self._commands

    def get(self, name: str) -> AcpCommand | None:
        return self._commands.get(name)


def _build_commands() -> dict[str, AcpCommand]:
    return {
        "help": AcpCommand(
            name="help",
            description="Show available commands and keyboard shortcuts",
            kind=AcpCommandKind.HELP,
        ),
        "compact": AcpCommand(
            name="compact",
            description="Compact conversation history by summarizing. Optionally pass instructions to guide the summary",
            kind=AcpCommandKind.COMPACT,
            input_hint="Optional instructions to guide the compaction summary",
        ),
        "reload": AcpCommand(
            name="reload",
            description="Reload configuration, agent instructions, and skills from disk",
            kind=AcpCommandKind.RELOAD,
        ),
        "log": AcpCommand(
            name="log",
            description="Show path to current session log directory",
            kind=AcpCommandKind.LOG,
        ),
        "mcp": AcpCommand(
            name="mcp",
            description="Show MCP OAuth status, login guidance, or log out an OAuth MCP server",
            kind=AcpCommandKind.MCP,
            input_hint="status | login <alias> | logout <alias>",
        ),
        "teleport": AcpCommand(
            name="teleport",
            description="Teleport session to Vibe Code Web",
            kind=AcpCommandKind.TELEPORT,
        ),
        "proxy-setup": AcpCommand(
            name="proxy-setup",
            description="Configure proxy and SSL certificate settings",
            kind=AcpCommandKind.PROXY_SETUP,
            input_hint="KEY value to set, KEY to unset, or empty for help",
        ),
        "retry": AcpCommand(
            name="retry",
            description=(
                "Continue an interrupted model response; optionally pass "
                "additional instructions"
            ),
            kind=AcpCommandKind.RETRY,
            input_hint="Optional additional instructions for the continuation",
        ),
        "leanstall": AcpCommand(
            name="leanstall",
            description="Install the Lean 4 agent (leanstral)",
            kind=AcpCommandKind.LEANSTALL,
        ),
        "unleanstall": AcpCommand(
            name="unleanstall",
            description="Uninstall the Lean 4 agent",
            kind=AcpCommandKind.UNLEANSTALL,
        ),
        "data-retention": AcpCommand(
            name="data-retention",
            description="Show data retention information",
            kind=AcpCommandKind.DATA_RETENTION,
        ),
    }
