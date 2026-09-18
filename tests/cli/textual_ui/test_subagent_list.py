from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.content import Content

from vibe.app_server.models import (
    ArchivedSessionStatus,
    BlockedSessionStatus,
    IdleSessionStatus,
    PublicChildSession,
    RunningSessionStatus,
    TokenUsage,
)
from vibe.cli.textual_ui.widgets.chat_input.subagent_list import (
    SubagentList,
    subagent_loading_status,
)


def _child(
    session_id: str,
    status: RunningSessionStatus | BlockedSessionStatus | ArchivedSessionStatus,
    tokens: int,
) -> PublicChildSession:
    return PublicChildSession(
        id=session_id,
        name="test-audit",
        agent_type="explore",
        status=status,
        created_at=1,
        updated_at=2,
        token_usage=TokenUsage(input_tokens=999_999, total_tokens=999_999),
        context_usage=TokenUsage(input_tokens=tokens, total_tokens=tokens),
    )


def test_subagent_list_renders_identity_status_and_context_usage() -> None:
    widget = SubagentList()
    child = _child("child-1", RunningSessionStatus(active_turn_id="turn-child"), 12_500)

    widget.update_sessions((child,))

    assert widget.display
    assert widget.option_count == 2
    assert str(widget.get_option_at_index(0).prompt) == "  Main conversation"
    assert str(widget.get_option_at_index(1).prompt) == (
        "  Explore (Test Audit) [running] · 12k tokens"
    )


@pytest.mark.parametrize(
    ("agent_type", "name", "expected_identity"),
    [
        ("code_review", "security-audit", "Code Review (Security Audit)"),
        ("repoExplorer", "dependencyScan", "Repo Explorer (Dependency Scan)"),
        ("API", "API audit", "API (API audit)"),
    ],
)
def test_subagent_list_humanizes_identifier_names(
    agent_type: str, name: str, expected_identity: str
) -> None:
    widget = SubagentList()
    child = _child(
        "child-1", RunningSessionStatus(active_turn_id="turn-child"), 100
    ).model_copy(update={"agent_type": agent_type, "name": name})

    widget.update_sessions((child,))

    assert str(widget.get_option_at_index(1).prompt).startswith(
        f"  {expected_identity} [running]"
    )


def test_subagent_list_renders_terminal_status() -> None:
    widget = SubagentList()
    child = _child("child-123456789", ArchivedSessionStatus(), 0).model_copy(
        update={"name": "Code review", "agent_type": "review"}
    )

    widget.update_sessions(
        (
            child.model_copy(
                update={"status": RunningSessionStatus(active_turn_id="turn-child")}
            ),
        ),
        selected_session_id=child.id,
    )
    widget.update_sessions((child,), selected_session_id=child.id)

    assert str(widget.get_option_at_index(1).prompt) == (
        "  Review (Code review) [stopped] · 0 tokens"
    )


def test_subagent_loading_status_only_shows_active_work() -> None:
    running = _child("child-1", RunningSessionStatus(active_turn_id="turn-child"), 0)
    blocked = running.model_copy(
        update={
            "status": BlockedSessionStatus(
                active_turn_id="turn-child", callback_id="callback-1", reason="approval"
            )
        }
    )
    stopped = running.model_copy(update={"status": ArchivedSessionStatus()})

    assert subagent_loading_status(running) == "Running"
    assert subagent_loading_status(blocked) == "Waiting for input"
    assert subagent_loading_status(stopped) is None


def test_subagent_list_marks_the_selected_child_without_rebuilding_options() -> None:
    widget = SubagentList()
    child = _child("child-1", RunningSessionStatus(active_turn_id="turn-child"), 10)
    widget.update_sessions((child,))
    main_option = widget.get_option_at_index(0)
    child_option = widget.get_option_at_index(1)

    widget.update_sessions((child,), selected_session_id=child.id)

    assert widget.get_option_at_index(0) is main_option
    assert widget.get_option_at_index(1) is child_option
    main_prompt = main_option.prompt
    child_prompt = child_option.prompt
    assert isinstance(main_prompt, Content)
    assert isinstance(child_prompt, Content)
    assert str(main_prompt) == "  Main conversation"
    assert str(child_prompt).startswith("  Explore (Test Audit)")
    assert all("$primary" not in str(span.style) for span in main_prompt.spans)
    selected_span = child_prompt.spans[-1]
    assert (selected_span.start, selected_span.end) == (0, len(child_prompt))
    assert str(selected_span.style) == "$primary"
    assert widget.has_class("subagent-view")
    assert widget.border_title == " Viewing Test Audit · read-only · Esc to return "


