from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult

from vibe.app_server.models import FileEditEffectInput, FileEditEffectOutput
from vibe.cli.textual_ui.widgets.diff_rendering import DiffOccurrence, render_edit_diff
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
async def test_restyle_keeps_existing_diff_until_background_render_finishes() -> None:
    widget = _result_widget()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        original_lines = widget._diff_view._lines
        assert not widget._diff_view._ansi
        replacement_lines = render_edit_diff(
            [DiffOccurrence(1, "value = 2", "value = 3")], "py", ansi=True, dark=True
        )
        render_started = asyncio.Event()
        allow_render = asyncio.Event()

        async def delayed_render(*args, **kwargs):
            render_started.set()
            await allow_render.wait()
            return replacement_lines

        with patch(
            "vibe.cli.textual_ui.widgets.tool_widgets.render_edit_diff_async",
            side_effect=delayed_render,
        ):
            worker = widget.request_diff_render(ansi=True, dark=True)
            assert worker is not None
            await render_started.wait()

            assert widget._diff_view._lines is original_lines
            assert widget._diff_view._ansi
            assert widget._diff_view._dark

            allow_render.set()
            await worker.wait()

        assert widget._diff_view._lines is replacement_lines


@pytest.mark.asyncio
async def test_approval_uses_latest_theme_requested_while_loading_inputs() -> None:
    widget = _approval_widget()
    inputs_started = asyncio.Event()
    release_inputs = asyncio.Event()
    rendered_theme: tuple[bool, bool] | None = None

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async def delayed_inputs(*args, **kwargs):
        inputs_started.set()
        await release_inputs.wait()
        return [DiffOccurrence(1, "value = 1", "value = 2")]

    async def capture_render(*args, ansi: bool, dark: bool, **kwargs):
        nonlocal rendered_theme
        rendered_theme = (ansi, dark)
        return render_edit_diff(
            [DiffOccurrence(1, "value = 1", "value = 2")], "py", ansi=ansi, dark=dark
        )

    async with _App().run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        widget._occurrences = None

        with (
            patch(
                "vibe.cli.textual_ui.widgets.tool_widgets.edit_diff_inputs",
                side_effect=delayed_inputs,
            ),
            patch(
                "vibe.cli.textual_ui.widgets.tool_widgets.render_edit_diff_async",
                side_effect=capture_render,
            ),
        ):
            reload_inputs = asyncio.create_task(widget.on_mount())
            await inputs_started.wait()
            assert widget.request_diff_render(ansi=True, dark=True) is None
            release_inputs.set()
            await reload_inputs
            assert widget._render_worker is not None
            await widget._render_worker.wait()

    assert rendered_theme == (True, True)


@pytest.mark.asyncio
async def test_latest_diff_render_wins_while_an_older_render_is_running() -> None:
    widget = _result_widget()

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            yield widget

    async with _App().run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        first_lines = render_edit_diff(
            [DiffOccurrence(1, "first = 1", "first = 2")], "py", ansi=True, dark=True
        )
        latest_lines = render_edit_diff(
            [DiffOccurrence(1, "latest = 1", "latest = 2")],
            "py",
            ansi=False,
            dark=False,
        )
        first_started = asyncio.Event()
        latest_started = asyncio.Event()
        release_first = asyncio.Event()
        release_latest = asyncio.Event()

        async def delayed_render(*args, ansi: bool, **kwargs):
            if ansi:
                first_started.set()
                await release_first.wait()
                return first_lines
            latest_started.set()
            await release_latest.wait()
            return latest_lines

        with patch(
            "vibe.cli.textual_ui.widgets.tool_widgets.render_edit_diff_async",
            side_effect=delayed_render,
        ):
            worker = widget.request_diff_render(ansi=True, dark=True)
            assert worker is not None
            await first_started.wait()

            assert widget.request_diff_render(ansi=False, dark=False) is worker
            release_first.set()
            await latest_started.wait()
            release_latest.set()
            await worker.wait()

        assert widget._diff_view._lines is latest_lines
        assert not widget._diff_view._ansi
        assert not widget._diff_view._dark
