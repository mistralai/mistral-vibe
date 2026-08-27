from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, assert_never, cast

from acp.helpers import SessionUpdate, ToolCallContentVariant
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    BlobResourceContents as AcpBlobResourceContents,
    ContentToolCallContent,
    EmbeddedResourceContentBlock as AcpEmbeddedResourceContentBlock,
    FileEditToolCallContent,
    ImageContentBlock as AcpImageContentBlock,
    PlanEntry,
    ResourceContentBlock as AcpResourceContentBlock,
    SessionInfoUpdate,
    TextContentBlock as AcpTextContentBlock,
    TextResourceContents as AcpTextResourceContents,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolCallStatus,
    ToolKind,
    UserMessageChunk,
)
from pydantic import BaseModel, JsonValue

from vibe.acp.user_display_content import USER_DISPLAY_CONTENT_META_KEY
from vibe.app_server.events import (
    AppServerEvent,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    SessionUpdated,
)
from vibe.app_server.models import (
    BlockedEffectState,
    CancelledEffectState,
    CompletedEffectState,
    FailedEffectState,
    FileEditEffectDetail,
    FileEditEffectInput,
    FileEditEffectOutput,
    FileImageSource,
    FileReadEffectDetail,
    FileReadEffectInput,
    FileReadEffectOutput,
    FileSearchEffectDetail,
    FileSearchEffectInput,
    FileSearchEffectOutput,
    FileWriteEffectDetail,
    FileWriteEffectInput,
    FileWriteEffectOutput,
    ImageContentBlock,
    InlineImageSource,
    PendingEffectState,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
    PublicReasoningEntry,
    PublicSession,
    PublicSessionState,
    ResourceContentBlock,
    RunningEffectState,
    SessionTitleUpdatedNoticeDetail,
    ShellEffectDetail,
    ShellEffectOutput,
    SkillEffectDetail,
    SkillEffectInput,
    SkillEffectOutput,
    SkippedEffectState,
    SubagentEffectDetail,
    SubagentEffectOutput,
    TextContentBlock,
    TodoEffectDetail,
    TodoEffectInput,
    TodoEffectItem,
    TodoEffectOutput,
    WebFetchEffectDetail,
    WebFetchEffectInput,
    WebFetchEffectOutput,
    WebSearchEffectDetail,
    WebSearchEffectInput,
    WebSearchEffectOutput,
    effect_input_json,
)
from vibe.user_content import UserBlobResource, UserResourceLink, UserTextResource
from vibe.utils.tool_presentation import ToolEffectKind

type _AcpMessageChunk = UserMessageChunk | AgentMessageChunk


_TOOL_KINDS: dict[ToolEffectKind, ToolKind] = {
    ToolEffectKind.TOOL: "other",
    ToolEffectKind.SHELL: "execute",
    ToolEffectKind.FILE_EDIT: "edit",
    ToolEffectKind.FILE_SEARCH: "search",
    ToolEffectKind.FILE_READ: "read",
    ToolEffectKind.TODO: "other",
    ToolEffectKind.FILE_WRITE: "edit",
    ToolEffectKind.USER_QUESTION: "other",
    ToolEffectKind.WEB_SEARCH: "search",
    ToolEffectKind.WEB_FETCH: "fetch",
    ToolEffectKind.SKILL: "read",
    ToolEffectKind.SUBAGENT: "think",
    ToolEffectKind.WORKTREE: "other",
}


def replay_session_updates(state: PublicSessionState) -> list[SessionUpdate]:
    updates: list[SessionUpdate] = []
    if display_title := _display_title(state.session):
        updates.append(_session_info_update(display_title, state.session.updated_at))
    for entry in state.history or []:
        if isinstance(entry, PublicNoticeEntry) and isinstance(
            entry.detail, SessionTitleUpdatedNoticeDetail
        ):
            continue
        updates.extend(replay_history_entry(entry))
    return updates


