from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Command:
    aliases: frozenset[str]
    description: str
    handler: str
    exits: bool = False


class CommandRegistry:
    def __init__(self, excluded_commands: list[str] | None = None) -> None:
        if excluded_commands is None:
            excluded_commands = []
        self.commands = {
            "help": Command(
                aliases=frozenset(["/help", "/h"]),
                description="Show help message",
                handler="_show_help",
            ),
            "status": Command(
                aliases=frozenset(["/status", "/stats"]),
                description="Display agent statistics",
                handler="_show_status",
            ),
            "config": Command(
                aliases=frozenset(["/config", "/cfg", "/theme", "/model"]),
                description="Edit config settings",
                handler="_show_config",
            ),
            "reload": Command(
                aliases=frozenset(["/reload", "/r"]),
                description="Reload configuration from disk",
                handler="_reload_config",
            ),
            "clear": Command(
                aliases=frozenset(["/clear", "/reset"]),
                description="Clear conversation history",
                handler="_clear_history",
            ),
            "log": Command(
                aliases=frozenset(["/log", "/logpath"]),
                description="Show path to current interaction log file",
                handler="_show_log_path",
            ),
            "compact": Command(
                aliases=frozenset(["/compact", "/summarize"]),
                description="Compact conversation history by summarizing",
                handler="_compact_history",
            ),
            "session": Command(
                aliases=frozenset(["/session", "/sessions"]),
                description="Session manager - list, switch, rename, and delete sessions",
                handler="_show_session_manager",
            ),
            "rename": Command(
                aliases=frozenset(["/rename"]),
                description="Rename sessions (opens session manager)",
                handler="_rename_session",
            ),
            "exit": Command(
                aliases=frozenset(["/exit", "/quit", "/q"]),
                description="Exit the application",
                handler="_exit_app",
                exits=True,
            ),
        }

        for command in excluded_commands:
            self.commands.pop(command, None)

        self._alias_map = {}
        for cmd_name, cmd in self.commands.items():
            for alias in cmd.aliases:
                self._alias_map[alias] = cmd_name

    def find_command(self, user_input: str) -> Command | None:
        cmd_name = self._alias_map.get(user_input.lower().strip())
        return self.commands.get(cmd_name) if cmd_name else None

    def get_help_text(self) -> str:
        lines: list[str] = [
            "### Keyboard Shortcuts",
            "",
            "- `Enter` Submit message",
            "- `Ctrl+J` / `Shift+Enter` Insert newline",
            "- `Escape` Interrupt agent or close dialogs",
            "- `Ctrl+C` Quit (or clear input if text present)",
            "- `Ctrl+O` Toggle tool output view",
            "- `Ctrl+T` Toggle todo view",
            "- `Shift+Tab` Toggle auto-approve mode",
            "",
            "### Special Features",
            "",
            "- `!<command>` Execute bash command directly",
            "- `@path/to/file/` Autocompletes file paths",
            "",
            "### Commands",
            "",
        ]

        for cmd in self.commands.values():
            aliases = ", ".join(f"`{alias}`" for alias in sorted(cmd.aliases))
            lines.append(f"- {aliases}: {cmd.description}")
        return "\n".join(lines)
