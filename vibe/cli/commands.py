from __future__ import annotations

from dataclasses import dataclass

from vibe.core.config import COMMAND_DIR


@dataclass
class Command:
    aliases: frozenset[str]
    description: str
    handler: str
    exits: bool = False


@dataclass
class CustomCommand:
    """A custom command loaded from ~/.vibe/commands/*.md"""

    name: str
    template: str

    @property
    def aliases(self) -> frozenset[str]:
        return frozenset([f"/{self.name}"])

    @property
    def description(self) -> str:
        first_line = self.template.split("\n")[0][:50]
        return f"Custom: {first_line}..." if first_line else "Custom command"

    def render(self, arguments: str) -> str:
        return self.template.replace("$ARGUMENTS", arguments)


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
            "exit": Command(
                aliases=frozenset(["/exit", "/quit", "/q"]),
                description="Exit the application",
                handler="_exit_app",
                exits=True,
            ),
        }

        for command in excluded_commands:
            self.commands.pop(command, None)

        self._alias_map: dict[str, str] = {}
        for cmd_name, cmd in self.commands.items():
            for alias in cmd.aliases:
                self._alias_map[alias] = cmd_name

        self.custom_commands: dict[str, CustomCommand] = {}
        self._load_custom_commands()

    def _load_custom_commands(self) -> None:
        if not COMMAND_DIR.path.is_dir():
            return
        for cmd_file in COMMAND_DIR.path.glob("*.md"):
            name = cmd_file.stem
            if name.startswith("_"):
                continue
            # Skip if conflicts with built-in command alias
            if f"/{name}" in self._alias_map:
                continue
            template = cmd_file.read_text()
            self.custom_commands[name] = CustomCommand(name=name, template=template)

    def find_command(self, user_input: str) -> Command | None:
        cmd_name = self._alias_map.get(user_input.lower().strip())
        return self.commands.get(cmd_name) if cmd_name else None

    def find_command_with_args(
        self, user_input: str
    ) -> tuple[Command | CustomCommand | None, str]:
        """Parse input and return (command, arguments) tuple."""
        parts = user_input.strip().split(maxsplit=1)
        cmd_str = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        # Check built-in commands first
        if cmd_name := self._alias_map.get(cmd_str):
            return (self.commands[cmd_name], args)

        # Check custom commands
        if cmd_str.startswith("/"):
            custom_name = cmd_str[1:]
            if custom_cmd := self.custom_commands.get(custom_name):
                return (custom_cmd, args)

        return (None, "")

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

        if self.custom_commands:
            lines.append("")
            lines.append("### Custom Commands")
            lines.append("")
            for cmd in self.custom_commands.values():
                lines.append(f"- `/{cmd.name}`: {cmd.description}")

        return "\n".join(lines)
