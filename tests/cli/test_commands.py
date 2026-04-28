from __future__ import annotations

from vibe.cli.commands import Command, CommandAvailabilityContext, CommandRegistry
from vibe.cli.plan_offer.decide_plan_offer import PlanInfo
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType


def _eligible_teleport_context() -> CommandAvailabilityContext:
    return CommandAvailabilityContext(
        vibe_code_enabled=True,
        is_active_model_mistral=True,
        plan_info=PlanInfo(
            plan_type=WhoAmIPlanType.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=False,
        ),
    )


class TestCommandRegistry:
    def test_get_command_name_returns_canonical_name_for_alias(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/help") == "help"
        assert registry.get_command_name("/config") == "config"
        assert registry.get_command_name("/model") == "model"
        assert registry.get_command_name("/connectors") == "mcp"
        assert registry.get_command_name("/clear") == "clear"
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
        registry = CommandRegistry(availability_context=_eligible_teleport_context())
        assert registry.get_command_name("/teleport") == "teleport"
        assert registry.has_command("teleport")

    def test_teleport_help_text_uses_resolved_context(self) -> None:
        registry = CommandRegistry()
        assert "/teleport" not in registry.get_help_text()

        eligible_registry = CommandRegistry(
            availability_context=_eligible_teleport_context()
        )
        assert eligible_registry.get("teleport") is not None
        assert "/teleport" in eligible_registry.get_help_text()

    def test_resume_command_registration(self) -> None:
        registry = CommandRegistry()
        assert registry.get_command_name("/resume") == "resume"
        assert registry.get_command_name("/continue") == "resume"
        result = registry.parse_command("/resume")
        assert result is not None
        _, cmd, _ = result
        assert cmd.handler == "_show_session_picker"

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

    def test_data_retention_command_registration(self) -> None:
        registry = CommandRegistry()
        result = registry.parse_command("/data-retention")
        assert result is not None
        _, cmd, _ = result
        assert cmd.handler == "_show_data_retention"
