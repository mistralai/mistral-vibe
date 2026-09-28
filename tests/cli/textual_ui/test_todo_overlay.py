from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from tests.conftest import build_test_vibe_app
from vibe.app_server.models import TodoEffectItem, TodoEffectStatus
from vibe.cli.textual_ui.screens.todo_overlay import TodoOverlayScreen
from vibe.cli.textual_ui.todo_tracker import TodoTracker


def _todos() -> list[TodoEffectItem]:
    return [
        TodoEffectItem(id="1", content="done step", status=TodoEffectStatus.COMPLETED),
        TodoEffectItem(id="2", content="pending step"),
        TodoEffectItem(
            id="3", content="current step", status=TodoEffectStatus.IN_PROGRESS
        ),
        TodoEffectItem(
            id="4", content="dropped step", status=TodoEffectStatus.CANCELLED
        ),
    ]


class _OverlayApp(App[None]):
    def __init__(self, todos: list[TodoEffectItem]) -> None:
        super().__init__()
        self._todos = todos

    def compose(self) -> ComposeResult:
        yield Static("body")

    def open_overlay(self) -> None:
        self.push_screen(TodoOverlayScreen(self._todos))


@pytest.mark.asyncio
async def test_overlay_lists_every_todo_grouped_by_status() -> None:
    app = _OverlayApp(_todos())
    async with app.run_test() as pilot:
        app.open_overlay()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, TodoOverlayScreen)

        lines = [
            str(w.render())
            for w in screen.query("#todo-overlay-list > Static").results()
        ]
        assert lines == [
            "▶ current step",
            "☐ pending step",
            "☑ done step",
            "☒ dropped step",
        ]

        content = screen.query_one("#todo-overlay-content")
        assert content.border_title == "Todos · 1/3 done"


@pytest.mark.asyncio
async def test_escape_dismisses_the_overlay() -> None:
    app = _OverlayApp(_todos())
    async with app.run_test() as pilot:
        app.open_overlay()
        await pilot.pause()
        assert isinstance(app.screen, TodoOverlayScreen)

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TodoOverlayScreen)


@pytest.mark.asyncio
async def test_escape_dismisses_the_overlay_on_the_real_app() -> None:
    """`_OverlayApp` above has no competing bindings.

    The real app binds escape to interrupt with `priority=True`, which is consulted
    before the focused screen and so trapped the user inside the overlay.
    """
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        app._todo_tracker = TodoTracker()
        app._todo_tracker.seed(_todos())
        app.action_show_todos()
        await pilot.pause()
        assert isinstance(app.screen, TodoOverlayScreen)

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TodoOverlayScreen)


@pytest.mark.asyncio
async def test_overlay_handles_an_empty_list() -> None:
    app = _OverlayApp([])
    async with app.run_test() as pilot:
        app.open_overlay()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, TodoOverlayScreen)
        lines = [
            str(w.render())
            for w in screen.query("#todo-overlay-list > Static").results()
        ]
        assert lines == ["No todos"]
