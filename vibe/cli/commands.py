from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vibe.core.custom_commands import CustomCommandLoader


@dataclass
class Command:
    aliases: frozenset[str]
    description: str
    handler: str
    exits: bool = False
    is_custom: bool = False


class CommandRegistry:
    def __init__(
        self, excluded_commands: list[str] | None = None, commands_dir: Path | None = None
    ) -> None:
        if excluded_commands is None:
            excluded_commands = []
        self.commands = {
            "help": Command(
                aliases=frozenset(["/help"]),
                description="Show help message",
                handler="_show_help",
            ),
            "config": Command(
                aliases=frozenset(["/config", "/theme", "/model"]),
                description="Edit config settings",
                handler="_show_config",
            ),
            "reload": Command(
                aliases=frozenset(["/reload"]),
                description="Reload configuration from disk",
                handler="_reload_config",
            ),
            "clear": Command(
                aliases=frozenset(["/clear"]),
                description="Clear conversation history",
                handler="_clear_history",
            ),
            "log": Command(
                aliases=frozenset(["/log"]),
                description="Show path to current interaction log file",
                handler="_show_log_path",
            ),
            "compact": Command(
                aliases=frozenset(["/compact"]),
                description="Compact conversation history by summarizing",
                handler="_compact_history",
            ),
            "exit": Command(
                aliases=frozenset(["/exit"]),
                description="Exit the application",
                handler="_exit_app",
                exits=True,
            ),
            "terminal-setup": Command(
                aliases=frozenset(["/terminal-setup"]),
                description="Configure Shift+Enter for newlines",
                handler="_setup_terminal",
            ),
            "status": Command(
                aliases=frozenset(["/status"]),
                description="Display agent statistics",
                handler="_show_status",
            ),
        }

        for command in excluded_commands:
            self.commands.pop(command, None)

        # Load custom commands
        self._load_custom_commands(commands_dir)

        self._alias_map = {}
        for cmd_name, cmd in self.commands.items():
            for alias in cmd.aliases:
                self._alias_map[alias] = cmd_name

    def _load_custom_commands(self, commands_dir: Path | None) -> None:
        """Load custom commands from the commands directory."""
        try:
            loader = CustomCommandLoader(commands_dir)
            custom_commands = loader.load_commands()

            for cmd_name, cmd_def in custom_commands.items():
                # Create a Command object from CustomCommandDefinition
                self.commands[cmd_name] = Command(
                    aliases=frozenset(cmd_def.aliases),
                    description=cmd_def.description,
                    handler=f"_custom_{cmd_name}",  # Custom handler name
                    exits=cmd_def.exits,
                    is_custom=True,
                )
        except Exception as e:
            # Log error but don't crash if custom commands fail to load
            print(f"Warning: Failed to load custom commands: {e}")

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
            "### Built-in Commands",
            "",
        ]

        # Separate built-in and custom commands
        builtin_commands = [cmd for cmd in self.commands.values() if not cmd.is_custom]
        custom_commands = [cmd for cmd in self.commands.values() if cmd.is_custom]

        for cmd in builtin_commands:
            aliases = ", ".join(f"`{alias}`" for alias in sorted(cmd.aliases))
            lines.append(f"- {aliases}: {cmd.description}")

        if custom_commands:
            lines.append("")
            lines.append("### Custom Commands")
            lines.append("")
            for cmd in custom_commands:
                aliases = ", ".join(f"`{alias}`" for alias in sorted(cmd.aliases))
                lines.append(f"- {aliases}: {cmd.description}")

        return "\n".join(lines)