def replay_history_entry(entry: PublicHistoryEntry) -> list[SessionUpdate]:
    match entry:
        case PublicMessageEntry():
            return _message_updates(entry)
        case PublicReasoningEntry():
            return _reasoning_updates(entry)
        case PublicEffectEntry():
            return _effect_replay_updates(entry)
        case PublicCheckpointEntry():
            return [_checkpoint_start(entry)]
        case PublicNoticeEntry(detail=SessionTitleUpdatedNoticeDetail(title=title)):
            return [_session_info_update(title, entry.updated_at)]
        case _:
            return []


def session_updates_for_event(event: AppServerEvent) -> list[SessionUpdate]:
    match event:
        case HistoryEntryAdded(entry=entry):
            return _added_entry_updates(entry)
        case HistoryEntryUpdated(previous=previous, entry=entry):
            return _updated_entry_updates(previous, entry)
        case SessionUpdated(previous=previous, session=session):
            # The client renders the title verbatim, so fold the first-message
            # preview into the effective title: an untitled session still gets a
            # readable label, and the update fires when the preview first lands.
            display_title = _display_title(session)
            if _display_title(previous) == display_title:
                return []
            return [_session_info_update(display_title, session.updated_at)]
        case _:
            return []


def _added_entry_updates(entry: PublicHistoryEntry) -> list[SessionUpdate]:
    if isinstance(entry, PublicNoticeEntry) and isinstance(
        entry.detail, SessionTitleUpdatedNoticeDetail
    ):
        return []
    return replay_history_entry(entry)


def _updated_entry_updates(
    previous: PublicHistoryEntry, entry: PublicHistoryEntry
) -> list[SessionUpdate]:
    if isinstance(previous, PublicMessageEntry) and isinstance(
        entry, PublicMessageEntry
    ):
        delta = _text_delta(previous.text, entry.text)
        return _message_updates(entry, text=delta) if delta else []
    if isinstance(previous, PublicReasoningEntry) and isinstance(
        entry, PublicReasoningEntry
    ):
        delta = _text_delta(previous.text, entry.text)
        return _reasoning_updates(entry, text=delta) if delta else []
    if isinstance(previous, PublicEffectEntry) and isinstance(entry, PublicEffectEntry):
        return _effect_progress_updates(previous, entry)
    if isinstance(previous, PublicCheckpointEntry) and isinstance(
        entry, PublicCheckpointEntry
    ):
        return [_checkpoint_progress(entry)]
    return []


def _message_updates(
    entry: PublicMessageEntry, *, text: str | None = None
) -> list[SessionUpdate]:
    if text is not None:
        block = AcpTextContentBlock(type="text", text=text)
        return [_message_chunk(entry, block)]

    updates: list[SessionUpdate] = []
    for block in entry.content:
        match block:
            case TextContentBlock(text=block_text) if block_text:
                updates.append(
                    _message_chunk(
                        entry, AcpTextContentBlock(type="text", text=block_text)
                    )
                )
            case ImageContentBlock(attachment=attachment):
                source = attachment.source
                if isinstance(source, InlineImageSource):
                    updates.append(
                        _message_chunk(
                            entry,
                            AcpImageContentBlock(
                                type="image",
                                data=source.data,
                                mime_type=attachment.mime_type,
                            ),
                        )
                    )
                elif isinstance(source, FileImageSource):
                    updates.append(
                        _message_chunk(
                            entry,
                            AcpResourceContentBlock(
                                type="resource_link",
                                name=attachment.alias,
                                uri=source.path,
                                mime_type=attachment.mime_type,
                            ),
                        )
                    )
            case ResourceContentBlock(resource=resource):
                match resource:
                    case UserTextResource():
                        content = AcpEmbeddedResourceContentBlock(
                            type="resource",
                            resource=AcpTextResourceContents(
                                uri=resource.uri,
                                mime_type=resource.media_type,
                                text=resource.text,
                            ),
                        )
                    case UserBlobResource():
                        content = AcpEmbeddedResourceContentBlock(
                            type="resource",
                            resource=AcpBlobResourceContents(
                                uri=resource.uri,
                                mime_type=resource.media_type,
                                blob=resource.blob,
                            ),
                        )
                    case UserResourceLink():
                        content = AcpResourceContentBlock(
                            type="resource_link",
                            name=resource.name or resource.title or resource.uri,
                            uri=resource.uri,
                            title=resource.title,
                            description=resource.description,
                            mime_type=resource.media_type,
                            size=resource.size,
                        )
                    case _:
                        assert_never(resource)
                updates.append(_message_chunk(entry, content))
    return updates


