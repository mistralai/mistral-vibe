from __future__ import annotations

from dataclasses import dataclass
from typing import Any, assert_never, cast
from uuid import uuid4

from vibe.app_server._patch import apply_json_patch
from vibe.app_server._projection import project_message_content
from vibe.app_server._root_session import rebind_history
from vibe.app_server._tool_projection import project_effect_detail, project_effect_state
from vibe.app_server._utils import now_ms
from vibe.app_server.models import (
    AgentChangedNoticeDetail,
    AgentSummary,
    BlockedEffectState,
    CallbackDetail,
    CallbackOutput,
    CancelledEffectState,
    ContextClearedNoticeDetail,
    EffectCallDisplay,
    EffectDetail,
    EffectResultDisplay,
    EffectState,
    FailedEffectState,
    GenericEffectDetail,
    HookNoticeDetail,
    HookScope,
    HookSeverity,
    JsonPatchOperation,
    NoticeDetail,
    OpenCallbackState,
    PlanReviewEndedNoticeDetail,
    PlanReviewStartedNoticeDetail,
    PublicCallbackEntry,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicError,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicMessageSource,
    PublicNoticeEntry,
    PublicReasoningEntry,
    RunningEffectState,
    SessionTitleUpdatedNoticeDetail,
    SubagentEffectDetail,
    TextContentBlock,
    WaitingForInputNoticeDetail,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    HistoryEntryAddedParams,
    HistoryEntryUpdatedParams,
    SessionUpdatedParams,
)
from vibe.core.hooks.models import (
    HookEndEvent,
    HookEvent,
    HookRunEndEvent,
    HookRunStartEvent,
    HookStartEvent,
)
from vibe.core.types import (
    AgentProfileChangedEvent,
    AssistantEvent,
    BaseEvent,
    CompactEndEvent,
    CompactStartEvent,
    ContextClearedEvent,
    PlanReviewEndedEvent,
    PlanReviewRequestedEvent,
    ReasoningEvent,
    SessionTitleUpdatedEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserMessageEvent,
    WaitingForInputEvent,
)
from vibe.user_content import UserResource, UserResourceLink


@dataclass(frozen=True, slots=True)
class ProjectedUpdate:
    method: str
    params: HistoryEntryAddedParams | HistoryEntryUpdatedParams | SessionUpdatedParams