def test_subagent_list_hides_without_children() -> None:
    widget = SubagentList()

    widget.update_sessions(())

    assert not widget.display
    assert widget.option_count == 0


class _SubagentListApp(App[None]):
    CSS = "SubagentList { height: auto; border: none; padding: 0; }"

    def __init__(self) -> None:
        super().__init__()
        self.focus_input_requests = 0
        self.selected_session_ids: list[str | None] = []

    def compose(self) -> ComposeResult:
        yield SubagentList()

    def on_subagent_list_focus_input_requested(
        self, _event: SubagentList.FocusInputRequested
    ) -> None:
        self.focus_input_requests += 1

    def on_subagent_list_selected(self, event: SubagentList.Selected) -> None:
        self.selected_session_ids.append(event.session_id)


@pytest.mark.asyncio
async def test_input_list_focus_boundary_requests_input_focus() -> None:
    app = _SubagentListApp()
    child = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)

    async with app.run_test() as pilot:
        widget = app.query_one(SubagentList)
        widget.update_sessions((child,))
        app.set_focus(None)
        await pilot.pause()

        assert widget.focus_first()
        await pilot.pause()
        assert app.focused is widget
        assert str(widget.get_option_at_index(0).prompt).startswith("> ")

        widget.action_cursor_up()
        await pilot.pause()
        assert app.focus_input_requests == 1


@pytest.mark.asyncio
async def test_subagent_view_list_navigation_stops_at_main_row() -> None:
    app = _SubagentListApp()
    child = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)

    async with app.run_test() as pilot:
        widget = app.query_one(SubagentList)
        widget.update_sessions((child,), selected_session_id=child.id)
        widget.focus()
        await pilot.pause()

        await pilot.press("up", "up")

        assert app.focused is widget
        assert widget.highlighted == 0
        assert str(widget.get_option_at_index(0).prompt).startswith("> ")
        assert app.focus_input_requests == 0


@pytest.mark.asyncio
async def test_arrow_keys_highlight_without_selecting_until_enter() -> None:
    app = _SubagentListApp()
    child = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)

    async with app.run_test() as pilot:
        widget = app.query_one(SubagentList)
        widget.update_sessions((child,))
        widget.focus()
        await pilot.pause()
        app.selected_session_ids.clear()

        widget.action_cursor_down()

        assert str(widget.get_option_at_index(0).prompt).startswith("  ")
        assert str(widget.get_option_at_index(1).prompt).startswith("> ")
        assert widget.highlighted == 1
        await pilot.pause()
        assert app.selected_session_ids == []

        await pilot.press("enter")
        await pilot.pause()

        assert app.selected_session_ids == [child.id]


@pytest.mark.asyncio
async def test_mouse_hover_marks_a_session_and_click_selects_it() -> None:
    app = _SubagentListApp()
    child = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)

    async with app.run_test() as pilot:
        widget = app.query_one(SubagentList)
        widget.update_sessions((child,))
        app.set_focus(None)
        await pilot.pause()
        app.selected_session_ids.clear()

        assert await pilot.hover(widget, offset=(1, 1))
        await pilot.pause()

        assert str(widget.get_option_at_index(0).prompt).startswith("  ")
        assert str(widget.get_option_at_index(1).prompt).startswith("> ")
        assert app.selected_session_ids == []

        await pilot.hover(offset=(10, 10))
        await pilot.pause()

        assert str(widget.get_option_at_index(1).prompt).startswith("  ")
        assert app.selected_session_ids == []

        assert await pilot.hover(widget, offset=(1, 1))
        assert await pilot.click(widget, offset=(1, 1))
        await pilot.pause()

        assert app.selected_session_ids == [child.id]