def _message_chunk(
    entry: PublicMessageEntry,
    content: AcpTextContentBlock
    | AcpImageContentBlock
    | AcpResourceContentBlock
    | AcpEmbeddedResourceContentBlock,
) -> _AcpMessageChunk:
    if entry.role == "user":
        field_meta = (
            {
                USER_DISPLAY_CONTENT_META_KEY: entry.user_display_content.model_dump(
                    mode="json", by_alias=True
                )
            }
            if entry.user_display_content is not None
            else None
        )
        return UserMessageChunk(
            session_update="user_message_chunk",
            content=content,
            message_id=entry.id,
            field_meta=field_meta,
        )
    return AgentMessageChunk(
        session_update="agent_message_chunk", content=content, message_id=entry.id
    )


def _reasoning_updates(
    entry: PublicReasoningEntry, *, text: str | None = None
) -> list[SessionUpdate]:
    value = entry.text if text is None else text
    if not value:
        return []
    return [
        AgentThoughtChunk(
            session_update="agent_thought_chunk",
            content=AcpTextContentBlock(type="text", text=value),
            message_id=entry.id,
        )
    ]


def _effect_replay_updates(entry: PublicEffectEntry) -> list[SessionUpdate]:
    updates: list[SessionUpdate] = [_effect_start(entry)]
    if plan := _plan_update(entry):
        updates.append(plan)
    return updates


def _effect_start(entry: PublicEffectEntry) -> ToolCallStart:
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=entry.id,
        title=entry.detail.display.summary or entry.title,
        kind=_TOOL_KINDS[entry.detail.kind],
        status=_effect_status(entry),
        content=_replayed_effect_content(entry),
        locations=_effect_locations(entry),
        raw_input=effect_input_json(entry.detail),
        raw_output=_raw_output(entry),
        field_meta=_effect_meta(entry),
    )


def _effect_progress_updates(
    previous: PublicEffectEntry, entry: PublicEffectEntry
) -> list[SessionUpdate]:
    previous_output = _output_text(previous)
    output_delta = _text_delta(previous_output, _output_text(entry))
    became_terminal = (
        previous.generation_status is PublicEntryGenerationStatus.IN_PROGRESS
        and entry.generation_status is PublicEntryGenerationStatus.COMPLETED
    )
    state_changed = previous.state.status != entry.state.status
    detail_changed = previous.detail != entry.detail

    content: list[ToolCallContentVariant] = []
    if output_delta:
        content.append(_text_tool_content(output_delta))
    if became_terminal or state_changed:
        content.extend(_result_effect_content(entry, output_delta))
    if detail_changed:
        content.extend(_call_effect_content(entry) or [])

    progress = ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=entry.id,
        title=(entry.detail.display.summary or entry.title) if detail_changed else None,
        kind=_TOOL_KINDS[entry.detail.kind],
        status=_effect_status(entry),
        content=content or None,
        locations=(
            _effect_locations(entry)
            if detail_changed or became_terminal or state_changed
            else None
        ),
        raw_input=effect_input_json(entry.detail) if detail_changed else None,
        raw_output=(_raw_output(entry) if became_terminal or state_changed else None),
        field_meta=_effect_meta(entry),
    )
    updates: list[SessionUpdate] = [progress]
    if plan := _plan_update(entry):
        if became_terminal or state_changed:
            updates.append(plan)
    return updates


