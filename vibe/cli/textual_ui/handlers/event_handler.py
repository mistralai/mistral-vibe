from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from textual.widget import Widget

from vibe.app_server.events import (
    AppServerEvent,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    TurnRetrying,
)
from vibe.app_server.models import (
    AgentChangedNoticeDetail,
    CancelledEffectState,
    CompletedEffectState,
    ContextClearedNoticeDetail,
    FailedEffectState,
    HookNoticeDetail,
    HookScope,
    HookSeverity,
    JsonPatchOperation,
    PlanReviewEndedNoticeDetail,
    PlanReviewStartedNoticeDetail,
    PublicCallbackEntry,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
    PublicReasoningEntry,
    ScheduledLoopFiredNoticeDetail,
    SessionTitleUpdatedNoticeDetail,
    SkippedEffectState,
    WaitingForInputNoticeDetail,
)
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.loading import (
    DEFAULT_LOADING_STATUS,
    RETRYING_LOADING_STATUS,
    THINKING_LOADING_STATUS,
)
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    ErrorMessage,
    HookRunContainer,
    HookSystemMessageLine,
    PlanFileMessage,
    ReasoningMessage,
    SlashCommandMessage,
    UserCommandMessage,
)
from vibe.cli.textual_ui.widgets.tools import (
    ToolCallMessage,
    ToolGroup,
    ToolResultMessage,
)
from vibe.utils.tool_presentation import ToolEffectKind

if TYPE_CHECKING:
    from vibe.cli.textual_ui.widgets.loading import LoadingWidget


_NON_GROUPED_EFFECT_KINDS = frozenset({
    ToolEffectKind.FILE_EDIT,
    ToolEffectKind.FILE_WRITE,
})


@dataclass(slots=True)
class _RetryPresentation:
    assistant: AssistantMessage | None
    transient_widgets: list[Widget] = field(default_factory=list)
    active: bool = False


