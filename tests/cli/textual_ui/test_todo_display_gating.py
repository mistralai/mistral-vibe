from __future__ import annotations

from unittest.mock import AsyncMock, PropertyMock, patch
from weakref import WeakKeyDictionary

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical

from tests.conftest import build_test_vibe_app
from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    EffectResultDisplay,
    GenericEffectDetail,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    TodoEffectDetail,
    TodoEffectItem,
    TodoEffectStatus,
)
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.todo_tracker import TodoTracker, todo_deltas
from vibe.cli.textual_ui.widgets.collapsible import HeaderCollapsibleSection
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.todo_status import TodoStatusRow
from vibe.cli.textual_ui.widgets.tools import ToolGroup, ToolResultMessage
from vibe.cli.textual_ui.windowing.history import build_history_widgets


def _todo_entry() -> PublicEffectEntry:
    todos = [
        TodoEffectItem(id="1", content="scaffold the crate"),
        TodoEffectItem(
            id="2", content="port the protocol", status=TodoEffectStatus.IN_PROGRESS
        ),
    ]
    return PublicEffectEntry(
        id="call-1",
        session_id="s",
        turn_id="t",
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title="todo",
        detail=TodoEffectDetail(
            tool_name="todo",
            display=EffectCallDisplay(summary="todo", status_text="Updating todos"),
        ),
        state=CompletedEffectState(
            output={"todos": [t.model_dump(mode="json") for t in todos]},
            output_text="",
            display=EffectResultDisplay(success=True, message="todo"),
        ),
    )


def _shell_entry() -> PublicEffectEntry:
    return PublicEffectEntry(
        id="call-2",
        session_id="s",
        turn_id="t",
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title="bash",
        detail=GenericEffectDetail(
            tool_name="bash",
            display=EffectCallDisplay(summary="bash", status_text="Running bash"),
        ),
        state=CompletedEffectState(
            output=None,
            output_text="plop",
            display=EffectResultDisplay(success=True, message="echo plop"),
        ),
    )


class _ResultApp(App[None]):
    def __init__(self, entry: PublicEffectEntry, todo_delta: str | None) -> None:
        super().__init__()
        self._entry = entry
        self._todo_delta = todo_delta
        self.result: ToolResultMessage | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="root")

    async def on_mount(self) -> None:
        root = self.query_one("#root", Vertical)
        self.result = ToolResultMessage(self._entry, todo_delta=self._todo_delta)
        await root.mount(self.result)


async def _header_suffix_and_body(app: _ResultApp) -> tuple[str, list[str]]:
    async with app.run_test() as pilot:
        await pilot.pause()
        result = app.result
        assert result is not None
        section = result.query_one(HeaderCollapsibleSection)
        suffix = " ".join(
            str(widget.render()) for widget in section.query(".status-indicator-suffix")
        )
        section.set_collapsed(False)
        await pilot.pause()
        body = result.query_one(".tool-result-content")
        return suffix, [str(widget.render()) for widget in body.query(NoMarkupStatic)]


@pytest.mark.asyncio
async def test_a_unified_todo_result_reports_the_change_in_its_header() -> None:
    suffix, body = await _header_suffix_and_body(
        _ResultApp(_todo_entry(), "+2 · 0/2 done")
    )
    # Results collapse by default, so a change reported only in the body would
    # never be read; the fold still opens onto the list itself.
    assert "+2 · 0/2 done" in suffix
    assert any("scaffold the crate" in text for text in body)


@pytest.mark.asyncio
async def test_a_legacy_todo_result_carries_no_header_change() -> None:
    suffix, body = await _header_suffix_and_body(_ResultApp(_todo_entry(), None))
    assert suffix == ""
    assert any("scaffold the crate" in text for text in body)
    assert any("port the protocol" in text for text in body)


@pytest.mark.asyncio
async def test_non_todo_results_are_untouched_by_the_delta_path() -> None:
    suffix, body = await _header_suffix_and_body(_ResultApp(_shell_entry(), None))
    assert suffix == ""
    assert any("plop" in text for text in body)


