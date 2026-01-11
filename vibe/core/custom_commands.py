"""Custom slash command system for Mistral Vibe.

This module provides support for user-defined custom slash commands that can be
loaded from configuration files and executed within the Vibe CLI.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from vibe.core.paths.config_paths import VIBE_HOME


@dataclass
class CustomCommandDefinition:
    """Definition of a custom slash command."""

    name: str
    aliases: list[str]
    description: str
    command_type: str  # "bash", "prompt", "python"
    handler: str | dict[str, Any]
    exits: bool = False


class CustomCommandLoader:
    """Loads custom commands from configuration files."""

    def __init__(self, commands_dir: Path | None = None) -> None:
        """Initialize the custom command loader.

        Args:
            commands_dir: Directory containing custom command definitions.
                         Defaults to ~/.vibe/commands/
        """
        self.commands_dir = commands_dir or VIBE_HOME / "commands"

    def load_commands(self) -> dict[str, CustomCommandDefinition]:
        """Load all custom commands from the commands directory.

        Returns:
            Dictionary mapping command names to their definitions.
        """
        commands: dict[str, CustomCommandDefinition] = {}

        if not self.commands_dir.exists():
            return commands

        # Load all .toml files in the commands directory
        for command_file in self.commands_dir.glob("*.toml"):
            try:
                loaded = self._load_command_file(command_file)
                if loaded:
                    commands[loaded.name] = loaded
            except Exception as e:
                # Log error but continue loading other commands
                print(f"Warning: Failed to load command from {command_file}: {e}")

        return commands

    def _load_command_file(self, file_path: Path) -> CustomCommandDefinition | None:
        """Load a single command definition from a TOML file.

        Args:
            file_path: Path to the command definition file.

        Returns:
            CustomCommandDefinition if successful, None otherwise.
        """
        with open(file_path, "rb") as f:
            data = tomllib.load(f)

        if "command" not in data:
            return None

        cmd_data = data["command"]

        # Validate required fields
        required = ["name", "description", "type"]
        for field in required:
            if field not in cmd_data:
                raise ValueError(f"Missing required field '{field}' in {file_path}")

        # Get aliases (defaults to /name if not specified)
        aliases = cmd_data.get("aliases", [f"/{cmd_data['name']}"])
        if not isinstance(aliases, list):
            aliases = [aliases]

        # Ensure all aliases start with /
        aliases = [alias if alias.startswith("/") else f"/{alias}" for alias in aliases]

        # Get handler based on type
        command_type = cmd_data["type"]
        if command_type == "bash":
            handler = cmd_data.get("command", cmd_data.get("handler", ""))
        elif command_type == "prompt":
            handler = cmd_data.get("template", cmd_data.get("handler", ""))
        elif command_type == "python":
            handler = cmd_data.get("function", cmd_data.get("handler", ""))
        else:
            raise ValueError(f"Unknown command type '{command_type}' in {file_path}")

        return CustomCommandDefinition(
            name=cmd_data["name"],
            aliases=aliases,
            description=cmd_data["description"],
            command_type=command_type,
            handler=handler,
            exits=cmd_data.get("exits", False),
        )

    def create_example_commands(self) -> None:
        """Create example custom command files in the commands directory."""
        self.commands_dir.mkdir(parents=True, exist_ok=True)

        # Example 1: Bash command to run tests
        test_command = """# Example custom slash command: Run tests
[command]
name = "test"
aliases = ["/test", "/t"]
description = "Run the project's test suite"
type = "bash"
command = "pytest tests/ -v"
"""
        (self.commands_dir / "test.toml").write_text(test_command)

        # Example 2: Prompt template for code review
        review_command = """# Example custom slash command: Request code review
[command]
name = "review"
aliases = ["/review", "/r"]
description = "Review recent changes and provide feedback"
type = "prompt"
template = '''Please review the changes I just made. Focus on:
1. Code quality and best practices
2. Potential bugs or edge cases
3. Performance considerations
4. Security implications

Provide constructive feedback and suggestions for improvement.'''
"""
        (self.commands_dir / "review.toml").write_text(review_command)

        # Example 3: Git status command
        git_status_command = """# Example custom slash command: Git status
[command]
name = "gitstatus"
aliases = ["/gs", "/gst"]
description = "Show git status and recent commits"
type = "bash"
command = "git status && echo '\\n--- Recent Commits ---' && git log --oneline -5"
"""
        (self.commands_dir / "gitstatus.toml").write_text(git_status_command)


class CustomCommandExecutor:
    """Executes custom commands."""

    def __init__(self, workdir: Path) -> None:
        """Initialize the command executor.

        Args:
            workdir: Working directory for command execution.
        """
        self.workdir = workdir

    async def execute_bash_command(
        self, command: str, timeout: int = 30
    ) -> tuple[str, str, int]:
        """Execute a bash command.

        Args:
            command: Bash command to execute.
            timeout: Timeout in seconds.

        Returns:
            Tuple of (stdout, stderr, returncode).
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.workdir,
                executable="/bin/bash" if os.name != "nt" else None,
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "", f"Command timed out after {timeout}s", 124
        except Exception as e:
            return "", f"Error executing command: {e}", 1

    def get_prompt_text(self, template: str) -> str:
        """Get the prompt text for a prompt-type command.

        Args:
            template: Prompt template string.

        Returns:
            Formatted prompt text.
        """
        return template.strip()


def create_custom_command_handler(
    definition: CustomCommandDefinition, executor: CustomCommandExecutor
) -> Callable:
    """Create a handler function for a custom command.

    Args:
        definition: Command definition.
        executor: Command executor.

    Returns:
        Async handler function.
    """

    async def handler() -> dict[str, Any]:
        """Execute the custom command and return results."""
        if definition.command_type == "bash":
            stdout, stderr, returncode = await executor.execute_bash_command(
                definition.handler  # type: ignore
            )
            return {
                "type": "bash",
                "stdout": stdout,
                "stderr": stderr,
                "returncode": returncode,
                "command": definition.handler,
            }
        elif definition.command_type == "prompt":
            prompt_text = executor.get_prompt_text(definition.handler)  # type: ignore
            return {"type": "prompt", "text": prompt_text}
        else:
            return {
                "type": "error",
                "message": f"Unknown command type: {definition.command_type}",
            }

    return handler
