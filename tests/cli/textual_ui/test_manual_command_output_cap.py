from __future__ import annotations

import time

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import BashOutputMessage
from vibe.core.types import Role


class TestCapOutput:
    """Unit tests for VibeApp._cap_output."""

    def test_short_text_unchanged(self) -> None:
        assert VibeApp._cap_output("hello", 100) == "hello"

    def test_exact_limit_unchanged(self) -> None:
        text = "a" * 50
        assert VibeApp._cap_output(text, 50) == text

    def test_over_limit_truncated(self) -> None:
        text = "a" * 100
        result = VibeApp._cap_output(text, 50)
        assert result == "a" * 50 + "\n... [truncated]"

    def test_empty_string_unchanged(self) -> None:
        assert VibeApp._cap_output("", 10) == ""


class TestFormatManualCommandContext:
    """Unit tests for VibeApp._format_manual_command_context with output capping."""

    @pytest.fixture
    def app(self) -> VibeApp:
        return build_test_vibe_app()

    def test_stdout_capped_in_context(self, app: VibeApp) -> None:
        limit = app._get_bash_max_output_bytes()
        big_stdout = "x" * (limit + 5000)

        result = app._format_manual_command_context(
            command="cat big.log", cwd="/tmp", stdout=big_stdout, exit_code=0
        )

        assert "[truncated]" in result
        # The raw oversized content must not appear in the formatted output
        assert big_stdout not in result

    def test_stderr_capped_in_context(self, app: VibeApp) -> None:
        limit = app._get_bash_max_output_bytes()
        big_stderr = "E" * (limit + 1000)

        result = app._format_manual_command_context(
            command="make build", cwd="/tmp", stderr=big_stderr, exit_code=1
        )

        assert "[truncated]" in result
        assert big_stderr not in result

    def test_small_output_not_truncated(self, app: VibeApp) -> None:
        result = app._format_manual_command_context(
            command="echo hi", cwd="/tmp", stdout="hi\n", exit_code=0
        )

        assert "[truncated]" not in result
        assert "hi" in result

    def test_both_stdout_and_stderr_capped_independently(self, app: VibeApp) -> None:
        limit = app._get_bash_max_output_bytes()
        big_stdout = "O" * (limit + 100)
        big_stderr = "E" * (limit + 100)

        result = app._format_manual_command_context(
            command="cmd", cwd="/tmp", stdout=big_stdout, stderr=big_stderr, exit_code=1
        )

        # Both should be truncated
        assert result.count("[truncated]") == 2


class TestGetBashMaxOutputBytes:
    """Test that _get_bash_max_output_bytes reads from the tool config."""

    def test_returns_default_value(self) -> None:
        from vibe.core.tools.builtins.bash import BashToolConfig

        app = build_test_vibe_app()
        result = app._get_bash_max_output_bytes()
        assert result == BashToolConfig().max_output_bytes

    def test_returns_positive_int(self) -> None:
        app = build_test_vibe_app()
        assert app._get_bash_max_output_bytes() > 0


@pytest.mark.asyncio
async def test_large_bang_command_output_is_capped_in_history() -> None:
    """Integration test: !command output injected into history respects the cap."""
    backend = FakeBackend(mock_llm_chunk(content="ok"))
    app = build_test_vibe_app(agent_loop=build_test_agent_loop(backend=backend))

    async with app.run_test() as pilot:
        limit = app._get_bash_max_output_bytes()
        # Generate output larger than the cap.
        # seq lines average ~4 chars for small numbers but grow; use
        # a generous count to guarantee we exceed the byte limit.
        repeat = (limit // 2) + 100
        cmd = f"!seq 1 {repeat}"

        chat_input = app.query_one(ChatInputContainer)
        chat_input.value = cmd
        await pilot.press("enter")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if next(iter(app.query(BashOutputMessage)), None):
                break
            await pilot.pause(0.05)

        injected = app.agent_loop.messages[-1]
        assert injected.role == Role.user
        assert injected.injected is True
        assert injected.content is not None
        assert "[truncated]" in injected.content
        # The injected content should be bounded; allow generous margin for
        # formatting overhead but ensure it's not the full raw output.
        assert len(injected.content) < limit * 3
