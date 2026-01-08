"""Test to verify that the approval app scrolls correctly with long content."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.core.config import VibeConfig
from vibe.core.tools.base import ToolPermission


class LongContentApprovalApp(App):
    """Test app with very long tool info content."""

    def compose(self) -> ComposeResult:
        yield Static("Test Container", id="test-container")

    async def on_mount(self) -> None:
        # Create a simple config
        config = VibeConfig()

        # Create approval app with very long tool args (simulating read_file with long content)
        long_args = {
            "path": "very_long_file_path_that_would_cause_the_content_to_be_very_tall.txt",
            "content": "A" * 10000,  # Very long content
        }

        # Test ASK_TIME permission with long content
        self.approval_app = ApprovalApp(
            tool_name="read_file",
            tool_args=long_args,
            workdir="/tmp",
            config=config,
            permission_type=ToolPermission.ASK_TIME,
        )

        await self.query_one("#test-container").mount(self.approval_app)


@pytest.mark.asyncio
async def test_approval_app_scrolls_with_long_content():
    """Test that approval app with long content is scrollable and input field is accessible."""
    app = LongContentApprovalApp()

    async with app.run_test() as pilot:
        # Wait for the approval app to mount and focus
        await pilot.pause(0.3)

        # Check if input widget exists and has focus
        input_widget = app.approval_app.input_widget
        assert input_widget is not None, (
            "Input widget should exist for ASK_TIME permission"
        )
        assert input_widget.has_focus, (
            "Input widget should have focus for ASK_TIME permission"
        )

        # Check that the content container is scrollable
        content_container = app.query_one("#approval-content")
        assert content_container is not None, "Content container should exist"

        # The content should be scrollable (VerticalScroll widget)
        assert content_container.__class__.__name__ == "VerticalScroll", (
            "Content container should be VerticalScroll"
        )


class LongContentApprovalAppIterations(App):
    """Test app with very long tool info content for iterations."""

    def compose(self) -> ComposeResult:
        yield Static("Test Container", id="test-container")

    async def on_mount(self) -> None:
        # Create a simple config
        config = VibeConfig()

        # Create approval app with very long tool args
        long_args = {
            "path": "very_long_file_path_that_would_cause_the_content_to_be_very_tall.txt",
            "content": "A" * 10000,  # Very long content
        }

        # Test ASK_ITERATIONS permission with long content
        self.approval_app = ApprovalApp(
            tool_name="read_file",
            tool_args=long_args,
            workdir="/tmp",
            config=config,
            permission_type=ToolPermission.ASK_ITERATIONS,
        )

        await self.query_one("#test-container").mount(self.approval_app)


@pytest.mark.asyncio
async def test_approval_app_iterations_scrolls_with_long_content():
    """Test that approval app with long content is scrollable for iterations."""
    app = LongContentApprovalAppIterations()

    async with app.run_test() as pilot:
        # Wait for the approval app to mount and focus
        await pilot.pause(0.3)

        # Check if input widget exists and has focus
        input_widget = app.approval_app.input_widget
        assert input_widget is not None, (
            "Input widget should exist for ASK_ITERATIONS permission"
        )
        assert input_widget.has_focus, (
            "Input widget should have focus for ASK_ITERATIONS permission"
        )

        # Check that the content container is scrollable
        content_container = app.query_one("#approval-content")
        assert content_container is not None, "Content container should exist"

        # The content should be scrollable (VerticalScroll widget)
        assert content_container.__class__.__name__ == "VerticalScroll", (
            "Content container should be VerticalScroll"
        )
