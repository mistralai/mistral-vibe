from __future__ import annotations

from weakref import WeakKeyDictionary

from textual.widget import Widget

from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    ReasoningMessage,
    UserMessage,
)
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.core.types import LLMMessage, Role


def non_system_history_messages(messages: list[LLMMessage]) -> list[LLMMessage]:
    return [msg for msg in messages if msg.role != Role.system]


def build_tool_call_map(messages: list[LLMMessage]) -> dict[str, str]:
    tool_call_map: dict[str, str] = {}
    for msg in messages:
        if msg.role != Role.assistant or not msg.tool_calls:
            continue
        for tool_call in msg.tool_calls:
            if tool_call.id:
                tool_call_map[tool_call.id] = tool_call.function.name or "unknown"
    return tool_call_map


def build_history_widgets(
    batch: list[LLMMessage],
    tool_call_map: dict[str, str],
    *,
    start_index: int,
    tools_collapsed: bool,
    history_widget_indices: WeakKeyDictionary[Widget, int],
) -> list[Widget]:
    widgets: list[Widget] = []

    for offset, msg in enumerate(batch):
        history_index = start_index + offset
        match msg.role:
            case Role.user:
                if msg.content:
                    widget = UserMessage(msg.content)
                    widgets.append(widget)
                    history_widget_indices[widget] = history_index

            case Role.assistant:
                if msg.content:
                    assistant_widget = AssistantMessage(msg.content)
                    widgets.append(assistant_widget)
                    history_widget_indices[assistant_widget] = history_index

                if msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        tool_name = tool_call.function.name or "unknown"
                        if tool_call.id:
                            tool_call_map[tool_call.id] = tool_name
                        widget = ToolCallMessage(tool_name=tool_name)
                        widgets.append(widget)
                        history_widget_indices[widget] = history_index

            case Role.tool:
                tool_name = msg.name or tool_call_map.get(
                    msg.tool_call_id or "", "tool"
                )
                widget = ToolResultMessage(
                    tool_name=tool_name, content=msg.content, collapsed=tools_collapsed
                )
                widgets.append(widget)
                history_widget_indices[widget] = history_index

    return widgets


def split_history_tail(
    history_messages: list[LLMMessage], tail_size: int
) -> tuple[list[LLMMessage], list[LLMMessage], int]:
    tail_messages = history_messages[-tail_size:]
    backfill_messages = history_messages[:-tail_size]
    tail_start_index = len(history_messages) - len(tail_messages)
    return tail_messages, backfill_messages, tail_start_index


def visible_history_indices(
    children: list[Widget], history_widget_indices: WeakKeyDictionary[Widget, int]
) -> list[int]:
    return [
        idx
        for child in children
        if (idx := history_widget_indices.get(child)) is not None
    ]


def visible_history_widgets_count(children: list[Widget]) -> int:
    history_widget_types = (
        UserMessage,
        AssistantMessage,
        ReasoningMessage,
        ToolCallMessage,
        ToolResultMessage,
    )
    return sum(isinstance(child, history_widget_types) for child in children)
