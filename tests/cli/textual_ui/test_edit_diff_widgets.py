from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult

from vibe.app_server.models import (
    FileEditEffectBatchInput,
    FileEditEffectChange,
    FileEditEffectInput,
    FileEditEffectOccurrence,
    FileEditEffectOutput,
)
from vibe.cli.textual_ui.widgets.diff_rendering import (
    DiffOccurrence,
    DiffView,
    edit_diff_batch_inputs,
)
from vibe.cli.textual_ui.widgets.tool_widgets import (
    EditApprovalWidget,
    EditResultWidget,
)


def _result_widget() -> EditResultWidget:
    return EditResultWidget(
        FileEditEffectOutput(
            file="example.py", old_string="value = 1", new_string="value = 2"
        ),
        success=True,
        message="updated",
    )


def _approval_widget() -> EditApprovalWidget:
    return EditApprovalWidget(
        FileEditEffectInput(
            file_path="example.py", old_string="value = 1", new_string="value = 2"
        )
    )


@pytest.mark.asyncio
async def test_batch_approval_applies_changes_in_order_from_one_file_read(
    tmp_path: Path,
) -> None:
    target = tmp_path / "example.py"
    target.write_text("alpha\n", encoding="utf-8")

    occurrences = await edit_diff_batch_inputs(
        str(target), [("alpha", "beta", False), ("beta", "gamma", False)]
    )

    assert [item.start_line for item in occurrences] == [1, 1]
    assert occurrences[0].old_lines == "alpha"
    assert occurrences[0].new_lines == "beta"
    assert occurrences[1].old_lines == "beta"
    assert occurrences[1].new_lines == "gamma"


def test_batch_approval_and_occurrence_only_result_build_diff_widgets() -> None:
    approval = EditApprovalWidget(
        FileEditEffectBatchInput(
            file_path="example.py",
            changes=[
                FileEditEffectChange(old_string="alpha", new_string="beta"),
                FileEditEffectChange(old_string="beta", new_string="gamma"),
            ],
        )
    )
    result = EditResultWidget(
        FileEditEffectOutput(
            file="example.py",
            occurrences=[
                FileEditEffectOccurrence(
                    start_line=1, old_text="alpha\n", new_text="beta\n"
                ),
                FileEditEffectOccurrence(
                    start_line=1, old_text="beta\n", new_text="gamma\n"
                ),
            ],
        ),
        success=True,
        message="updated",
    )

    assert isinstance(approval.args, FileEditEffectBatchInput)
    assert approval.args.changes[1].new_string == "gamma"
    assert result._occurrences == [
        DiffOccurrence(1, "alpha\n", "beta\n"),
        DiffOccurrence(1, "beta\n", "gamma\n"),
    ]


@pytest.mark.asyncio
async def test_restyle_rebuilds_the_diff_in_the_requested_mode() -> None:
    widget = _result_widget()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        original_lines = widget._diff_view._lines
        assert not widget._diff_view._ansi

        widget.request_diff_render(ansi=True, dark=True)

        assert widget._diff_view._lines is not original_lines
        assert widget._diff_view._ansi
        assert widget._diff_view._dark
        assert widget.border_row_colors


@pytest.mark.asyncio
async def test_restyle_only_highlights_visible_rows() -> None:
    # A tall diff mounted in a short viewport: restyling must not pay for the
    # rows the compositor never asks for.
    result = FileEditEffectOutput(
        file="example.py",
        old_string="\n".join(f"value = {i}" for i in range(200)),
        new_string="\n".join(f"changed = {i}" for i in range(200)),
    )
    widget = EditResultWidget(result, success=True, message="updated")

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        widget.request_diff_render(ansi=True, dark=True)
        await pilot.pause()

        lines = widget._diff_view._lines
        built = sum(line._content is not None for line in lines)

    assert len(lines) > 100
    assert 0 < built < len(lines)


@pytest.mark.asyncio
async def test_approval_uses_latest_theme_requested_while_loading_inputs() -> None:
    widget = _approval_widget()
    inputs_started = asyncio.Event()
    release_inputs = asyncio.Event()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async def delayed_inputs(*args, **kwargs):
        inputs_started.set()
        await release_inputs.wait()
        return [DiffOccurrence(1, "value = 1", "value = 2")]

    async with _App().run_test() as pilot:
        await pilot.pause()
        widget._occurrences = None

        with patch(
            "vibe.cli.textual_ui.widgets.tool_widgets.edit_diff_inputs",
            side_effect=delayed_inputs,
        ):
            reload_inputs = asyncio.create_task(widget.on_mount())
            await inputs_started.wait()
            widget.request_diff_render(ansi=True, dark=True)
            release_inputs.set()
            await reload_inputs

    assert widget._diff_view._ansi
    assert widget._diff_view._dark
    assert widget._diff_view._lines


@pytest.mark.asyncio
async def test_diff_view_reuses_the_visual_it_built_for_a_row() -> None:
    widget = _result_widget()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        view = widget.query_one(DiffView)
        first = view._visual(0)
        assert view._visual(0) is first

        widget.request_diff_render(ansi=True, dark=True)
        assert view._visuals == [None] * len(view._lines)