def _replayed_effect_content(
    entry: PublicEffectEntry,
) -> list[ToolCallContentVariant] | None:
    if entry.generation_status is PublicEntryGenerationStatus.IN_PROGRESS:
        content = _call_effect_content(entry) or []
        if output := _output_text(entry):
            content.append(_text_tool_content(output))
        return content or None

    content = _result_effect_content(entry, "")
    if output := _output_text(entry):
        content.insert(0, _text_tool_content(output))
    return content or None


def _result_effect_content(
    entry: PublicEffectEntry, output_delta: str
) -> list[ToolCallContentVariant]:
    content: list[ToolCallContentVariant] = []
    match entry.detail:
        case ShellEffectDetail() if isinstance(entry.state, CompletedEffectState):
            # The transcript arrives as output; a trailer would repeat its tail.
            return []
        case FileEditEffectDetail():
            if value := _effect_output(entry, FileEditEffectOutput):
                content.append(
                    FileEditToolCallContent(
                        type="diff",
                        path=value.file,
                        old_text=value.old_string,
                        new_text=value.new_string,
                    )
                )
        case FileWriteEffectDetail():
            if value := _effect_output(entry, FileWriteEffectOutput):
                content.append(
                    FileEditToolCallContent(
                        type="diff",
                        path=value.file_path,
                        old_text=None,
                        new_text=value.content,
                    )
                )
    display = _result_display_text(entry)
    if display and display != output_delta:
        content.append(_text_tool_content(display))
    return content


def _call_effect_content(
    entry: PublicEffectEntry,
) -> list[ToolCallContentVariant] | None:
    match entry.detail:
        case FileEditEffectDetail(input=FileEditEffectInput() as value):
            return [
                FileEditToolCallContent(
                    type="diff",
                    path=value.file_path,
                    old_text=value.old_string,
                    new_text=value.new_string,
                )
            ]
        case FileWriteEffectDetail(input=FileWriteEffectInput() as value):
            return [
                FileEditToolCallContent(
                    type="diff",
                    path=value.file_path,
                    old_text=None,
                    new_text=value.content,
                )
            ]
    text = entry.detail.display.content
    if text:
        return [_text_tool_content(text)]
    return None


def _effect_locations(entry: PublicEffectEntry) -> list[ToolCallLocation] | None:
    match entry.detail:
        case FileEditEffectDetail(input=value) if value is not None:
            output = _effect_output(entry, FileEditEffectOutput)
            path = output.file if output is not None else value.file_path
            locations = [ToolCallLocation(path=_resolved_path(path))]
        case FileWriteEffectDetail(input=value) if value is not None:
            output = _effect_output(entry, FileWriteEffectOutput)
            path = output.file_path if output is not None else value.file_path
            locations = [ToolCallLocation(path=_resolved_path(path))]
        case FileSearchEffectDetail():
            locations = _file_search_locations(entry)
        case FileReadEffectDetail(input=FileReadEffectInput() as value):
            output = _effect_output(entry, FileReadEffectOutput)
            if output is not None:
                locations = [_read_result_location(output)]
            else:
                locations = [_read_call_location(value)]
        case WebSearchEffectDetail():
            locations = _web_search_locations(entry)
        case WebFetchEffectDetail(input=WebFetchEffectInput() as value):
            locations = _web_fetch_locations(entry, value)
        case SkillEffectDetail():
            locations = _skill_locations(entry)
        case _:
            locations = None
    return locations


def _file_search_locations(entry: PublicEffectEntry) -> list[ToolCallLocation] | None:
    output = _effect_output(entry, FileSearchEffectOutput)
    if output is None:
        return None
    locations = [
        ToolCallLocation(path=match.path, line=match.line)
        for match in output.parsed_matches
    ]
    return locations or None


def _web_search_locations(entry: PublicEffectEntry) -> list[ToolCallLocation] | None:
    output = _effect_output(entry, WebSearchEffectOutput)
    if output is None:
        return None
    locations = [
        ToolCallLocation(
            path=source.url, field_meta={"type": "url", "title": source.title}
        )
        for source in output.sources
    ]
    return locations or None


