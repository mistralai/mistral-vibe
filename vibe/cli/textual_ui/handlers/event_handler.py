from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.messages import AssistantMessage, ReasoningMessage
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import (
    AssistantEvent,
    BaseEvent,
    CompactEndEvent,
    CompactStartEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserMessageEvent,
)
from vibe.core.utils import TaggedText

if TYPE_CHECKING:
    from vibe.cli.textual_ui.widgets.loading import LoadingWidget


class EventHandler:
    def __init__(
        self, mount_callback: Callable, get_tools_collapsed: Callable[[], bool]
    ) -> None:
        self.mount_callback = mount_callback
        self.get_tools_collapsed = get_tools_collapsed
        self.tool_calls: dict[str, ToolCallMessage] = {}
        self.current_compact: CompactMessage | None = None
        self.current_streaming_message: AssistantMessage | None = None
        self.current_streaming_reasoning: ReasoningMessage | None = None

    async def handle_event(
        self,
        event: BaseEvent,
        loading_active: bool = False,
        loading_widget: LoadingWidget | None = None,
    ) -> ToolCallMessage | None:
        match event:
            case ReasoningEvent():
                await self._handle_reasoning_message(event)
            case AssistantEvent():
                await self._handle_assistant_message(event)
            case ToolCallEvent():
                await self.finalize_streaming()
                return await self._handle_tool_call(event, loading_widget)
            case ToolResultEvent():
                await self.finalize_streaming()
                sanitized_event = self._sanitize_event(event)
                await self._handle_tool_result(sanitized_event)
            case ToolStreamEvent():
                await self._handle_tool_stream(event)
            case CompactStartEvent():
                await self.finalize_streaming()
                await self._handle_compact_start()
            case CompactEndEvent():
                await self.finalize_streaming()
                await self._handle_compact_end(event)
            case UserMessageEvent():
                await self.finalize_streaming()
            case _:
                await self.finalize_streaming()
                await self._handle_unknown_event(event)
        return None

    def _sanitize_event(self, event: ToolResultEvent) -> ToolResultEvent:
        if isinstance(event, ToolResultEvent):
            return ToolResultEvent(
                tool_name=event.tool_name,
                tool_class=event.tool_class,
                result=event.result,
                error=TaggedText.from_string(event.error).message
                if event.error
                else None,
                skipped=event.skipped,
                skip_reason=TaggedText.from_string(event.skip_reason).message
                if event.skip_reason
                else None,
                duration=event.duration,
                tool_call_id=event.tool_call_id,
            )
        return event

    async def _handle_tool_call(
        self, event: ToolCallEvent, loading_widget: LoadingWidget | None = None
    ) -> ToolCallMessage | None:
        tool_call_id = event.tool_call_id
        existing_tool_call = self.tool_calls.get(tool_call_id) if tool_call_id else None
        if existing_tool_call:
            existing_tool_call.update_event(event)
            tool_call = existing_tool_call
        else:
            tool_call = ToolCallMessage(event)
            if tool_call_id:
                self.tool_calls[tool_call_id] = tool_call
            await self.mount_callback(tool_call)

        if loading_widget and event.tool_class:
            adapter = ToolUIDataAdapter(event.tool_class)
            loading_widget.set_status(adapter.get_status_text())

        return tool_call

    async def _handle_tool_result(self, event: ToolResultEvent) -> None:
        tools_collapsed = self.get_tools_collapsed()

        call_widget = (
            self.tool_calls.get(event.tool_call_id) if event.tool_call_id else None
        )

        tool_result = ToolResultMessage(event, call_widget, collapsed=tools_collapsed)
        await self.mount_callback(tool_result, after=call_widget)

        if event.tool_call_id and event.tool_call_id in self.tool_calls:
            del self.tool_calls[event.tool_call_id]

    async def _handle_tool_stream(self, event: ToolStreamEvent) -> None:
        tool_call = self.tool_calls.get(event.tool_call_id)
        if tool_call:
            tool_call.set_stream_message(event.message)

    async def _handle_assistant_message(self, event: AssistantEvent) -> None:
        if self.current_streaming_reasoning is not None:
            self.current_streaming_reasoning.stop_spinning()
            await self.current_streaming_reasoning.stop_stream()
            self.current_streaming_reasoning = None

        if self.current_streaming_message is None:
            msg = AssistantMessage(event.content)
            self.current_streaming_message = msg
            await self.mount_callback(msg)
        else:
            await self.current_streaming_message.append_content(event.content)

    async def _handle_reasoning_message(self, event: ReasoningEvent) -> None:
        if self.current_streaming_message is not None:
            await self.current_streaming_message.stop_stream()
            if self.current_streaming_message.is_stripped_content_empty():
                await self.current_streaming_message.remove()
            self.current_streaming_message = None

        if self.current_streaming_reasoning is None:
            tools_collapsed = self.get_tools_collapsed()
            msg = ReasoningMessage(event.content, collapsed=tools_collapsed)
            self.current_streaming_reasoning = msg
            await self.mount_callback(msg)
        else:
            await self.current_streaming_reasoning.append_content(event.content)

    async def _handle_compact_start(self) -> None:
        compact_msg = CompactMessage()
        self.current_compact = compact_msg
        await self.mount_callback(compact_msg)

    async def _handle_compact_end(self, event: CompactEndEvent) -> None:
        if self.current_compact:
            self.current_compact.set_complete(
                old_tokens=event.old_context_tokens, new_tokens=event.new_context_tokens
            )
            self.current_compact = None

    async def _handle_unknown_event(self, event: BaseEvent) -> None:
        await self.mount_callback(NoMarkupStatic(str(event), classes="unknown-event"))

    async def finalize_streaming(self) -> None:
        if self.current_streaming_reasoning is not None:
            self.current_streaming_reasoning.stop_spinning()
            await self.current_streaming_reasoning.stop_stream()
            self.current_streaming_reasoning = None
        if self.current_streaming_message is not None:
            await self.current_streaming_message.stop_stream()
            self.current_streaming_message = None

    def stop_current_tool_call(self, success: bool = True) -> None:
        for tool_call in self.tool_calls.values():
            tool_call.stop_spinning(success=success)
        self.tool_calls.clear()

    def stop_current_compact(self) -> None:
        if self.current_compact:
            self.current_compact.stop_spinning(success=False)
            self.current_compact = None