@pytest.mark.asyncio
async def test_subagent_update_preserves_focused_highlight() -> None:
    app = _SubagentListApp()
    first = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)
    second = _child("child-2", RunningSessionStatus(active_turn_id="turn-2"), 20)

    async with app.run_test() as pilot:
        widget = app.query_one(SubagentList)
        widget.update_sessions((first, second))
        widget.focus()
        widget.highlighted = 2
        await pilot.pause()

        widget.update_sessions((
            first.model_copy(
                update={
                    "updated_at": 3,
                    "token_usage": TokenUsage(input_tokens=30, total_tokens=30),
                }
            ),
            second,
        ))

        assert app.focused is widget
        assert widget.highlighted == 2


@pytest.mark.asyncio
async def test_subagent_update_preserves_focused_highlight_when_child_added() -> None:
    app = _SubagentListApp()
    first = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)
    second = _child("child-2", RunningSessionStatus(active_turn_id="turn-2"), 20)
    third = _child("child-3", RunningSessionStatus(active_turn_id="turn-3"), 30)

    async with app.run_test() as pilot:
        widget = app.query_one(SubagentList)
        widget.update_sessions((first, second), selected_session_id=second.id)
        widget.focus()
        widget.highlighted = 1
        await pilot.pause()

        widget.update_sessions((first, second, third), selected_session_id=second.id)

        assert app.focused is widget
        assert widget.highlighted == 1
        assert str(widget.get_option_at_index(1).prompt).startswith("> ")


@pytest.mark.asyncio
async def test_subagent_list_navigation_does_not_wrap_at_bottom() -> None:
    app = _SubagentListApp()
    child = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)

    async with app.run_test() as pilot:
        widget = app.query_one(SubagentList)
        widget.update_sessions((child,))
        widget.focus()
        widget.highlighted = 1
        await pilot.pause()
        app.selected_session_ids.clear()

        await pilot.press("down")

        assert widget.highlighted == 1
        assert app.selected_session_ids == []


@pytest.mark.asyncio
async def test_idle_subagent_remains_visible_and_keeps_list_focus() -> None:
    app = _SubagentListApp()
    running = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)
    idle = running.model_copy(update={"status": IdleSessionStatus()})

    async with app.run_test() as pilot:
        widget = app.query_one(SubagentList)
        widget.update_sessions((running,))
        widget.focus()
        await pilot.pause()

        widget.update_sessions((idle,))
        await pilot.pause()

        assert widget.display
        assert widget.option_count == 2
        assert "[ready]" in str(widget.get_option_at_index(1).prompt)
        assert app.focus_input_requests == 0


def test_stopped_subagent_remains_until_open_transcript_closes() -> None:
    widget = SubagentList()
    running = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)
    stopped = running.model_copy(update={"status": ArchivedSessionStatus()})

    widget.update_sessions((running,), selected_session_id=running.id)
    widget.update_sessions((stopped,), selected_session_id=stopped.id)

    assert widget.display
    assert "[stopped]" in str(widget.get_option_at_index(1).prompt)

    widget.update_sessions((stopped,), selected_session_id=None)

    assert not widget.display
    assert widget.option_count == 0


def test_completed_sibling_remains_while_batch_is_active() -> None:
    widget = SubagentList()
    first = _child("child-1", RunningSessionStatus(active_turn_id="turn-1"), 10)
    second = _child("child-2", RunningSessionStatus(active_turn_id="turn-2"), 20)

    widget.update_sessions((first, second))
    widget.update_sessions((
        first.model_copy(update={"status": IdleSessionStatus()}),
        second,
    ))

    assert widget.display
    assert widget.option_count == 3
    assert "[ready]" in str(widget.get_option_at_index(1).prompt)


def test_new_active_session_starts_a_fresh_batch() -> None:
    widget = SubagentList()
    old_running = _child(
        "child-old", RunningSessionStatus(active_turn_id="turn-old"), 10
    )
    old_idle = old_running.model_copy(update={"status": IdleSessionStatus()})
    new_running = _child(
        "child-new", RunningSessionStatus(active_turn_id="turn-new"), 20
    )

    widget.update_sessions((old_running,))
    widget.update_sessions((old_idle,))
    widget.update_sessions((old_idle, new_running))

    assert widget.display
    assert widget.option_count == 3
    assert widget.get_option_at_index(1).id == old_idle.id
    assert widget.get_option_at_index(2).id == new_running.id