def _web_fetch_locations(
    entry: PublicEffectEntry, value: WebFetchEffectInput
) -> list[ToolCallLocation]:
    output = _effect_output(entry, WebFetchEffectOutput)
    if output is None:
        return [
            ToolCallLocation(
                path=_normalized_web_url(value.url), field_meta={"type": "url"}
            )
        ]
    return [
        ToolCallLocation(
            path=output.url,
            field_meta={
                "type": "url",
                "char_count": len(output.content),
                "truncated": output.was_truncated,
            },
        )
    ]


def _skill_locations(entry: PublicEffectEntry) -> list[ToolCallLocation] | None:
    output = _effect_output(entry, SkillEffectOutput)
    if output is None or output.skill_dir is None:
        return None
    return [ToolCallLocation(path=_resolved_path(output.skill_dir))]


def _read_call_location(value: FileReadEffectInput) -> ToolCallLocation:
    path = _resolved_path(value.file_path)
    if value.limit != FileReadEffectInput.DEFAULT_LIMIT:
        return ToolCallLocation(
            path=path,
            field_meta={
                "type": "file_range",
                "offset": value.offset,
                "limit": value.limit,
            },
        )
    return ToolCallLocation(path=path, line=value.offset, field_meta={"type": "file"})


def _read_result_location(value: FileReadEffectOutput) -> ToolCallLocation:
    path = _resolved_path(value.file_path)
    if (
        value.requested_limit != FileReadEffectInput.DEFAULT_LIMIT
        or value.was_truncated
    ):
        return ToolCallLocation(
            path=path,
            field_meta={
                "type": "file_range",
                "offset": value.start_line,
                "limit": value.num_lines,
            },
        )
    return ToolCallLocation(
        path=path, line=value.requested_offset, field_meta={"type": "file"}
    )


def _resolved_path(value: str) -> str:
    return str(Path(value).resolve())


def _normalized_web_url(value: str) -> str:
    raw = value.lstrip("/") if value.startswith("//") else value
    if raw.startswith(("http://", "https://")):
        return raw
    return f"https://{raw}"


def _effect_output[ModelT: BaseModel](
    entry: PublicEffectEntry, model_type: type[ModelT]
) -> ModelT | None:
    if not isinstance(entry.state, CompletedEffectState):
        return None
    if entry.state.output is None:
        return None
    return model_type.model_validate(entry.state.output)


def _todo_items(entry: PublicEffectEntry) -> list[TodoEffectItem]:
    if not isinstance(entry.detail, TodoEffectDetail):
        return []
    if output := _effect_output(entry, TodoEffectOutput):
        return output.todos
    if isinstance(entry.detail.input, TodoEffectInput):
        return entry.detail.input.todos or []
    return []


def _effect_status(entry: PublicEffectEntry) -> ToolCallStatus:
    state = entry.state
    if isinstance(state, PendingEffectState):
        return "pending"
    if isinstance(state, RunningEffectState | BlockedEffectState):
        return "in_progress"
    if isinstance(state, CompletedEffectState):
        if isinstance(entry.detail, SubagentEffectDetail):
            output = _effect_output(entry, SubagentEffectOutput)
            if output is not None and not output.completed:
                return "failed"
        return "completed"
    return "failed"


def _raw_output(entry: PublicEffectEntry) -> JsonValue:
    state = entry.state
    if isinstance(state, CompletedEffectState):
        return state.output
    if isinstance(state, FailedEffectState):
        return state.error.model_dump(mode="json", by_alias=True)
    if isinstance(state, CancelledEffectState | SkippedEffectState):
        return state.reason
    return None


def _output_text(entry: PublicEffectEntry) -> str:
    state = entry.state
    if isinstance(
        state,
        RunningEffectState
        | BlockedEffectState
        | CompletedEffectState
        | FailedEffectState
        | CancelledEffectState,
    ):
        return state.output_text
    return ""


def _result_display_text(entry: PublicEffectEntry) -> str:
    state = entry.state
    if isinstance(state, CompletedEffectState | FailedEffectState):
        return state.display.text
    if isinstance(state, CancelledEffectState) and state.display is not None:
        return state.display.text
    if isinstance(state, SkippedEffectState):
        return state.display.text
    return ""