class EventProjector:
    def __init__(
        self, session_id: str, turn_id: str | None, *, session_preview: str = ""
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self._session_preview = session_preview
        self.history: list[PublicHistoryEntry] = []
        self._entries: dict[str, PublicHistoryEntry] = {}
        self._assistant_entries: dict[str, str] = {}
        self._reasoning_entries: dict[str, str] = {}
        self._effect_entries: dict[str, str] = {}
        self._checkpoint_entries: dict[str, str] = {}
        self._callback_entries: dict[str, str] = {}

    def project(
        self,
        event: BaseEvent,
        *,
        user_message_source: PublicMessageSource = "turn_start",
    ) -> list[ProjectedUpdate]:
        match event:
            case UserMessageEvent():
                updates = self._project_user_message(event, user_message_source)
            case AssistantEvent():
                updates = [self._project_assistant(event)]
            case ReasoningEvent():
                updates = [self._project_reasoning(event)]
            case ToolCallEvent():
                updates = [
                    *self._complete_streamed_text(),
                    self._project_effect_started(event),
                ]
            case ToolStreamEvent():
                updates = [self._project_effect_output(event)]
            case ToolResultEvent():
                updates = self._project_effect_completed(event)
            case CompactStartEvent():
                updates = [self._project_compaction_started(event)]
            case CompactEndEvent():
                updates = [self._project_compaction_completed(event)]
            case HookEvent():
                updates = [self._project_hook(event)]
            case SessionTitleUpdatedEvent():
                updates = [
                    self._project_notice(event),
                    self._update_session([
                        JsonPatchOperation(
                            op="replace", path="/title", value=event.title
                        )
                    ]),
                ]
            case (
                WaitingForInputEvent()
                | AgentProfileChangedEvent()
                | ContextClearedEvent()
                | PlanReviewRequestedEvent()
                | PlanReviewEndedEvent()
            ):
                updates = [self._project_notice(event)]
            case _:
                updates = []
        return updates

    def project_agent(self, agent: AgentSummary) -> ProjectedUpdate:
        return self._update_session([
            JsonPatchOperation(
                op="replace",
                path="/agent",
                value=agent.model_dump(mode="json", by_alias=True),
            )
        ])

    def rebind_session(self, session_id: str) -> None:
        self.session_id = session_id
        self.history = rebind_history(self.history, session_id)
        self._entries = {entry.id: entry for entry in self.history}

    def link_subagent(
        self, tool_call_id: str, child_session_id: str
    ) -> list[ProjectedUpdate]:
        entry_id, _, detail = self._subagent_effect(tool_call_id)
        if detail.child_session_id == child_session_id:
            return []
        if detail.child_session_id is not None:
            raise ValueError(f"Subagent effect is already linked: {tool_call_id}")
        return [
            self._patch(
                entry_id,
                [
                    JsonPatchOperation(
                        op="replace",
                        path="/detail/childSessionId",
                        value=child_session_id,
                    )
                ],
            )
        ]

    def unlink_subagent(
        self, tool_call_id: str, child_session_id: str
    ) -> list[ProjectedUpdate]:
        entry_id, _, detail = self._subagent_effect(tool_call_id)
        if detail.child_session_id is None:
            return []
        if detail.child_session_id != child_session_id:
            raise ValueError(f"Subagent effect links another child: {tool_call_id}")
        return [
            self._patch(
                entry_id,
                [
                    JsonPatchOperation(
                        op="replace", path="/detail/childSessionId", value=None
                    )
                ],
            )
        ]

    def _subagent_effect(
        self, tool_call_id: str
    ) -> tuple[str, PublicEffectEntry, SubagentEffectDetail]:
        entry_id = self._effect_entries.get(tool_call_id)
        if entry_id is None:
            raise ValueError(f"Subagent effect not found: {tool_call_id}")
        entry = self._entries[entry_id]
        if not isinstance(entry, PublicEffectEntry) or not isinstance(
            entry.detail, SubagentEffectDetail
        ):
            raise ValueError(f"Effect is not a subagent: {tool_call_id}")
        return entry_id, entry, entry.detail

    def effect_detail(self, tool_call_id: str) -> EffectDetail:
        entry_id = self._effect_entries.get(tool_call_id)
        if entry_id is None:
            raise ValueError(f"Effect not found: {tool_call_id}")
        entry = self._entries[entry_id]
        if not isinstance(entry, PublicEffectEntry):
            raise ValueError(f"History entry is not an effect: {tool_call_id}")
        return entry.detail

    def start_effect(
        self, entry_id: str, *, title: str, detail: EffectDetail
    ) -> ProjectedUpdate:
        if existing_id := self._effect_entries.get(entry_id):
            return self._patch(
                existing_id,
                [
                    JsonPatchOperation(
                        op="replace",
                        path="/detail",
                        value=detail.model_dump(mode="json", by_alias=True),
                    )
                ],
            )
        self._effect_entries[entry_id] = entry_id
        return self._add(
            PublicEffectEntry(
                **self._entry_fields(entry_id, PublicEntryGenerationStatus.IN_PROGRESS),
                title=title,
                detail=detail,
                state=RunningEffectState(),
            )
        )

    def add_notice(
        self, entry_id: str, *, message: str, detail: NoticeDetail
    ) -> ProjectedUpdate:
        return self._add(
            PublicNoticeEntry(
                **self._entry_fields(entry_id, PublicEntryGenerationStatus.COMPLETED),
                level="info",
                message=message,
                detail=detail,
            )
        )

    def append_effect_output(self, entry_id: str, text: str) -> ProjectedUpdate:
        effect_id = self._effect_entries.get(entry_id)
        if effect_id is None:
            raise ValueError(f"Effect not found: {entry_id}")
        return self._patch(
            effect_id,
            [JsonPatchOperation(op="append", path="/state/outputText", value=text)],
        )

    def complete_effect(self, entry_id: str, state: EffectState) -> ProjectedUpdate:
        effect_id = self._effect_entries.get(entry_id)
        if effect_id is None:
            raise ValueError(f"Effect not found: {entry_id}")
        return self._patch(
            effect_id,
            [
                JsonPatchOperation(
                    op="replace",
                    path="/state",
                    value=state.model_dump(mode="json", by_alias=True),
                ),
                JsonPatchOperation(
                    op="replace", path="/generationStatus", value="completed"
                ),
            ],
        )

    def _complete_streamed_text(self) -> list[ProjectedUpdate]:
        entry_ids = {
            *self._assistant_entries.values(),
            *self._reasoning_entries.values(),
        }
        return [
            self._patch(
                entry.id,
                [
                    JsonPatchOperation(
                        op="replace", path="/generationStatus", value="completed"
                    )
                ],
            )
            for entry in self.history
            if entry.id in entry_ids
            and entry.generation_status is PublicEntryGenerationStatus.IN_PROGRESS
        ]

    def open_callback(
        self, callback_id: str, detail: CallbackDetail, title: str
    ) -> list[ProjectedUpdate]:
        updates: list[ProjectedUpdate] = []
        related_entry_id = detail.related_entry_id
        entry_id = f"callback:{callback_id}"
        self._callback_entries[callback_id] = entry_id
        entry = PublicCallbackEntry(
            **self._entry_fields(entry_id, PublicEntryGenerationStatus.IN_PROGRESS),
            callback_id=callback_id,
            title=title,
            detail=detail,
            state=OpenCallbackState(),
            related_entry_id=related_entry_id,
        )
        updates.append(self._add(entry))
        if related_entry_id in self._entries:
            updates.append(
                self._patch(
                    cast(str, related_entry_id),
                    [
                        JsonPatchOperation(
                            op="replace",
                            path="/state",
                            value=BlockedEffectState(
                                callback_id=callback_id
                            ).model_dump(mode="json", by_alias=True),
                        )
                    ],
                )
            )
        return updates

    def resolve_callback(
        self, callback_id: str, output: CallbackOutput
    ) -> list[ProjectedUpdate]:
        entry_id = self._callback_entries.get(callback_id)
        if entry_id is None:
            return []
        entry = cast(PublicCallbackEntry, self._entries[entry_id])
        updates = [
            self._patch(
                entry_id,
                [
                    JsonPatchOperation(
                        op="replace",
                        path="/state",
                        value={
                            "status": "answered",
                            "output": output.model_dump(mode="json", by_alias=True),
                        },
                    ),
                    JsonPatchOperation(
                        op="replace", path="/generationStatus", value="completed"
                    ),
                ],
            )
        ]
        if entry.related_entry_id in self._entries:
            effect = self._entries[cast(str, entry.related_entry_id)]
            if isinstance(effect, PublicEffectEntry):
                output_text = (
                    effect.state.output_text
                    if isinstance(effect.state, BlockedEffectState)
                    else ""
                )
                updates.append(
                    self._patch(
                        effect.id,
                        [
                            JsonPatchOperation(
                                op="replace",
                                path="/state",
                                value=RunningEffectState(
                                    output_text=output_text
                                ).model_dump(mode="json", by_alias=True),
                            )
                        ],
                    )
                )
        return updates

    def finalize(self, *, cancelled: bool = False) -> list[ProjectedUpdate]:
        updates: list[ProjectedUpdate] = []
        for entry in list(self._entries.values()):
            if entry.generation_status is PublicEntryGenerationStatus.COMPLETED:
                continue
            operations: list[JsonPatchOperation] = []
            if isinstance(entry, PublicEffectEntry):
                reason = "Turn interrupted" if cancelled else "Turn ended"
                display = EffectResultDisplay(success=False, message=reason)
                state = (
                    CancelledEffectState(
                        reason=reason,
                        output_text=_effect_output_text(entry),
                        display=display,
                    )
                    if cancelled
                    else FailedEffectState(
                        error=PublicError(message=reason),
                        output_text=_effect_output_text(entry),
                        display=display,
                    )
                )
                operations.append(
                    JsonPatchOperation(
                        op="replace",
                        path="/state",
                        value=state.model_dump(mode="json", by_alias=True),
                    )
                )
            elif isinstance(entry, PublicCallbackEntry):
                operations.append(
                    JsonPatchOperation(
                        op="replace",
                        path="/state",
                        value={
                            "status": "cancelled",
                            "reason": "Turn interrupted" if cancelled else "Turn ended",
                        },
                    )
                )
            operations.append(
                JsonPatchOperation(
                    op="replace", path="/generationStatus", value="completed"
                )
            )
            updates.append(self._patch(entry.id, operations))
        return updates

    def _entry_fields(
        self, entry_id: str, generation_status: PublicEntryGenerationStatus
    ) -> dict[str, Any]:
        timestamp = now_ms()
        return {
            "id": entry_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "generation_status": generation_status,
        }

    def _add(self, entry: PublicHistoryEntry) -> ProjectedUpdate:
        if entry.id in self._entries:
            raise ValueError(f"Duplicate public history entry: {entry.id}")
        self.history.append(entry)
        self._entries[entry.id] = entry
        return ProjectedUpdate(
            method="history/entryAdded",
            params=HistoryEntryAddedParams(
                event_id=0,
                session_id=self.session_id,
                turn_id=self.turn_id,
                entry=entry,
                emitted_at=now_ms(),
            ),
        )

    def _patch(
        self, entry_id: str, operations: list[JsonPatchOperation]
    ) -> ProjectedUpdate:
        entry = self._entries[entry_id]
        if entry.generation_status is PublicEntryGenerationStatus.COMPLETED:
            raise ValueError(f"Completed public history entry is frozen: {entry_id}")
        timestamp = now_ms()
        operations = [
            *operations,
            JsonPatchOperation(op="replace", path="/updatedAt", value=timestamp),
        ]
        raw = entry.model_dump(mode="json", by_alias=True)
        patched = apply_json_patch(raw, operations)
        updated = validate_history_entry(patched)
        if (
            type(updated) is not type(entry)
            or updated.id != entry.id
            or updated.session_id != entry.session_id
            or updated.turn_id != entry.turn_id
            or updated.created_at != entry.created_at
        ):
            raise ValueError(f"Public history entry identity is frozen: {entry_id}")
        self._entries[entry_id] = updated
        index = next(
            index
            for index, candidate in enumerate(self.history)
            if candidate.id == entry_id
        )
        self.history[index] = updated
        return ProjectedUpdate(
            method="history/entryUpdated",
            params=HistoryEntryUpdatedParams(
                event_id=0,
                session_id=self.session_id,
                turn_id=self.turn_id,
                entry_id=entry_id,
                patch=operations,
                emitted_at=timestamp,
            ),
        )

    def _update_session(self, operations: list[JsonPatchOperation]) -> ProjectedUpdate:
        return ProjectedUpdate(
            method="session/updated",
            params=SessionUpdatedParams(
                event_id=0,
                session_id=self.session_id,
                patch=operations,
                emitted_at=now_ms(),
            ),
        )

    def _project_user_message(
        self, event: UserMessageEvent, source: PublicMessageSource
    ) -> list[ProjectedUpdate]:
        updates = [
            self._add(
                PublicMessageEntry(
                    **self._entry_fields(
                        event.message_id, PublicEntryGenerationStatus.COMPLETED
                    ),
                    role="user",
                    content=project_message_content(
                        event.content, event.images, event.resources
                    ),
                    source=source,
                    user_display_content=event.user_display_content,
                )
            )
        ]
        preview = event.content or next(
            (_resource_preview(resource) for resource in event.resources), ""
        )
        if self._session_preview or not preview:
            return updates
        self._session_preview = preview[:160]
        updates.append(
            self._update_session([
                JsonPatchOperation(
                    op="replace", path="/preview", value=self._session_preview
                )
            ])
        )
        return updates

    def _project_assistant(self, event: AssistantEvent) -> ProjectedUpdate:
        key = event.message_id or "assistant"
        if entry_id := self._assistant_entries.get(key):
            return self._patch(
                entry_id,
                [
                    JsonPatchOperation(
                        op="append", path="/content/0/text", value=event.content
                    )
                ],
            )
        entry_id = event.message_id or str(uuid4())
        self._assistant_entries[key] = entry_id
        return self._add(
            PublicMessageEntry(
                **self._entry_fields(entry_id, PublicEntryGenerationStatus.IN_PROGRESS),
                role="assistant",
                content=[TextContentBlock(text=event.content)],
            )
        )

    def _project_reasoning(self, event: ReasoningEvent) -> ProjectedUpdate:
        key = event.message_id or "reasoning"
        if entry_id := self._reasoning_entries.get(key):
            return self._patch(
                entry_id,
                [JsonPatchOperation(op="append", path="/text", value=event.content)],
            )
        entry_id = event.message_id or str(uuid4())
        self._reasoning_entries[key] = entry_id
        return self._add(
            PublicReasoningEntry(
                **self._entry_fields(entry_id, PublicEntryGenerationStatus.IN_PROGRESS),
                text=event.content,
            )
        )

    def _project_effect_started(self, event: ToolCallEvent) -> ProjectedUpdate:
        presentation = event.presentation
        if presentation is None:
            raise ValueError("Tool calls require a presentation snapshot")
        detail = project_effect_detail(event.tool_name, event.args, presentation)
        return self.start_effect(
            event.tool_call_id, title=event.tool_name, detail=detail
        )

    def _project_effect_output(self, event: ToolStreamEvent) -> ProjectedUpdate:
        return self.append_effect_output(event.tool_call_id, event.message)

    def _project_effect_completed(
        self, event: ToolResultEvent
    ) -> list[ProjectedUpdate]:
        updates: list[ProjectedUpdate] = []
        entry_id = self._effect_entries.get(event.tool_call_id)
        if entry_id is None:
            entry_id = event.tool_call_id
            self._effect_entries[event.tool_call_id] = entry_id
            updates.append(self._add(_result_only_effect(self, event)))
        entry = cast(PublicEffectEntry, self._entries[entry_id])
        output_text = _effect_output_text(entry)
        state = project_effect_state(event, output_text=output_text)
        updates.append(self.complete_effect(entry_id, state))
        return updates

    def _project_compaction_started(self, event: CompactStartEvent) -> ProjectedUpdate:
        self._checkpoint_entries[event.tool_call_id] = event.tool_call_id
        return self._add(
            PublicCheckpointEntry(
                **self._entry_fields(
                    event.tool_call_id, PublicEntryGenerationStatus.IN_PROGRESS
                ),
                kind="compaction",
                message="Compacting context",
                details={
                    "currentContextTokens": event.current_context_tokens,
                    "threshold": event.threshold,
                },
            )
        )

    def _project_compaction_completed(self, event: CompactEndEvent) -> ProjectedUpdate:
        entry_id = self._checkpoint_entries.get(event.tool_call_id, event.tool_call_id)
        if entry_id not in self._entries:
            self._checkpoint_entries[event.tool_call_id] = entry_id
            self._add(
                PublicCheckpointEntry(
                    **self._entry_fields(
                        entry_id, PublicEntryGenerationStatus.IN_PROGRESS
                    ),
                    kind="compaction",
                )
            )
        return self._patch(
            entry_id,
            [
                JsonPatchOperation(
                    op="replace", path="/message", value="Context compacted"
                ),
                JsonPatchOperation(
                    op="replace",
                    path="/details",
                    value={
                        "summaryLength": event.summary_length,
                        "oldSessionId": event.old_session_id,
                        "newSessionId": event.new_session_id,
                    },
                ),
                JsonPatchOperation(
                    op="replace", path="/generationStatus", value="completed"
                ),
            ],
        )

    def _project_hook(self, event: HookEvent) -> ProjectedUpdate:
        match event:
            case HookRunStartEvent():
                message = "Running hooks"
                detail = HookNoticeDetail(
                    kind="hook_run_started",
                    scope=HookScope(event.scope.value),
                    tool_name=event.tool_name,
                    tool_call_id=event.tool_call_id,
                )
            case HookRunEndEvent():
                message = "Hooks completed"
                detail = HookNoticeDetail(
                    kind="hook_run_completed",
                    scope=HookScope(event.scope.value),
                    tool_call_id=event.tool_call_id,
                )
            case HookStartEvent():
                message = f"Running hook {event.hook_name}"
                detail = HookNoticeDetail(
                    kind="hook_started",
                    scope=HookScope(event.scope.value),
                    tool_call_id=event.tool_call_id,
                    hook_name=event.hook_name,
                )
            case HookEndEvent():
                message = event.content or f"Hook {event.hook_name} completed"
                detail = HookNoticeDetail(
                    kind="hook_completed",
                    scope=HookScope(event.scope.value),
                    tool_call_id=event.tool_call_id,
                    hook_name=event.hook_name,
                    status=HookSeverity(event.status.value),
                    content=event.content,
                )
            case _:
                raise TypeError(f"Unsupported hook event: {type(event).__name__}")
        return self.add_notice(str(uuid4()), message=message, detail=detail)

    def _project_notice(self, event: NoticeEvent) -> ProjectedUpdate:
        message, detail = _notice_data(event)
        return self.add_notice(str(uuid4()), message=message, detail=detail)


def _result_only_effect(
    projector: EventProjector, event: ToolResultEvent
) -> PublicEffectEntry:
    display = EffectCallDisplay(
        summary=event.tool_name,
        verb="Running",
        message=event.tool_name,
        settled_verb="Ran",
        settled_message=event.tool_name,
        status_text=f"Running {event.tool_name}",
    )
    return PublicEffectEntry(
        **projector._entry_fields(
            event.tool_call_id, PublicEntryGenerationStatus.IN_PROGRESS
        ),
        title=event.tool_name,
        detail=GenericEffectDetail(
            tool_name=event.tool_name, input=None, display=display
        ),
        state=RunningEffectState(),
    )


def _resource_preview(resource: UserResource) -> str:
    if isinstance(resource, UserResourceLink):
        return resource.title or resource.uri
    return resource.uri


def _effect_output_text(entry: PublicEffectEntry) -> str:
    state = entry.state
    if isinstance(state, RunningEffectState | BlockedEffectState):
        return state.output_text
    return ""


type NoticeEvent = (
    WaitingForInputEvent
    | AgentProfileChangedEvent
    | ContextClearedEvent
    | SessionTitleUpdatedEvent
    | PlanReviewRequestedEvent
    | PlanReviewEndedEvent
)


def _notice_data(event: NoticeEvent) -> tuple[str, NoticeDetail]:
    match event:
        case WaitingForInputEvent():
            message = event.label or "Waiting for input"
            detail = WaitingForInputNoticeDetail(
                task_id=event.task_id,
                label=event.label,
                predefined_answers=event.predefined_answers,
            )
        case AgentProfileChangedEvent():
            message = f"Agent changed to {event.agent_name}"
            detail = AgentChangedNoticeDetail(agent_name=event.agent_name)
        case ContextClearedEvent():
            message = "Context cleared"
            detail = ContextClearedNoticeDetail(
                plan_file_path=(
                    str(event.plan_file_path)
                    if event.plan_file_path is not None
                    else None
                )
            )
        case SessionTitleUpdatedEvent():
            message = "Session title updated"
            detail = SessionTitleUpdatedNoticeDetail(title=event.title)
        case PlanReviewRequestedEvent():
            message = "Plan ready for review"
            detail = PlanReviewStartedNoticeDetail(file_path=str(event.file_path))
        case PlanReviewEndedEvent():
            message = "Plan review ended"
            detail = PlanReviewEndedNoticeDetail()
        case _:
            assert_never(event)
    return message, detail
