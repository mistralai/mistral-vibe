from __future__ import annotations

from vibe.cli.commands import Command, CommandRegistry


class TestCommandRegistry:
    def test_get_command_name_returns_canonical_name_for_alias(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/help") == "help"
        assert registry.get_command_name("/config") == "config"
        assert registry.get_command_name("/model") == "config"
        assert registry.get_command_name("/clear") == "clear"
        assert registry.get_command_name("/exit") == "exit"

    def test_get_command_name_normalizes_input(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("  /help  ") == "help"
        assert registry.get_command_name("/HELP") == "help"

    def test_get_command_name_returns_none_for_unknown(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/unknown") is None
        assert registry.get_command_name("hello") is None
        assert registry.get_command_name("") is None

    def test_find_command_returns_command_when_alias_matches(self) -> None:
        registry = CommandRegistry()
        cmd = registry.find_command("/help")
        assert cmd is not None
        assert cmd.handler == "_show_help"
        assert isinstance(cmd, Command)

    def test_find_command_returns_none_when_no_match(self) -> None:
        registry = CommandRegistry()
        assert registry.find_command("/nonexistent") is None

    def test_find_command_uses_get_command_name(self) -> None:
        """find_command and get_command_name stay in sync for same input."""
        registry = CommandRegistry()
        for alias in ["/help", "/config", "/clear", "/exit"]:
            cmd_name = registry.get_command_name(alias)
            cmd = registry.find_command(alias)
            if cmd_name is None:
                assert cmd is None
            else:
                assert cmd is not None
                assert cmd_name in registry.commands
                assert registry.commands[cmd_name] is cmd

    def test_excluded_commands_not_in_registry(self) -> None:
        registry = CommandRegistry(excluded_commands=["exit"])
        assert registry.get_command_name("/exit") is None
        assert registry.find_command("/exit") is None
        assert registry.get_command_name("/help") == "help"

    def test_resume_command_registration(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/resume") == "resume"
        assert registry.get_command_name("/continue") == "resume"
        cmd = registry.find_command("/resume")
        assert cmd is not None
        assert cmd.handler == "_show_session_picker"
