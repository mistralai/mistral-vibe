from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import platform

from vibe.cli.constants import CLIPBOARD_IMAGE_PASTE_SUPPORTED_SYSTEM


@dataclass(frozen=True)
class CommandContext:
    vibe_code_enabled: bool = False
    # Whether the backend answered a plugin catalogue read. Only the Unified
    # Harness resolves plugins, and without one there is nothing to list and
    # nothing a reload could re-pin.
    plugins_enabled: bool = False


CommandAvailability = Callable[[CommandContext], bool]


@dataclass
class Command:
    aliases: frozenset[str]
    description: str
    handler: str
    exits: bool = False
    side_channel: bool = False
    # A command that resets the conversation (e.g. /clear) supersedes any
    # prompts queued before it: at drain time the queue drops those pending
    # prompts instead of running an LLM turn on the widgets the command is
    # about to tear down.
    flushes_pending: bool = False
    is_available: CommandAvailability | None = None


class CommandRegistry:
    def __init__(
        self,
        excluded_commands: list[str] | None = None,
        context: CommandContext | None = None,
    ) -> None:
        if excluded_commands is None:
            excluded_commands = []
        self._disabled_commands = set(excluded_commands)
        self._commands: dict[str, Command] = {}
        self.refresh(context)

    def _build_commands(self) -> dict[str, Command]:
        return {
            "help": Command(
                aliases=frozenset(["/help"]),
                description="Show help message",
                handler="_show_help",
                side_channel=True,
            ),
            "config": Command(
                aliases=frozenset(["/config"]),
                description="Edit config settings",
                handler="_show_config",
                side_channel=True,
            ),
            "model": Command(
                aliases=frozenset(["/model"]),
                description="Select active model",
                handler="_show_model",
                side_channel=True,
            ),
            "thinking": Command(
                aliases=frozenset(["/thinking"]),
                description="Select thinking level",
                handler="_show_thinking",
                side_channel=True,
            ),
            "reload": Command(
                aliases=frozenset(["/reload"]),
                description="Reload configuration, agent instructions, and skills from disk",
                handler="_reload_config",
            ),
            "clear": Command(
                aliases=frozenset(["/clear", "/new"]),
                description=(
                    "Start a new conversation. Optionally pass a prompt to seed it."
                ),
                handler="_clear_history",
                flushes_pending=True,
            ),
            "copy": Command(
                aliases=frozenset(["/copy"]),
                description="Copy the last agent message to the clipboard",
                handler="_copy_last_agent_message",
                side_channel=True,
            ),
            "paste-image": Command(
                aliases=frozenset(["/paste-image"]),
                description="Paste an image from the OS clipboard into the prompt",
                handler="_paste_clipboard_image_command",
                side_channel=True,
                is_available=lambda _ctx: (
                    platform.system() == CLIPBOARD_IMAGE_PASTE_SUPPORTED_SYSTEM
                ),
            ),
            "log": Command(
                aliases=frozenset(["/log"]),
                description="Show path to current interaction log file",
                handler="_show_log_path",
                side_channel=True,
            ),
            "log-level": Command(
                aliases=frozenset(["/log-level"]),
                description=(
                    "Change the log level for this session or persist it to config.toml."
                ),
                handler="_log_level_command",
                side_channel=True,
            ),
            "debug": Command(
                aliases=frozenset(["/debug"]),
                description="Toggle debug console",
                handler="action_toggle_debug_console",
                side_channel=True,
            ),
            "compact": Command(
                aliases=frozenset(["/compact"]),
                description="Compact conversation history by summarizing. Optionally pass instructions to guide the summary",
                handler="_compact_history",
            ),
            "exit": Command(
                aliases=frozenset(["/exit", "exit", "quit", ":q", ":quit"]),
                description="Exit the application",
                handler="_exit_app",
                exits=True,
                side_channel=True,
            ),
            "status": Command(
                aliases=frozenset(["/status"]),
                description="Display agent statistics",
                handler="_show_status",
                side_channel=True,
            ),
            "whoami": Command(
                aliases=frozenset(["/whoami"]),
                description="Display the Mistral signed-in user, workspace, and plan",
                handler="_show_whoami",
                side_channel=True,
            ),
            "teleport": Command(
                aliases=frozenset(["/teleport"]),
                description="Teleport session to Vibe Code Web",
                handler="_teleport_command",
                is_available=lambda ctx: ctx.vibe_code_enabled,
            ),
            "remote-project": Command(
                aliases=frozenset(["/remote-project"]),
                description="Select the Vibe Code Web project for this repository",
                handler="_vibe_code_project_command",
                is_available=lambda ctx: ctx.vibe_code_enabled,
            ),
            "proxy-setup": Command(
                aliases=frozenset(["/proxy-setup"]),
                description="Configure proxy and SSL certificate settings",
                handler="_show_proxy_setup",
                side_channel=True,
            ),
            "resume": Command(
                aliases=frozenset(["/resume", "/continue"]),
                description="Browse, resume, or delete saved sessions",
                handler="_show_session_picker",
            ),
            "rename": Command(
                aliases=frozenset(["/rename"]),
                description="Rename the current session",
                handler="_rename_session",
                side_channel=True,
            ),
            "mcp": Command(
                aliases=frozenset(["/mcp", "/connectors"]),
                description=(
                    "Display available MCP servers and connectors. "
                    "Pass a name to list tools; subcommands: add <url> "
                    "[--transport http|streamable-http], status, login <alias>, "
                    "logout <alias>"
                ),
                handler="_show_mcp",
            ),
            # Withheld from this release: the handlers, the catalogue probe and
            # the `PluginsApp` behind them all stay wired, only the two entry
            # points are unregistered. Restore both entries to bring them back.
            # "plugins": Command(
            #     aliases=frozenset(["/plugins"]),
            #     description="Display the plugins this session is running",
            #     handler="_show_plugins",
            #     is_available=lambda ctx: ctx.plugins_enabled,
            # ),
            # "reload-plugins": Command(
            #     aliases=frozenset(["/reload-plugins"]),
            #     description="Re-pin this session's plugins and report what changed",
            #     handler="_reload_plugins",
            #     is_available=lambda ctx: ctx.plugins_enabled,
            # ),
            "voice": Command(
                aliases=frozenset(["/voice"]),
                description="Configure voice settings",
                handler="_show_voice_settings",
                side_channel=True,
            ),
            "leanstall": Command(
                aliases=frozenset(["/leanstall"]),
                description="Install the Lean 4 agent (leanstral)",
                handler="_install_lean",
            ),
            "unleanstall": Command(
                aliases=frozenset(["/unleanstall"]),
                description="Uninstall the Lean 4 agent",
                handler="_uninstall_lean",
            ),
            "rewind": Command(
                aliases=frozenset(["/rewind"]),
                description="Rewind to a previous message (or press Esc twice)",
                handler="_start_rewind_mode",
            ),
            "retry": Command(
                aliases=frozenset(["/retry"]),
                description=(
                    "Continue an interrupted model response; optionally pass "
                    "additional instructions"
                ),
                handler="_retry",
            ),
            "loop": Command(
                aliases=frozenset(["/loop"]),
                description=(
                    "Schedule a recurring prompt. "
                    "Use `/loop <interval> <prompt>`, `/loop list`, or `/loop cancel <id|all>`"
                ),
                handler="_loop_command",
            ),
            "data-retention": Command(
                aliases=frozenset(["/data-retention"]),
                description="Show data retention information",
                handler="_show_data_retention",
                side_channel=True,
            ),
            "theme": Command(
                aliases=frozenset(["/theme"]),
                description="Select theme",
                handler="_show_theme",
                side_channel=True,
            ),
        }

    @property
    def commands(self) -> dict[str, Command]:
        return self._commands

    def refresh(self, context: CommandContext | None = None) -> None:
        self._context = context or CommandContext()
        self._commands = {
            name: command
            for name, command in self._build_commands().items()
            if name not in self._disabled_commands
            and self._is_command_available(command)
        }

    def _is_command_available(self, command: Command) -> bool:
        if command.is_available is None:
            return True
        return command.is_available(self._context)

    def _alias_map(self) -> dict[str, str]:
        return {
            alias: cmd_name
            for cmd_name, cmd in self.commands.items()
            for alias in cmd.aliases
        }

    def get(self, name: str) -> Command | None:
        return self.commands.get(name)

    def has_command(self, name: str) -> bool:
        return name in self.commands

    def get_command_name(self, user_input: str) -> str | None:
        return self._alias_map().get(user_input.lower().strip())

    def parse_command(self, user_input: str) -> tuple[str, Command, str] | None:
        parts = user_input.strip().split(None, 1)
        if not parts:
            return None

        cmd_word = parts[0]
        cmd_args = parts[1] if len(parts) > 1 else ""
        cmd_name = self.get_command_name(cmd_word)
        if cmd_name is None:
            return None

        # Bare aliases (e.g. `exit`) match only as the whole input, else a
        # message starting with one would be swallowed instead of sent.
        if not cmd_word.startswith("/") and cmd_args:
            return None

        command = self.commands[cmd_name]
        return cmd_name, command, cmd_args

    def get_help_text(self) -> str:
        lines: list[str] = [
            "### Keyboard Shortcuts",
            "",
            "- `Enter` Submit message",
            "- `Ctrl+J` / `Shift+Enter` Insert newline",
            "- `Escape` Interrupt agent or close dialogs",
            "- `Ctrl+C` Quit (or clear input if text present)",
            "- `Ctrl+G` Edit input in external editor",
            "- `Ctrl+O` Toggle tool output view",
            "- `Shift+Tab` Cycle through agents (ask, plan, ...)",
            "- `Esc Esc` Rewind to a previous message (when input is empty)",
            "",
            "### Special Features",
            "",
            "- `!<command>` Execute bash command directly",
            "- `@path/to/file/` Autocompletes file paths",
            "",
            "### Commands",
            "",
        ]

        for name, cmd in sorted(self.commands.items()):
            canonical_alias = f"/{name}"
            aliases = ", ".join(
                f"`{alias}`"
                for alias in sorted(
                    cmd.aliases, key=lambda alias: (alias != canonical_alias, alias)
                )
            )
            lines.append(f"- {aliases}: {cmd.description}")
        return "\n".join(lines)
