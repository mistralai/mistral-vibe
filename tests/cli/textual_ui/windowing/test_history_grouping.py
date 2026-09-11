from __future__ import annotations

from weakref import WeakKeyDictionary

from vibe.app_server._effect_models import FileReadEffectDetail, ShellEffectDetail
from vibe.app_server.models import (
    MANUAL_SHELL_TOOL_NAME,
    CompletedEffectState,
    EffectResultDisplay,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    TextContentBlock,
)
from vibe.cli.textual_ui.widgets.messages import AssistantMessage, UserMessage
from vibe.cli.textual_ui.widgets.tools import ToolGroup
from vibe.cli.textual_ui.windowing.history import (
    build_history_widgets,
    visible_history_indices,
    visible_history_widgets_count,
)
from vibe.utils.tool_presentation import EffectCallDisplay, ToolEffectKind

_DETAIL_CLASSES = {
    ToolEffectKind.FILE_READ: FileReadEffectDetail,
    ToolEffectKind.SHELL: ShellEffectDetail,
}


def _message(index: int, content: str = "hello") -> PublicMessageEntry:
    return PublicMessageEntry(
        id=f"msg-{index}",
        session_id="s1",
        created_at=index,
        updated_at=index,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="assistant",
        content=[TextContentBlock(text=content)],
    )


def _effect(
    index: int, kind: ToolEffectKind = ToolEffectKind.FILE_READ
) -> PublicEffectEntry:
    detail_cls = _DETAIL_CLASSES.get(kind, FileReadEffectDetail)
    detail = detail_cls(
        tool_name="test_tool",
        display=EffectCallDisplay(summary="reading", status_text="running"),
    )
    return PublicEffectEntry(
        id=f"eff-{index}",
        session_id="s1",
        created_at=index,
        updated_at=index,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title="test",
        detail=detail,
        state=CompletedEffectState(
            duration_ms=1.0, display=EffectResultDisplay(success=True, message="ok")
        ),
    )


def _indices() -> WeakKeyDictionary:
    return WeakKeyDictionary()


def test_history_reconstructs_group_for_consecutive_effects() -> None:
    batch: list[PublicHistoryEntry] = [_effect(0), _effect(1), _effect(2)]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    assert len(widgets) == 1
    group = widgets[0]
    assert isinstance(group, ToolGroup)
    assert group.is_collapsed is True
    content_children = list(group.content_container.children)
    assert len(content_children) == 6  # 3 calls + 3 results


def test_history_assistant_message_breaks_group() -> None:
    batch: list[PublicHistoryEntry] = [
        _effect(0),
        _effect(1),
        _message(2, "done"),
        _effect(3),
    ]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    assert len(widgets) == 3
    assert isinstance(widgets[0], ToolGroup)
    assert isinstance(widgets[1], AssistantMessage)
    assert isinstance(widgets[2], ToolGroup)


def test_history_file_edit_grouped_with_reads() -> None:
    batch: list[PublicHistoryEntry] = [
        _effect(0, ToolEffectKind.FILE_READ),
        _effect(1, ToolEffectKind.FILE_EDIT),
    ]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    # FILE_EDIT is grouped with other effects: both live and history paths
    # treat all consecutive effects as one group.
    assert len(widgets) == 1
    assert isinstance(widgets[0], ToolGroup)


def test_history_user_message_not_inside_group() -> None:
    batch: list[PublicHistoryEntry] = [
        _effect(0),
        PublicMessageEntry(
            id="u1",
            session_id="s1",
            created_at=1,
            updated_at=1,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            role="user",
            content=[TextContentBlock(text="hi")],
        ),
    ]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    assert len(widgets) == 2
    assert isinstance(widgets[0], ToolGroup)
    assert isinstance(widgets[1], UserMessage)


def test_history_group_has_categories() -> None:
    batch: list[PublicHistoryEntry] = [
        _effect(0, ToolEffectKind.FILE_READ),
        _effect(1, ToolEffectKind.SHELL),
    ]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    group = widgets[0]
    assert isinstance(group, ToolGroup)
    assert "Read files" in group.header.get_content()
    assert "ran commands" in group.header.get_content()


def test_visible_history_widgets_count_includes_tool_group() -> None:
    batch: list[PublicHistoryEntry] = [_effect(0), _effect(1), _message(2, "done")]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    count = visible_history_widgets_count(widgets)
    assert count == 2  # 1 ToolGroup + 1 AssistantMessage


def test_visible_history_indices_descends_into_group() -> None:
    indices = _indices()
    batch: list[PublicHistoryEntry] = [_effect(0), _effect(1), _message(2, "done")]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=indices, tools_collapsed=True
    )
    visible = visible_history_indices(widgets, indices)
    # Group wraps entries at indices 0 and 1; their children are indexed.
    # The assistant message at index 2 is indexed directly.
    assert 0 in visible
    assert 1 in visible
    assert 2 in visible


def test_history_group_collapsed_state_follows_tools_collapsed() -> None:
    batch: list[PublicHistoryEntry] = [_effect(0)]
    widgets_collapsed = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    group_collapsed = widgets_collapsed[0]
    assert isinstance(group_collapsed, ToolGroup)
    assert group_collapsed.is_collapsed is True

    widgets_expanded = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=False
    )
    group_expanded = widgets_expanded[0]
    assert isinstance(group_expanded, ToolGroup)
    assert group_expanded.is_collapsed is False


def _manual_shell(index: int) -> PublicEffectEntry:
    """The entry a `!<command>` produces, named the way both backends name it."""
    entry = _effect(index, ToolEffectKind.SHELL)
    entry.detail.tool_name = MANUAL_SHELL_TOOL_NAME
    return entry


def test_history_manual_shell_stays_out_of_the_tool_group() -> None:
    # The user typed the command to read what it printed, so it is not folded
    # away behind a summary line covering work they never asked to see.
    batch: list[PublicHistoryEntry] = [_manual_shell(0)]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    assert not any(isinstance(widget, ToolGroup) for widget in widgets)


def test_history_manual_shell_breaks_a_group_of_agent_effects() -> None:
    batch: list[PublicHistoryEntry] = [_effect(0), _manual_shell(1), _effect(2)]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    groups = [i for i, widget in enumerate(widgets) if isinstance(widget, ToolGroup)]
    assert len(groups) == 2, "the agent's effects either side should not share a group"
    assert not any(
        isinstance(widget, ToolGroup) for widget in widgets[groups[0] + 1 : groups[1]]
    )


def test_history_agent_shell_call_still_groups() -> None:
    # Same SHELL kind as a `!` command: only the tool name separates them.
    batch: list[PublicHistoryEntry] = [
        _effect(0, ToolEffectKind.SHELL),
        _effect(1, ToolEffectKind.SHELL),
    ]
    widgets = build_history_widgets(
        batch, start_index=0, history_widget_indices=_indices(), tools_collapsed=True
    )
    assert len(widgets) == 1
    assert isinstance(widgets[0], ToolGroup)
