from __future__ import annotations

from pydantic import JsonValue

from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    EffectResultDisplay,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    RunningEffectState,
    TextContentBlock,
    TodoEffectDetail,
    TodoEffectItem,
    TodoEffectStatus,
)
from vibe.cli.textual_ui.todo_tracker import (
    TodoTracker,
    progress_label,
    summarize_change,
    summary_line,
    todo_deltas,
)


def _todo(
    todo_id: str, content: str, status: TodoEffectStatus = TodoEffectStatus.PENDING
) -> TodoEffectItem:
    return TodoEffectItem(id=todo_id, content=content, status=status)


def _todo_entry(
    entry_id: str, todos: list[TodoEffectItem] | None, *, completed: bool = True
) -> PublicEffectEntry:
    output: JsonValue = (
        {"todos": [t.model_dump(mode="json") for t in todos]}
        if todos is not None
        else None
    )
    return PublicEffectEntry(
        id=entry_id,
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
        state=(
            CompletedEffectState(
                output=output,
                output_text="",
                display=EffectResultDisplay(success=True, message="todo"),
            )
            if completed
            else RunningEffectState()
        ),
    )


def test_progress_label_excludes_cancelled_from_the_denominator() -> None:
    todos = [
        _todo("1", "a", TodoEffectStatus.COMPLETED),
        _todo("2", "b", TodoEffectStatus.COMPLETED),
        _todo("3", "c", TodoEffectStatus.CANCELLED),
    ]
    # An abandoned step must not leave the plan permanently stuck at 2/3.
    assert progress_label(todos) == "2/2"


def test_summary_line_prefers_the_in_progress_item() -> None:
    todos = [
        _todo("1", "done thing", TodoEffectStatus.COMPLETED),
        _todo("2", "pending thing"),
        _todo("3", "current thing", TodoEffectStatus.IN_PROGRESS),
    ]
    assert summary_line(todos) == "▶ 1/3 · current thing"


def test_summary_line_falls_back_to_the_first_pending_item() -> None:
    todos = [
        _todo("1", "done thing", TodoEffectStatus.COMPLETED),
        _todo("2", "next thing"),
        _todo("3", "later thing"),
    ]
    assert summary_line(todos) == "☐ 1/3 · next thing"


def test_summary_line_reports_completion_when_nothing_is_left() -> None:
    todos = [_todo("1", "a", TodoEffectStatus.COMPLETED)]
    assert summary_line(todos) == "☑ 1/1 · All todos complete"


def test_summary_line_does_not_call_an_abandoned_list_complete() -> None:
    todos = [
        _todo("1", "a", TodoEffectStatus.CANCELLED),
        _todo("2", "b", TodoEffectStatus.CANCELLED),
    ]
    assert summary_line(todos) == "☒ 0/0 · All todos cancelled"


def test_summary_line_reports_completion_when_only_some_were_cancelled() -> None:
    todos = [
        _todo("1", "a", TodoEffectStatus.COMPLETED),
        _todo("2", "b", TodoEffectStatus.CANCELLED),
    ]
    assert summary_line(todos) == "☑ 1/1 · All todos complete"


def test_summary_line_is_none_when_there_are_no_todos() -> None:
    assert summary_line([]) is None


def test_summarize_change_reports_additions_removals_and_transitions() -> None:
    previous = [_todo("1", "a"), _todo("2", "b")]
    current = [
        _todo("1", "a", TodoEffectStatus.COMPLETED),
        _todo("3", "c", TodoEffectStatus.IN_PROGRESS),
        _todo("4", "d"),
    ]
    assert summarize_change(previous, current) == "+2 · −1 · 1 completed · 1/3 done"


def test_summarize_change_reports_a_rewrite_with_no_semantic_change() -> None:
    todos = [_todo("1", "a"), _todo("2", "b")]
    assert summarize_change(todos, todos) == "no changes · 0/2 done"


def test_summarize_change_handles_the_first_write_and_a_clear() -> None:
    todos = [_todo("1", "a"), _todo("2", "b")]
    assert summarize_change([], todos) == "+2 · 0/2 done"
    assert summarize_change(todos, []) == "Cleared todos"
    assert summarize_change([], []) == "No todos"


def test_tracker_records_deltas_against_its_own_previous_state() -> None:
    tracker = TodoTracker()
    assert tracker.summary is None

    first = [_todo("1", "a", TodoEffectStatus.IN_PROGRESS), _todo("2", "b")]
    assert tracker.record(first) == "+2 · 0/2 done"
    assert tracker.summary == "▶ 0/2 · a"

    second = [
        _todo("1", "a", TodoEffectStatus.COMPLETED),
        _todo("2", "b", TodoEffectStatus.IN_PROGRESS),
    ]
    assert tracker.record(second) == "1 started · 1 completed · 1/2 done"
    assert tracker.summary == "▶ 1/2 · b"
    assert [t.id for t in tracker.todos] == ["1", "2"]


def test_todo_deltas_are_relative_to_the_preceding_write() -> None:
    history = [
        PublicMessageEntry(
            id="m1",
            session_id="s",
            turn_id="t",
            created_at=1,
            updated_at=1,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            role="user",
            content=[TextContentBlock(text="go")],
        ),
        _todo_entry("e1", [_todo("1", "old")]),
        _todo_entry("e2", [_todo("1", "new"), _todo("2", "newer")]),
    ]
    # `e2` must read as the change `e1` left behind, exactly as the live path
    # recorded it -- not as a standalone reprint of the whole list.
    assert todo_deltas(history) == {"e1": "+1 · 0/1 done", "e2": "+1 · 0/2 done"}


def test_todo_deltas_skip_entries_without_usable_output() -> None:
    history = [
        _todo_entry("e1", [_todo("1", "kept")]),
        _todo_entry("e2", None),
        _todo_entry("e3", None, completed=False),
    ]
    # A hook-replaced or still-running write carries no structured todos, so it
    # has no delta and must not disturb the running state either.
    assert todo_deltas(history) == {"e1": "+1 · 0/1 done"}


def test_todo_deltas_is_empty_when_history_has_none() -> None:
    assert todo_deltas([]) == {}