class EventHandler:
    def __init__(
        self,
        mount_callback: Callable,
        get_tools_collapsed: Callable[[], bool],
        on_profile_changed: Callable[[], None] | None = None,
        get_show_thinking: Callable[[], bool] | None = None,
        on_context_cleared: Callable[[Path | None], Awaitable[None]] | None = None,
    ) -> None:
        self.mount_callback = mount_callback
        self.get_tools_collapsed = get_tools_collapsed
        self.on_profile_changed = on_profile_changed
        self.get_show_thinking = get_show_thinking or (lambda: True)
        self.on_context_cleared = on_context_cleared
        self.tool_calls: dict[str, ToolCallMessage] = {}
        self.current_compact: CompactMessage | None = None
        self.current_streaming_message: AssistantMessage | None = None
        self._turn_assistant_message: AssistantMessage | None = None
        self.current_streaming_reasoning: ReasoningMessage | None = None
        self.current_tool_group: ToolGroup | None = None
        self.plan_file_message: PlanFileMessage | None = None
        self._hook_containers: dict[str, HookRunContainer] = {}
        self._tool_call_anchors: dict[str, Widget] = {}
        self._pending_error_results: list[ToolResultMessage] = []
        self._retry_presentation: _RetryPresentation | None = None

    def offer_retry(self, error: ErrorMessage | None = None) -> None:
        presentation = self._retry_presentation
        if presentation is None:
            presentation = _RetryPresentation(self._turn_assistant_message)
            self._retry_presentation = presentation
        elif presentation.assistant is None:
            presentation.assistant = self._turn_assistant_message
        if error is not None:
            presentation.transient_widgets.append(error)
        presentation.active = False

    def begin_retry(self, command: SlashCommandMessage | None = None) -> bool:
        if self._retry_presentation is None:
            return False
        if command is not None:
            self._retry_presentation.transient_widgets.append(command)
        self._retry_presentation.active = True
        return True

    def cancel_retry_presentation(self) -> None:
        self._retry_presentation = None
        self._turn_assistant_message = None

    async def handle_event(
        self, event: AppServerEvent, loading_widget: LoadingWidget | None = None
    ) -> ToolCallMessage | None:
        match event:
            case HistoryEntryAdded(entry=entry):
                return await self._handle_entry_added(entry, loading_widget)
            case HistoryEntryUpdated() as update:
                await self._handle_entry_updated(update, loading_widget)
            case TurnRetrying() if loading_widget is not None:
                loading_widget.set_status(RETRYING_LOADING_STATUS)
            case _:
                pass
        return None

    async def _handle_entry_added(
        self, entry: PublicHistoryEntry, loading_widget: LoadingWidget | None
    ) -> ToolCallMessage | None:
        if self.current_tool_group is not None and not _entry_keeps_tool_group(entry):
            self._finalize_tool_group()

        match entry:
            case PublicMessageEntry(role="assistant"):
                await self._handle_assistant_delta(entry.text, loading_widget)
                if entry.generation_status is PublicEntryGenerationStatus.COMPLETED:
                    await self.finalize_streaming()
            case PublicMessageEntry(role="user"):
                self.cancel_retry_presentation()
                await self.finalize_streaming()
            case PublicMessageEntry(role="system"):
                await self._resolve_retry_presentation(continue_assistant=False)
            case PublicReasoningEntry():
                await self._resolve_retry_presentation(continue_assistant=False)
                await self._handle_reasoning_delta(entry.text, loading_widget)
                if entry.generation_status is PublicEntryGenerationStatus.COMPLETED:
                    await self.finalize_streaming()
            case PublicEffectEntry():
                await self._resolve_retry_presentation(continue_assistant=False)
                await self.finalize_streaming()
                tool_call = await self._handle_effect_started(entry, loading_widget)
                if _effect_is_terminal(entry):
                    await self._handle_effect_completed(entry, loading_widget)
                return tool_call
            case PublicCheckpointEntry(kind="compaction"):
                await self._resolve_retry_presentation(continue_assistant=False)
                await self.finalize_streaming()
                await self._handle_compact_start()
                if entry.generation_status is PublicEntryGenerationStatus.COMPLETED:
                    await self._handle_compact_end()
            case PublicNoticeEntry():
                await self._resolve_retry_presentation(continue_assistant=False)
                await self._handle_notice(entry, loading_widget)
            case PublicCallbackEntry():
                await self._resolve_retry_presentation(continue_assistant=False)
                await self.finalize_streaming()
            case _:
                raise TypeError(
                    f"Unsupported public history entry: {type(entry).__name__}"
                )
        return None

    async def _handle_entry_updated(
        self, update: HistoryEntryUpdated, loading_widget: LoadingWidget | None
    ) -> None:
        entry = update.entry
        match entry:
            case PublicMessageEntry(role="assistant"):
                if delta := _appended_text(update.patch, "/content/0/text"):
                    await self._handle_assistant_delta(delta, loading_widget)
                if entry.generation_status is PublicEntryGenerationStatus.COMPLETED:
                    await self.finalize_streaming()
            case PublicReasoningEntry():
                if delta := _appended_text(update.patch, "/text"):
                    await self._handle_reasoning_delta(delta, loading_widget)
                if entry.generation_status is PublicEntryGenerationStatus.COMPLETED:
                    await self.finalize_streaming()
            case PublicEffectEntry():
                call = self.tool_calls.get(entry.id)
                if call is not None:
                    call.update_entry(entry)
                    if output := _appended_text(update.patch, "/state/outputText"):
                        call.set_stream_message(output)
                if _effect_became_terminal(update):
                    await self.finalize_streaming()
                    await self._handle_effect_completed(entry, loading_widget)
                elif loading_widget is not None:
                    loading_widget.set_status(entry.detail.display.status_text)
            case PublicCheckpointEntry(kind="compaction"):
                if entry.generation_status is PublicEntryGenerationStatus.COMPLETED:
                    await self.finalize_streaming()
                    await self._handle_compact_end()
            case _:
                pass

    async def _handle_effect_started(
        self, entry: PublicEffectEntry, loading_widget: LoadingWidget | None
    ) -> ToolCallMessage:
        existing = self.tool_calls.get(entry.id)
        if existing is not None:
            existing.update_entry(entry)
            tool_call = existing
        else:
            self._resolve_pending_errors(escalate=False)
            tool_call = ToolCallMessage(entry)
            self.tool_calls[entry.id] = tool_call
            self._tool_call_anchors[entry.id] = tool_call
            if entry.detail.kind in _NON_GROUPED_EFFECT_KINDS:
                self._finalize_tool_group()
                await self.mount_callback(tool_call)
            else:
                group = await self._ensure_tool_group()
                await self._mount_in_group(group, tool_call)
        if loading_widget is not None:
            loading_widget.set_status(entry.detail.display.status_text)
        return tool_call

    async def _handle_effect_completed(
        self, entry: PublicEffectEntry, loading_widget: LoadingWidget | None
    ) -> None:
        call_widget = self.tool_calls.get(entry.id)
        anchor = self._tool_call_anchors.get(entry.id) or call_widget
        result = ToolResultMessage(entry, call_widget)
        await self.mount_callback(result, after=anchor)
        if isinstance(entry.state, FailedEffectState):
            self._pending_error_results.append(result)
        self._tool_call_anchors[entry.id] = result
        self.tool_calls.pop(entry.id, None)
        if loading_widget is not None and not self.tool_calls:
            loading_widget.set_status(DEFAULT_LOADING_STATUS)

    async def _handle_assistant_delta(
        self, content: str, loading_widget: LoadingWidget | None
    ) -> None:
        if self.current_streaming_reasoning is not None:
            self.current_streaming_reasoning.stop_spinning()
            await self.current_streaming_reasoning.stop_stream()
            self.current_streaming_reasoning = None
        if self.current_streaming_message is None:
            if loading_widget is not None:
                loading_widget.set_status(DEFAULT_LOADING_STATUS)
            message = await self._resolve_retry_presentation(continue_assistant=True)
            if message is not None:
                self.current_streaming_message = message
                self._turn_assistant_message = message
                await message.append_content(content)
                return
            message = AssistantMessage(content)
            self.current_streaming_message = message
            self._turn_assistant_message = message
            await self.mount_callback(message)
            return
        await self.current_streaming_message.append_content(content)

    async def _resolve_retry_presentation(
        self, *, continue_assistant: bool
    ) -> AssistantMessage | None:
        presentation = self._retry_presentation
        if presentation is None or not presentation.active:
            return None
        self._retry_presentation = None
        for widget in presentation.transient_widgets:
            if widget.parent is not None:
                await widget.remove()
        assistant = presentation.assistant
        if not continue_assistant or assistant is None or assistant.parent is None:
            return None
        return assistant

    async def _handle_reasoning_delta(
        self, content: str, loading_widget: LoadingWidget | None
    ) -> None:
        if loading_widget is not None:
            loading_widget.set_status(THINKING_LOADING_STATUS)
        if self.current_streaming_message is not None:
            await self.current_streaming_message.stop_stream()
            if self.current_streaming_message.is_stripped_content_empty():
                await self.current_streaming_message.remove()
            self.current_streaming_message = None
        if self.current_streaming_reasoning is None:
            message = ReasoningMessage(content, collapsed=self.get_tools_collapsed())
            self.current_streaming_reasoning = message
            group = await self._ensure_tool_group()
            await self._mount_in_group(group, message)
            if not self.get_show_thinking():
                message.display = False
                group.sync_visibility()
            return
        await self.current_streaming_reasoning.append_content(content)

    async def _handle_notice(
        self, entry: PublicNoticeEntry, loading_widget: LoadingWidget | None
    ) -> None:
        match entry.detail:
            case HookNoticeDetail() as detail:
                await self._handle_hook_notice(detail, loading_widget)
            case AgentChangedNoticeDetail():
                if self.on_profile_changed is not None:
                    self.on_profile_changed()
            case ContextClearedNoticeDetail(plan_file_path=plan_file_path):
                await self.finalize_streaming()
                if self.on_context_cleared is not None:
                    path = Path(plan_file_path) if plan_file_path is not None else None
                    await self.on_context_cleared(path)
            case PlanReviewStartedNoticeDetail(file_path=file_path):
                await self._handle_start_plan_review(Path(file_path))
            case PlanReviewEndedNoticeDetail():
                self._handle_stop_plan_review()
            case WaitingForInputNoticeDetail():
                await self.finalize_streaming()
            case ScheduledLoopFiredNoticeDetail():
                await self.finalize_streaming()
                await self.mount_callback(UserCommandMessage(entry.message))
            case SessionTitleUpdatedNoticeDetail():
                pass

    async def _handle_hook_notice(
        self, detail: HookNoticeDetail, loading_widget: LoadingWidget | None
    ) -> None:
        match detail.kind:
            case "hook_run_started":
                await self._handle_hook_run_start(detail)
            case "hook_run_completed":
                await self._handle_hook_run_end(detail)
            case "hook_started":
                await self.finalize_streaming()
                if loading_widget is not None:
                    loading_widget.set_status(
                        f"Running hook {detail.hook_name or ''}".rstrip()
                    )
            case "hook_completed":
                await self._handle_hook_completed(detail)
                if loading_widget is not None:
                    loading_widget.set_status(DEFAULT_LOADING_STATUS)

    @staticmethod
    def _hook_container_key(scope: HookScope, tool_call_id: str | None) -> str:
        if scope is HookScope.POST_AGENT:
            return "agent_turn"
        return f"{scope.value}:{tool_call_id or ''}"

    async def _handle_hook_run_start(self, details: HookNoticeDetail) -> None:
        container = HookRunContainer()
        key = self._hook_container_key(details.scope, details.tool_call_id)
        self._hook_containers[key] = container
        if details.scope is HookScope.PRE_TOOL:
            container.add_class("hook-before-tool")
        anchor = self._tool_call_anchors.get(details.tool_call_id or "")
        if details.scope is HookScope.PRE_TOOL and anchor is not None:
            await self.mount_callback(container, before=anchor)
        elif details.scope is HookScope.POST_TOOL and anchor is not None:
            await self.mount_callback(container, after=anchor)
        else:
            await self.mount_callback(container)

    async def _handle_hook_run_end(self, details: HookNoticeDetail) -> None:
        key = self._hook_container_key(details.scope, details.tool_call_id)
        container = self._hook_containers.pop(key, None)
        if container is None:
            return
        if container.display:
            if details.scope is not HookScope.PRE_TOOL and details.tool_call_id:
                self._tool_call_anchors[details.tool_call_id] = container
            return
        await container.remove()

    async def _handle_hook_completed(self, details: HookNoticeDetail) -> None:
        if not details.content or details.hook_name is None:
            return
        key = self._hook_container_key(details.scope, details.tool_call_id)
        container = self._hook_containers.get(key)
        if container is None:
            return
        await container.add_message(
            HookSystemMessageLine(
                hook_name=details.hook_name,
                content=details.content,
                severity=details.status or HookSeverity.WARNING,
            )
        )
        if details.scope is HookScope.PRE_TOOL and details.tool_call_id:
            tool_call = self.tool_calls.get(details.tool_call_id)
            if tool_call is not None:
                tool_call.add_class("no-gap")

    def _finalize_tool_group(self) -> None:
        self.current_tool_group = None

    async def _ensure_tool_group(self) -> ToolGroup:
        if self.current_tool_group is None:
            group = ToolGroup()
            self.current_tool_group = group
            await self.mount_callback(group)
        return self.current_tool_group

    async def _mount_in_group(self, group: ToolGroup, widget: Widget) -> None:
        children = list(group.content_container.children)
        if children:
            await self.mount_callback(widget, after=children[-1])
        else:
            await self.mount_callback(widget, container=group.content_container)
        group.sync_visibility()

    async def _handle_compact_start(self) -> None:
        compact = CompactMessage()
        self.current_compact = compact
        await self.mount_callback(compact)

    async def _handle_compact_end(self) -> None:
        if self.current_compact is None:
            return
        self.current_compact.set_complete()
        self.current_compact = None

    def _resolve_pending_errors(self, *, escalate: bool) -> None:
        if escalate:
            for result in self._pending_error_results:
                result.escalate_error()
        self._pending_error_results.clear()

    def escalate_unresolved_errors(self) -> None:
        self._resolve_pending_errors(escalate=True)

    async def finalize_streaming(self) -> None:
        if self.current_streaming_reasoning is not None:
            self.current_streaming_reasoning.stop_spinning()
            await self.current_streaming_reasoning.stop_stream()
            self.current_streaming_reasoning = None
        if self.current_streaming_message is not None:
            await self.current_streaming_message.stop_stream()
            self.current_streaming_message = None

    def stop_current_tool_call(
        self, success: bool = True, *, cancelled: bool = False
    ) -> None:
        for tool_call in self.tool_calls.values():
            if cancelled:
                tool_call.show_muted()
            else:
                tool_call.stop_spinning(success=success)
        self.tool_calls.clear()
        self._tool_call_anchors.clear()
        self._hook_containers.clear()
        self._finalize_tool_group()
        self._resolve_pending_errors(escalate=not cancelled)

    def stop_current_compact(self) -> None:
        if self.current_compact:
            self.current_compact.stop_spinning(success=False)
            self.current_compact = None

    async def _handle_start_plan_review(self, file_path: Path) -> None:
        file_path.touch()
        message = PlanFileMessage(file_path=file_path)
        self.plan_file_message = message
        await self.mount_callback(message)

    def _handle_stop_plan_review(self) -> None:
        if self.plan_file_message is None:
            return
        self.plan_file_message.stop_watching()
        self.plan_file_message = None


def _entry_keeps_tool_group(entry: PublicHistoryEntry) -> bool:
    return isinstance(entry, PublicEffectEntry | PublicReasoningEntry) or (
        isinstance(entry, PublicNoticeEntry)
        and isinstance(entry.detail, HookNoticeDetail)
    )


def _appended_text(patch: list[JsonPatchOperation], path: str) -> str:
    return "".join(
        operation.value
        for operation in patch
        if operation.op == "append"
        and operation.path == path
        and isinstance(operation.value, str)
    )


def _effect_is_terminal(entry: PublicEffectEntry) -> bool:
    return isinstance(
        entry.state,
        CompletedEffectState
        | FailedEffectState
        | CancelledEffectState
        | SkippedEffectState,
    )


def _effect_became_terminal(update: HistoryEntryUpdated) -> bool:
    return (
        isinstance(update.entry, PublicEffectEntry)
        and _effect_is_terminal(update.entry)
        and (
            not isinstance(update.previous, PublicEffectEntry)
            or not _effect_is_terminal(update.previous)
        )
    )
