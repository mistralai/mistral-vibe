"""Test to verify that the approval app input field gets focused correctly for time/iteration permissions."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.core.config import VibeConfig
from vibe.core.tools.base import ToolPermission


class TestApprovalApp(App):
    """Test app to verify approval app focus behavior."""

    def compose(self) -> ComposeResult:
        yield Static("Test Container", id="test-container")

    async def on_mount(self) -> None:
        # Create a simple config
        config = VibeConfig()

        # Test ASK_TIME permission (should focus input)
        self.approval_app = ApprovalApp(
            tool_name="test_tool",
            tool_args={},
            workdir="/tmp",
            config=config,
            permission_type=ToolPermission.ASK_TIME,
        )

        await self.query_one("#test-container").mount(self.approval_app)


@pytest.mark.asyncio
async def test_ask_time_permission_focuses_input():
    """Test that ASK_TIME permission focuses the input field."""
    app = TestApprovalApp()

    async with app.run_test() as pilot:
        # Wait for the approval app to mount and focus
        await pilot.pause(0.2)

        # Check if input widget exists and has focus
        input_widget = app.approval_app.input_widget
        assert input_widget is not None, (
            "Input widget should exist for ASK_TIME permission"
        )
        assert input_widget.has_focus, (
            "Input widget should have focus for ASK_TIME permission"
        )


class TestApprovalAppIterations(App):
    """Test app to verify approval app focus behavior for iterations."""

    def compose(self) -> ComposeResult:
        yield Static("Test Container", id="test-container")

    async def on_mount(self) -> None:
        # Create a simple config
        config = VibeConfig()

        # Test ASK_ITERATIONS permission (should focus input)
        self.approval_app = ApprovalApp(
            tool_name="test_tool",
            tool_args={},
            workdir="/tmp",
            config=config,
            permission_type=ToolPermission.ASK_ITERATIONS,
        )

        await self.query_one("#test-container").mount(self.approval_app)


@pytest.mark.asyncio
async def test_ask_iterations_permission_focuses_input():
    """Test that ASK_ITERATIONS permission focuses the input field."""
    app = TestApprovalAppIterations()

    async with app.run_test() as pilot:
        # Wait for the approval app to mount and focus
        await pilot.pause(0.2)

        # Check if input widget exists and has focus
        input_widget = app.approval_app.input_widget
        assert input_widget is not None, (
            "Input widget should exist for ASK_ITERATIONS permission"
        )
        assert input_widget.has_focus, (
            "Input widget should have focus for ASK_ITERATIONS permission"
        )


class TestApprovalAppAsk(App):
    """Test app to verify approval app focus behavior for regular ASK."""

    def compose(self) -> ComposeResult:
        yield Static("Test Container", id="test-container")

    async def on_mount(self) -> None:
        # Create a simple config
        config = VibeConfig()

        # Test ASK permission (should focus container, not input)
        self.approval_app = ApprovalApp(
            tool_name="test_tool",
            tool_args={},
            workdir="/tmp",
            config=config,
            permission_type=ToolPermission.ASK,
        )

        await self.query_one("#test-container").mount(self.approval_app)


@pytest.mark.asyncio
async def test_ask_permission_focuses_container():
    """Test that ASK permission focuses the container, not input field."""
    app = TestApprovalAppAsk()

    async with app.run_test() as pilot:
        # Wait for the approval app to mount and focus
        await pilot.pause(0.2)

        # Check if input widget does not exist
        input_widget = app.approval_app.input_widget
        assert input_widget is None, "Input widget should not exist for ASK permission"

        # Check if container has focus
        assert app.approval_app.has_focus, (
            "Approval app container should have focus for ASK permission"
        )