def _noop_mount(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("_record_todos must not mount anything")


def test_event_handler_without_a_tracker_produces_no_delta() -> None:
    handler = EventHandler(mount_callback=_noop_mount, get_tools_collapsed=lambda: True)
    assert handler._record_todos(_todo_entry()) is None


def test_event_handler_with_a_tracker_records_and_notifies() -> None:
    tracker = TodoTracker()
    notified: list[int] = []
    handler = EventHandler(
        mount_callback=_noop_mount,
        get_tools_collapsed=lambda: True,
        todo_tracker=tracker,
        on_todos_changed=lambda: notified.append(1),
    )
    assert handler._record_todos(_todo_entry()) == "+2 · 0/2 done"
    assert tracker.summary == "▶ 0/2 · port the protocol"
    assert notified == [1]


def test_event_handler_ignores_non_todo_effects() -> None:
    tracker = TodoTracker()
    handler = EventHandler(
        mount_callback=_noop_mount,
        get_tools_collapsed=lambda: True,
        todo_tracker=tracker,
    )
    assert handler._record_todos(_shell_entry()) is None
    assert tracker.todos == []


class _RowApp(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.row = TodoStatusRow()
        self.activations = 0

    def compose(self) -> ComposeResult:
        yield self.row

    def on_todo_status_row_activated(self, event: TodoStatusRow.Activated) -> None:
        event.stop()
        self.activations += 1


@pytest.mark.asyncio
async def test_pinned_row_hides_itself_when_there_is_nothing_to_show() -> None:
    app = _RowApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.row.display is False

        app.row.set_summary("▶ 1/3 · current thing")
        await pilot.pause()
        assert app.row.display is True
        assert "current thing" in str(app.row.render())

        app.row.set_summary(None)
        await pilot.pause()
        assert app.row.display is False


@pytest.mark.asyncio
async def test_clicking_the_pinned_row_requests_the_overlay() -> None:
    app = _RowApp()
    async with app.run_test() as pilot:
        app.row.set_summary("▶ 1/3 · current thing")
        await pilot.pause()
        await pilot.click(TodoStatusRow)
        await pilot.pause()
        assert app.activations == 1


def _replayed_result(
    entries: list[PublicEffectEntry], deltas: dict[str, str] | None
) -> ToolResultMessage:
    widgets = build_history_widgets(
        entries,
        start_index=0,
        history_widget_indices=WeakKeyDictionary(),
        tools_collapsed=True,
        todo_deltas=deltas,
    )
    (group,) = widgets
    assert isinstance(group, ToolGroup)
    (result,) = [
        child
        for child in group.content_container.children
        if isinstance(child, ToolResultMessage)
    ]
    return result


def test_a_replayed_todo_result_carries_the_delta_the_live_path_recorded() -> None:
    # A re-windowed page cannot derive its own delta, so without the replay the same
    # result renders one way while it streams and another once it is rebuilt.
    entries = [_todo_entry()]
    assert (
        _replayed_result(entries, todo_deltas(entries))._todo_delta == "+2 · 0/2 done"
    )


def test_a_replay_without_deltas_keeps_the_legacy_full_list() -> None:
    assert _replayed_result([_todo_entry()], None)._todo_delta is None


@pytest.mark.asyncio
async def test_resume_does_not_seed_the_pinned_row_from_history() -> None:
    """*Prepare*: A session whose history holds a completed todo write.
    *Do*: Replay that history the way a resume does.
    *Assert*: The pinned row stays hidden.

    The list itself lives in the harness session's memory, which a resumed process
    rebuilds empty. A row seeded from history would advertise a plan that
    ``todo read`` reports as empty.
    """
    vibe_app = build_test_vibe_app()
    async with vibe_app.run_test() as pilot:
        vibe_app._todo_tracker = TodoTracker()
        vibe_app._mount_and_scroll = AsyncMock()
        row = vibe_app.query_one(TodoStatusRow)

        with patch.object(
            type(vibe_app.app_server),
            "history",
            new_callable=PropertyMock,
            return_value=[_todo_entry()],
        ):
            await vibe_app._resume_history_from_messages()
        await pilot.pause()

        assert vibe_app._todo_tracker.todos == []
        assert row.display is False


@pytest.mark.asyncio
async def test_attaching_another_session_drops_the_pinned_row() -> None:
    """*Prepare*: A pinned row showing the attached session's todos.
    *Do*: Reset the presentation the way a resume does.
    *Assert*: The row is empty again.

    Nothing reseeds the row from history, so a row left standing would advertise the
    previous session's plan against the newly attached one.
    """
    vibe_app = build_test_vibe_app()
    async with vibe_app.run_test() as pilot:
        vibe_app._todo_tracker = TodoTracker()
        vibe_app._todo_tracker.seed([
            TodoEffectItem(id="1", content="port the protocol")
        ])
        vibe_app._refresh_todo_status()
        await pilot.pause()
        row = vibe_app.query_one(TodoStatusRow)
        assert row.display is True

        await vibe_app._reset_presentation_after_resume()
        await pilot.pause()

        assert vibe_app._todo_tracker.todos == []
        assert row.display is False


@pytest.mark.asyncio
async def test_a_replayed_history_batch_is_wired_to_the_deltas() -> None:
    """A rebuilt page only gets a delta if the app recomputes one from history."""
    vibe_app = build_test_vibe_app()
    async with vibe_app.run_test() as pilot:
        vibe_app._todo_tracker = TodoTracker()
        vibe_app._mount_and_scroll = AsyncMock()

        with patch.object(
            type(vibe_app.app_server),
            "history",
            new_callable=PropertyMock,
            return_value=[_todo_entry()],
        ):
            await vibe_app._resume_history_from_messages()
        await pilot.pause()

        results = list(vibe_app._messages_area.query(ToolResultMessage))
        assert [result._todo_delta for result in results] == ["+2 · 0/2 done"]
