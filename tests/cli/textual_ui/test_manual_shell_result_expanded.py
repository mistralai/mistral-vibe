from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical

from vibe.app_server._shell import shell_effect_detail, shell_effect_state
from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    EffectDetail,
    EffectResultDisplay,
    EffectState,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    ShellEffectDetail,
    ShellEffectInput,
)
from vibe.app_server.protocol import ShellRunResponse
from vibe.cli.textual_ui.widgets.collapsible import (
    HeaderCollapsibleSection,
    OverflowCollapsibleSection,
)
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.tools import ToolResultMessage


def _entry(detail: EffectDetail, state: EffectState) -> PublicEffectEntry:
    return PublicEffectEntry(
        id="op-1",
        session_id="s",
        turn_id="t",
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title=detail.tool_name,
        detail=detail,
        state=state,
    )


def _manual_shell_entry(
    command: str, *, stdout: str = "", stderr: str = "", exit_code: int = 0
) -> PublicEffectEntry:
    """The entry a `!<command>` produces, built from the same helpers the backends use."""
    result = ShellRunResponse(
        operation_id="op-1",
        command=command,
        cwd="/repo",
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )
    output_text = stdout + stderr
    return _entry(
        shell_effect_detail(command),
        shell_effect_state(result, output_text=output_text, duration_ms=1.0),
    )


def _model_shell_entry(command: str, output_text: str) -> PublicEffectEntry:
    """The same SHELL effect kind, but produced by the model calling `bash`."""
    return _entry(
        ShellEffectDetail(
            tool_name="bash",
            input=ShellEffectInput(command=command),
            display=EffectCallDisplay(summary=command, status_text="Running command"),
        ),
        CompletedEffectState(
            output={"stdout": output_text, "stderr": "", "output": output_text},
            output_text=output_text,
            display=EffectResultDisplay(success=True, message=f"Ran {command}"),
        ),
    )


class _ResultApp(App[None]):
    def __init__(self, entry: PublicEffectEntry) -> None:
        super().__init__()
        self._entry = entry
        self.result: ToolResultMessage | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="root")

    async def on_mount(self) -> None:
        root = self.query_one("#root", Vertical)
        self.result = ToolResultMessage(self._entry)
        await root.mount(self.result)


@asynccontextmanager
async def _mounted(
    entry: PublicEffectEntry,
) -> AsyncIterator[tuple[ToolResultMessage, str]]:
    """Mount the result and hand back its rendered text while the app is still live.

    Assertions have to run inside ``run_test``: once it exits the widget tree is
    torn down, and every query comes back empty whatever the widget did.
    """
    app = _ResultApp(entry)
    async with app.run_test() as pilot:
        await pilot.pause()
        result = app.result
        assert result is not None
        texts = [str(widget.render()) for widget in result.query(NoMarkupStatic)]
        yield result, "\n".join(texts)


@pytest.mark.asyncio
async def test_manual_shell_result_is_shown_without_unfolding_it() -> None:
    entry = _manual_shell_entry("ls", stdout="README.md\npyproject.toml")

    async with _mounted(entry) as (result, text):
        assert not result.query(HeaderCollapsibleSection), (
            "a `!` command the user typed should not hide its output behind a toggle"
        )
        assert "README.md" in text
        assert "pyproject.toml" in text


@pytest.mark.asyncio
async def test_model_shell_call_still_folds_into_a_header() -> None:
    # Same SHELL effect kind: only the tool name separates the agent's own shell
    # call from the user's, and the agent's stays folded like every tool result.
    entry = _model_shell_entry("ls", "README.md\npyproject.toml")

    async with _mounted(entry) as (result, _):
        section = result.query_one(HeaderCollapsibleSection)
        assert section._collapsible is True


@pytest.mark.asyncio
async def test_failing_manual_shell_command_still_shows_what_it_printed() -> None:
    # A non-zero exit is an ordinary outcome of `!`, and the stderr is usually the
    # whole reason it was run -- reporting only the exit status would bury it.
    entry = _manual_shell_entry(
        "cat missing", stderr="cat: missing: No such file", exit_code=1
    )

    async with _mounted(entry) as (result, text):
        assert "cat: missing: No such file" in text
        # The failure is still reported as one: the output is added, not substituted.
        assert result._is_error is True
        assert result.query(OverflowCollapsibleSection)