def _effect_meta(entry: PublicEffectEntry) -> dict[str, JsonValue]:
    meta: dict[str, JsonValue] = {
        "tool_name": entry.detail.tool_name,
        "effect_kind": entry.detail.kind.value,
    }
    match entry.detail:
        case ShellEffectDetail():
            output = _effect_output(entry, ShellEffectOutput)
            if output is not None and output.truncated:
                meta["output_truncated"] = True
        case FileSearchEffectDetail(input=FileSearchEffectInput() as value):
            meta.update(query=value.pattern, search_path=_resolved_path(value.path))
        case WebSearchEffectDetail(input=WebSearchEffectInput() as value):
            meta["query"] = value.query
        case SkillEffectDetail(input=SkillEffectInput() as value):
            output = _effect_output(entry, SkillEffectOutput)
            meta["skill_name"] = output.name if output is not None else value.name
        case SubagentEffectDetail(input=value):
            if value is not None:
                meta.update(agent=value.agent, task=value.task)
            if entry.detail.child_session_id is not None:
                meta["child_session_id"] = entry.detail.child_session_id
            if output := _effect_output(entry, SubagentEffectOutput):
                meta.update(turn_count=output.turns_used, response=output.response)
    return meta


def _plan_update(entry: PublicEffectEntry) -> AgentPlanUpdate | None:
    if not isinstance(entry.detail, TodoEffectDetail):
        return None

    plan_entries: list[PlanEntry] = []
    for item in _todo_items(entry):
        status = item.status.value
        if status == "cancelled":
            continue
        if status not in {"pending", "in_progress", "completed"}:
            status = "pending"
        plan_entries.append(
            PlanEntry(
                content=item.content,
                status=cast(Literal["pending", "in_progress", "completed"], status),
                priority=cast(Literal["low", "medium", "high"], item.priority.value),
            )
        )
    return AgentPlanUpdate(
        session_update="plan",
        entries=plan_entries,
        field_meta={"effect_entry_id": entry.id},
    )


def _checkpoint_start(entry: PublicCheckpointEntry) -> ToolCallStart:
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=entry.id,
        title=entry.message or _checkpoint_title(entry.kind),
        kind="think",
        status=(
            "completed"
            if entry.generation_status is PublicEntryGenerationStatus.COMPLETED
            else "in_progress"
        ),
        content=(
            [_text_tool_content(entry.message)] if entry.message is not None else None
        ),
        raw_input=entry.details,
        field_meta={"checkpoint_kind": entry.kind},
    )


def _checkpoint_progress(entry: PublicCheckpointEntry) -> ToolCallProgress:
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=entry.id,
        title=entry.message or _checkpoint_title(entry.kind),
        kind="think",
        status=(
            "completed"
            if entry.generation_status is PublicEntryGenerationStatus.COMPLETED
            else "in_progress"
        ),
        content=(
            [_text_tool_content(entry.message)] if entry.message is not None else None
        ),
        raw_output=entry.details,
        field_meta={"checkpoint_kind": entry.kind},
    )


def _checkpoint_title(kind: str) -> str:
    return kind.replace("_", " ").capitalize()


def _text_tool_content(text: str) -> ContentToolCallContent:
    return ContentToolCallContent(
        type="content", content=AcpTextContentBlock(type="text", text=text)
    )


def _text_delta(previous: str, current: str) -> str:
    if current.startswith(previous):
        return current[len(previous) :]
    return current


def _display_title(session: PublicSession) -> str | None:
    # The client renders the title verbatim with no preview fallback, so an
    # untitled session should still show its first-message preview.
    return session.title or session.preview or None


def _session_info_update(title: str | None, updated_at: int) -> SessionInfoUpdate:
    return SessionInfoUpdate(
        session_update="session_info_update",
        title=title,
        updated_at=datetime.fromtimestamp(updated_at / 1000, tz=UTC).isoformat(),
    )
