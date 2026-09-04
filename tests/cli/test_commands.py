from __future__ import annotations

import re

import pytest

from vibe.cli.commands import Command, CommandContext, CommandRegistry
from vibe.core.skills.builtins.vibe import SKILL as VIBE_SKILL

_SKILL_MODULE = "vibe/core/skills/builtins/vibe.py"

# A backticked token that opens with a slash, e.g. `/help` or `/loop <interval>`.
# The match stops at the first non-name character, so argument placeholders still
# yield the bare command. The lookahead must reject every name character as well
# as `/`, otherwise the regex backtracks and reads `/v1/traces` as `/v`.
_SLASH_TOKEN_RE = re.compile(r"`(/[a-z0-9][a-z0-9-]*)(?![a-z0-9\-/])")

# Slash-shaped tokens in the skill prose that are deliberately not commands:
# they stand in for the name of a user-invocable skill.
_NON_COMMAND_TOKENS = frozenset({"/skill", "/skill-name", "/word"})


class TestCommandRegistry:
    def test_get_command_name_returns_canonical_name_for_alias(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/help") == "help"
        assert registry.get_command_name("/config") == "config"
        assert registry.get_command_name("/model") == "model"
        assert registry.get_command_name("/connectors") == "mcp"
        assert registry.get_command_name("/clear") == "clear"
        assert registry.get_command_name("/new") == "clear"
        assert registry.get_command_name("/exit") == "exit"
        assert registry.get_command_name("/data-retention") == "data-retention"

    def test_get_command_name_normalizes_input(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("  /help  ") == "help"
        assert registry.get_command_name("/HELP") == "help"

    def test_get_command_name_returns_none_for_unknown(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/unknown") is None
        assert registry.get_command_name("hello") is None
        assert registry.get_command_name("") is None

    def test_parse_command_returns_command_when_alias_matches(self) -> None:
        registry = CommandRegistry()
        result = registry.parse_command("/help")
        assert result is not None
        cmd_name, cmd, cmd_args = result
        assert cmd_name == "help"
        assert cmd.handler == "_show_help"
        assert isinstance(cmd, Command)
        assert cmd_args == ""

    def test_parse_command_returns_none_when_no_match(self) -> None:
        registry = CommandRegistry()
        assert registry.parse_command("/nonexistent") is None

    def test_new_alias_clears_conversation_history(self) -> None:
        registry = CommandRegistry()

        result = registry.parse_command("/new")

        assert result == ("clear", registry.commands["clear"], "")
        assert registry.commands["clear"].handler == "_clear_history"

    def test_clear_command_accepts_optional_prompt(self) -> None:
        registry = CommandRegistry()

        result = registry.parse_command("/clear fix the bug")

        assert result is not None
        cmd_name, cmd, cmd_args = result
        assert cmd_name == "clear"
        assert cmd.handler == "_clear_history"
        assert cmd_args == "fix the bug"

    def test_new_alias_accepts_optional_prompt(self) -> None:
        registry = CommandRegistry()

        result = registry.parse_command("/new hello")

        assert result is not None
        cmd_name, _, cmd_args = result
        assert cmd_name == "clear"
        assert cmd_args == "hello"

    def test_parse_command_uses_get_command_name(self) -> None:
        """parse_command and get_command_name stay in sync for same input."""
        registry = CommandRegistry()
        for alias in ["/help", "/config", "/clear", "/exit"]:
            cmd_name = registry.get_command_name(alias)
            result = registry.parse_command(alias)
            if cmd_name is None:
                assert result is None
            else:
                assert result is not None
                found_name, found_cmd, _ = result
                assert found_name == cmd_name
                assert registry.commands[cmd_name] is found_cmd

    def test_excluded_commands_not_in_registry(self) -> None:
        registry = CommandRegistry(excluded_commands=["exit"])
        assert registry.get_command_name("/exit") is None
        assert registry.parse_command("/exit") is None
        assert registry.get_command_name("/help") == "help"

    def test_teleport_command_hidden_without_eligible_context(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/teleport") is None
        assert registry.parse_command("/teleport") is None

    def test_teleport_command_registration_uses_resolved_context(self) -> None:
        registry = CommandRegistry(context=CommandContext(vibe_code_enabled=True))
        assert registry.get_command_name("/teleport") == "teleport"
        assert registry.has_command("teleport")

    def test_teleport_command_hidden_for_unified_harness(self) -> None:
        registry = CommandRegistry(
            context=CommandContext(vibe_code_enabled=True, experimental_harness=True)
        )
        assert registry.get_command_name("/teleport") is None

    def test_teleport_command_registration_uses_latest_context(self) -> None:
        registry = CommandRegistry(context=CommandContext(vibe_code_enabled=True))
        assert registry.get_command_name("/teleport") == "teleport"

        registry.refresh(CommandContext(vibe_code_enabled=False))
        assert registry.get_command_name("/teleport") is None

    def test_teleport_help_text_uses_resolved_context(self) -> None:
        registry = CommandRegistry()
        assert "/teleport" not in registry.get_help_text()

        eligible_registry = CommandRegistry(
            context=CommandContext(vibe_code_enabled=True)
        )
        assert eligible_registry.get("teleport") is not None
        assert "/teleport" in eligible_registry.get_help_text()

    def test_vibe_code_project_command_registered_when_vibe_code_enabled(self) -> None:
        registry = CommandRegistry(context=CommandContext(vibe_code_enabled=True))

        assert registry.get_command_name("/remote-project") == "remote-project"
        result = registry.parse_command("/remote-project")
        assert result is not None
        _, cmd, cmd_args = result
        assert cmd.handler == "_vibe_code_project_command"
        assert cmd_args == ""

    def test_help_text_lists_commands_alphabetically(self) -> None:
        registry = CommandRegistry()
        commands_section = registry.get_help_text().split(
            "### Commands\n\n", maxsplit=1
        )[1]
        command_names = [
            line.split("`", maxsplit=2)[1].removeprefix("/")
            for line in commands_section.splitlines()
            if line.startswith("- ")
        ]

        assert command_names == sorted(command_names)

    def test_resume_command_registration(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/resume") == "resume"
        assert registry.get_command_name("/continue") == "resume"
        result = registry.parse_command("/resume")
        assert result is not None
        _, cmd, _ = result
        assert cmd.handler == "_show_session_picker"
        assert cmd.description == "Browse, resume, or delete saved sessions"

    def test_rename_command_registration(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/rename") == "rename"
        assert registry.get_command_name("/title") is None
        result = registry.parse_command("/rename Better title")
        assert result is not None
        _, cmd, cmd_args = result
        assert cmd.handler == "_rename_session"
        assert cmd_args == "Better title"

    def test_parse_command_keeps_args_for_no_arg_commands(self) -> None:
        registry = CommandRegistry()
        result = registry.parse_command("/help extra")
        assert result == ("help", registry.commands["help"], "extra")

    def test_parse_command_keeps_args_for_argument_commands(self) -> None:
        registry = CommandRegistry()
        result = registry.parse_command("/mcp filesystem")
        assert result == ("mcp", registry.commands["mcp"], "filesystem")

    def test_parse_command_maps_connector_alias_to_mcp(self) -> None:
        registry = CommandRegistry()
        result = registry.parse_command("/connectors filesystem")
        assert result == ("mcp", registry.commands["mcp"], "filesystem")

    def test_mcp_command_description_surfaces_auth_subcommands(self) -> None:
        registry = CommandRegistry()
        command = registry.commands["mcp"]

        assert "status" in command.description
        assert "login <alias>" in command.description
        assert "logout <alias>" in command.description

    def test_data_retention_command_registration(self) -> None:
        registry = CommandRegistry()
        result = registry.parse_command("/data-retention")
        assert result is not None
        _, cmd, _ = result
        assert cmd.handler == "_show_data_retention"

    def test_loop_command_registration(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/loop") == "loop"
        result = registry.parse_command("/loop 30s ping")
        assert result is not None
        cmd_name, cmd, cmd_args = result
        assert cmd_name == "loop"
        assert cmd.handler == "_loop_command"
        assert cmd.side_channel is False
        assert cmd_args == "30s ping"

    @pytest.mark.parametrize(
        "command_name",
        ["config", "model", "thinking", "log-level", "proxy-setup", "voice", "theme"],
    )
    def test_config_picker_commands_require_idle(self, command_name: str) -> None:
        registry = CommandRegistry()

        assert registry.commands[command_name].side_channel is False

    def test_exit_command_accepts_bare_synonyms(self) -> None:
        registry = CommandRegistry()
        for alias in ["/exit", "exit", "quit", ":q", ":quit"]:
            assert registry.get_command_name(alias) == "exit", alias
            result = registry.parse_command(alias)
            assert result is not None, alias
            cmd_name, cmd, _ = result
            assert cmd_name == "exit"
            assert cmd.handler == "_exit_app"
            assert cmd.exits is True

    def test_bare_exit_synonym_with_trailing_text_is_not_a_command(self) -> None:
        registry = CommandRegistry()
        assert registry.parse_command("exit the function early") is None
        assert registry.parse_command("quit your job") is None

    def test_bare_exit_synonym_in_multiline_message_is_not_a_command(self) -> None:
        registry = CommandRegistry()
        assert registry.parse_command("exit\nplease refactor this module") is None

    def test_slash_exit_still_parses_with_trailing_text(self) -> None:
        registry = CommandRegistry()
        result = registry.parse_command("/exit now")
        assert result is not None
        cmd_name, _, cmd_args = result
        assert cmd_name == "exit"
        assert cmd_args == "now"

    def test_exit_command_synonyms_are_case_insensitive(self) -> None:
        registry = CommandRegistry()
        for alias in ["EXIT", "Quit", "  exit  ", ":Q"]:
            assert registry.get_command_name(alias) == "exit", alias

    def test_exit_synonyms_excluded_when_command_disabled(self) -> None:
        registry = CommandRegistry(excluded_commands=["exit"])
        for alias in ["/exit", "exit", "quit", ":q", ":quit"]:
            assert registry.get_command_name(alias) is None, alias

    def test_help_text_lists_exit_synonyms(self) -> None:
        registry = CommandRegistry()
        help_text = registry.get_help_text()
        for alias in ["`/exit`", "`exit`", "`quit`", "`:q`", "`:quit`"]:
            assert alias in help_text, alias


class TestBuiltinSkillCommandDrift:
    """The builtin `vibe` skill is loaded into the model's context, so any slash
    command it names is one the model will confidently recommend. A command that
    is documented there but absent from the registry is a phantom: the user types
    it and it does nothing. These tests keep the two in sync.
    """

    @staticmethod
    def _all_aliases() -> set[str]:
        # Deliberately the unfiltered command table rather than a live registry:
        # `/paste-image` is macOS-only and `/teleport` / `/remote-project` need
        # Vibe Code, but the skill documents all three with their conditions, and
        # the guard must hold on every platform.
        return {
            alias
            for command in CommandRegistry()._build_commands().values()  # pyright: ignore[reportPrivateUsage]
            for alias in command.aliases
        }

    @staticmethod
    def _documented_commands() -> set[str]:
        found = set(_SLASH_TOKEN_RE.findall(VIBE_SKILL.prompt))
        return found - _NON_COMMAND_TOKENS

    def test_every_command_documented_in_builtin_skill_is_registered(self) -> None:
        known = self._all_aliases()
        phantoms = sorted(self._documented_commands() - known)
        assert not phantoms, (
            f"{_SKILL_MODULE} documents slash command(s) that "
            f"CommandRegistry._build_commands() does not register: "
            f"{', '.join(phantoms)}. The skill is loaded into the model's "
            f"context, so the model will recommend commands that do not exist. "
            f"Either register them or delete them from the skill text."
        )

    def test_builtin_skill_documents_the_user_facing_commands(self) -> None:
        # Guards the reverse direction: a newly registered command that never
        # makes it into the skill text is invisible to the model. Add the command
        # to the skill's "Built-in Slash Commands" section, or list its canonical
        # alias here if it is internal and deliberately undocumented.
        undocumented_by_design: frozenset[str] = frozenset()
        documented = self._documented_commands()
        missing = sorted(
            f"/{name}"
            for name in CommandRegistry()._build_commands()  # pyright: ignore[reportPrivateUsage]
            if f"/{name}" not in documented and f"/{name}" not in undocumented_by_design
        )
        assert not missing, (
            f"Command(s) registered in CommandRegistry._build_commands() but not "
            f"mentioned in {_SKILL_MODULE}: {', '.join(missing)}. The model only "
            f"knows the commands that skill names."
        )

    def test_guard_detects_a_phantom_command(self) -> None:
        # The guard is only worth having if it actually fires, and the regex has
        # to survive argument placeholders.
        assert _SLASH_TOKEN_RE.findall("- `/loop <interval> <prompt>` - Schedule") == [
            "/loop"
        ]
        assert _SLASH_TOKEN_RE.findall("Vibe appends `/v1/traces`.") == []
        assert "/terminal-setup" not in self._all_aliases()
