from __future__ import annotations

from unittest.mock import AsyncMock, call, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.app import BottomApp
from vibe.cli.textual_ui.widgets.smart_auto_rules_app import (
    CURATED_ASK_RULES,
    SmartAutoRulesApp,
)

pytestmark = pytest.mark.asyncio


class SmartAutoRulesHarness(App[None]):
    def __init__(
        self,
        *,
        ask_rules: list[str] | None = None,
        allow_rules: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.rules = SmartAutoRulesApp(
            build_test_vibe_config(
                auto_mode={
                    "enabled": True,
                    "soft_deny": ask_rules or [],
                    "allow": allow_rules or [],
                }
            )
        )
        self.closed: SmartAutoRulesApp.RulesClosed | None = None

    def compose(self) -> ComposeResult:
        yield self.rules

    def on_smart_auto_rules_app_rules_closed(
        self, message: SmartAutoRulesApp.RulesClosed
    ) -> None:
        self.closed = message


async def test_curated_rule_can_be_selected_and_saved() -> None:
    app = SmartAutoRulesHarness()

    async with app.run_test() as pilot:
        option_list = app.query_one(OptionList)
        option_list.highlighted = 0
        await pilot.press("enter")
        assert CURATED_ASK_RULES[0] in app.rules.ask_rules

        option_list.highlighted = option_list.option_count - 1
        await pilot.press("enter")
        await pilot.pause()

        assert app.closed is not None
        assert CURATED_ASK_RULES[0] in app.closed.ask_rules


async def test_custom_ask_and_allow_rules_can_be_added() -> None:
    app = SmartAutoRulesHarness()

    async with app.run_test() as pilot:
        option_list = app.query_one(OptionList)
        option_list.highlighted = len(CURATED_ASK_RULES)
        await pilot.press("enter")
        await pilot.press(*"changing production access controls")
        await pilot.press("enter")

        assert "changing production access controls" in app.rules.ask_rules

        option_list.highlighted = option_list.option_count - 2
        await pilot.press("enter")
        rule_input = app.query_one(Input)
        assert rule_input.has_focus
        await pilot.press(*"pushing my own feature branch")
        await pilot.press("enter")

        assert "pushing my own feature branch" in app.rules.allow_rules


async def test_x_removes_only_custom_rules() -> None:
    custom_rule = "rotating production credentials"
    app = SmartAutoRulesHarness(
        ask_rules=[CURATED_ASK_RULES[0], custom_rule],
        allow_rules=["running local unit tests"],
    )

    async with app.run_test() as pilot:
        option_list = app.query_one(OptionList)

        option_list.highlighted = len(CURATED_ASK_RULES)
        await pilot.press("x")
        assert custom_rule not in app.rules.ask_rules
        assert CURATED_ASK_RULES[0] in app.rules.ask_rules

        option_list.highlighted = len(CURATED_ASK_RULES)
        await pilot.press("x")
        assert app.rules.allow_rules == []


async def test_escape_cancels_editor_then_saves_rules() -> None:
    app = SmartAutoRulesHarness()

    async with app.run_test() as pilot:
        option_list = app.query_one(OptionList)
        option_list.highlighted = len(CURATED_ASK_RULES)
        await pilot.press("enter")
        await pilot.press(*"unfinished rule")
        await pilot.press("escape")

        assert app.rules.ask_rules == []
        assert option_list.has_focus

        await pilot.press("escape")
        await pilot.pause()
        assert app.closed is not None


async def test_slash_command_panel_opens_and_persists_both_rule_lists() -> None:
    config = build_test_vibe_config(auto_mode={"enabled": True})
    app = build_test_vibe_app(config=config)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_smart_auto_rules()
        await pilot.pause(0.1)

        assert app._current_bottom_app == BottomApp.SmartAutoRules
        rules_app = app.query_one(SmartAutoRulesApp)
        rules_app.ask_rules.append("ask before touching production")
        rules_app.allow_rules.append("allow local tests")

        set_field = AsyncMock(return_value=[])
        with (
            patch.object(app.agent_loop.config_orchestrator, "set_field", set_field),
            patch.object(app, "_reload_config", new=AsyncMock()) as reload_config,
        ):
            await pilot.press("escape")
            await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert set_field.await_args_list == [
            call(
                "/auto_mode",
                {
                    "enabled": True,
                    "hard_deny": [],
                    "soft_deny": ["ask before touching production"],
                    "allow": ["allow local tests"],
                    "environment": [],
                    "classifier_model": None,
                },
                reason="Update Careful YOLO rules",
            )
        ]
        reload_config.assert_awaited_once()
