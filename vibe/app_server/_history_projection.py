"""Projection of stored session transcripts into public history entries.

Extracted from ``_projection`` because the legacy session importer needs it
without importing the legacy execution engine: this module turns plain
``LLMMessage`` records into public entries and depends on no runtime
object. ``_projection`` imports what it still uses (``project_message_history``
and ``history_message_id``); the callers of ``project_message_content`` import
it from here directly.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import TypedDict

from pydantic import JsonValue

from vibe.app_server._shell import restored_shell_effect_state, shell_effect_detail
from vibe.app_server._time import now_ms
from vibe.app_server._tool_projection import (
    project_effect_detail,
    project_effect_output_value,
)
from vibe.app_server._worktree_effects import WorktreeEffect
from vibe.app_server.models import (
    CancelledEffectState,
    CompletedEffectState,
    ContentBlock,
    EffectCallDisplay,
    EffectResultDisplay,
    EffectState,
    FailedEffectState,
    GenericEffectDetail,
    ImageAttachment,
    ImageContentBlock,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicError,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicReasoningEntry,
    ResourceContentBlock,
    SubagentEffectDetail,
    TextContentBlock,
)
from vibe.core.types import (
    ImageAttachment as CoreImageAttachment,
    LLMMessage,
    Role,
    SessionMetadata,
    WorktreeContext,
)
from vibe.core.utils import CANCELLATION_TAG, TOOL_ERROR_TAG, TaggedText
from vibe.user_content import UserResource
from vibe.utils.tool_presentation import ToolCallPresentation

__all__ = ["history_message_id", "project_message_content", "project_message_history"]


def project_message_content(
    text: str | None,
    images: Sequence[CoreImageAttachment] | None,
    resources: Sequence[UserResource] | None = None,
) -> list[ContentBlock]:
    content: list[ContentBlock] = []
    if text:
        content.append(TextContentBlock(text=text))
    content.extend(
        ImageContentBlock(
            attachment=ImageAttachment.model_validate(image.model_dump(mode="json"))
        )
        for image in images or []
    )
    content.extend(
        ResourceContentBlock(resource=resource) for resource in resources or []
    )
    return content


def project_message_history(
    session_id: str, messages: Sequence[LLMMessage], metadata: SessionMetadata | None
) -> list[PublicHistoryEntry]:
    timestamp = now_ms()
    entries: list[PublicHistoryEntry] = []
    effect_indices: dict[str, int] = {}
    child_sessions = (
        {link.tool_call_id: link.session_id for link in metadata.child_sessions}
        if metadata is not None
        else {}
    )
    # First: the worktree is created before the session has a single message.
    if metadata is not None and metadata.created_worktree is not None:
        entries.append(_history_worktree(session_id, metadata.created_worktree))
    for index, message in enumerate(messages):
        _project_stored_message(
            session_id,
            message,
            index,
            timestamp + index,
            entries,
            effect_indices,
            child_sessions,
        )
    return entries


def _project_stored_message(
    session_id: str,
    message: LLMMessage,
    index: int,
    created_at: int,
    entries: list[PublicHistoryEntry],
    effect_indices: dict[str, int],
    child_sessions: dict[str, str],
) -> None:
    if message.role is Role.system:
        return
    if message.context_boundary == "compaction":
        _append_compaction_history(session_id, message, index, created_at, entries)
        return
    if message.injected:
        _append_injected_history(session_id, message, entries)
        return
    match message.role:
        case Role.user:
            entries.append(
                _history_user_message(session_id, message, index, created_at)
            )
        case Role.assistant:
            _append_assistant_history(
                session_id,
                message,
                index,
                created_at,
                entries,
                effect_indices,
                child_sessions,
            )
        case Role.tool:
            _apply_tool_history(
                session_id,
                message,
                index,
                created_at,
                entries,
                effect_indices,
                child_sessions,
            )


def _history_worktree(session_id: str, worktree: WorktreeContext) -> PublicEffectEntry:
    restored = WorktreeEffect.restored(worktree)
    return PublicEffectEntry(
        **_history_fields(session_id, worktree.entry_id, worktree.created_at),
        title="worktree",
        detail=restored.detail,
        state=restored.state,
    )


def _append_injected_history(
    session_id: str, message: LLMMessage, entries: list[PublicHistoryEntry]
) -> None:
    shell = message.manual_shell
    if shell is None:
        return
    entries.append(
        PublicEffectEntry(
            **_history_fields(session_id, shell.operation_id, shell.created_at),
            title="shell",
            detail=shell_effect_detail(shell.command),
            state=restored_shell_effect_state(shell),
        )
    )


def _append_compaction_history(
    session_id: str,
    message: LLMMessage,
    index: int,
    created_at: int,
    entries: list[PublicHistoryEntry],
) -> None:
    message_id = message.message_id or f"history:{index}:compaction"
    entries.append(
        PublicCheckpointEntry(
            **_history_fields(
                session_id, f"checkpoint:compaction:{message_id}", created_at
            ),
            kind="compaction",
            message="Context compacted",
            details={},
        )
    )


def _history_user_message(
    session_id: str, message: LLMMessage, index: int, created_at: int
) -> PublicMessageEntry:
    return PublicMessageEntry(
        **_history_fields(session_id, history_message_id(message, index), created_at),
        role="user",
        content=project_message_content(
            message.input_text if message.input_text is not None else message.content,
            message.images,
            message.resources,
        ),
        source="harness",
        user_display_content=message.user_display_content,
    )


def _append_assistant_history(
    session_id: str,
    message: LLMMessage,
    index: int,
    created_at: int,
    entries: list[PublicHistoryEntry],
    effect_indices: dict[str, int],
    child_sessions: dict[str, str],
) -> None:
    if message.reasoning_content:
        entries.append(
            PublicReasoningEntry(
                **_history_fields(
                    session_id,
                    message.reasoning_message_id or f"history:{index}:reasoning",
                    created_at,
                ),
                text=message.reasoning_content,
            )
        )
    if message.content:
        entries.append(
            PublicMessageEntry(
                **_history_fields(
                    session_id,
                    message.message_id or f"history:{index}:assistant",
                    created_at,
                ),
                role="assistant",
                content=[TextContentBlock(text=message.content)],
                source="harness",
            )
        )
    for tool_call in message.tool_calls or []:
        tool_call_id = tool_call.id or f"history:{index}:effect"
        effect_indices[tool_call_id] = len(entries)
        entries.append(
            _history_effect(
                session_id,
                tool_call_id,
                tool_call.function.name or "unknown",
                tool_call.function.arguments,
                created_at,
                presentation=tool_call.presentation,
                child_session_id=child_sessions.get(tool_call_id),
            )
        )


def _apply_tool_history(
    session_id: str,
    message: LLMMessage,
    index: int,
    created_at: int,
    entries: list[PublicHistoryEntry],
    effect_indices: dict[str, int],
    child_sessions: dict[str, str],
) -> None:
    tool_call_id = message.tool_call_id or ""
    effect_index = effect_indices.get(tool_call_id)
    if effect_index is None:
        entries.append(
            _history_effect(
                session_id,
                tool_call_id or f"history:{index}:effect",
                message.name or "tool",
                None,
                created_at,
                result_message=message,
                child_session_id=child_sessions.get(tool_call_id),
            )
        )
        return
    effect = entries[effect_index]
    if not isinstance(effect, PublicEffectEntry):
        return
    entries[effect_index] = effect.model_copy(
        update={"state": _persisted_effect_state(effect, message)}
    )


def history_message_id(message: LLMMessage, index: int) -> str:
    return message.message_id or f"history:{index}:{message.role.value}"


class _HistoryFields(TypedDict):
    id: str
    session_id: str
    turn_id: str | None
    created_at: int
    updated_at: int
    generation_status: PublicEntryGenerationStatus
    related_entry_id: str | None


def _history_fields(session_id: str, entry_id: str, created_at: int) -> _HistoryFields:
    return {
        "id": entry_id,
        "session_id": session_id,
        "turn_id": None,
        "created_at": created_at,
        "updated_at": created_at,
        "generation_status": PublicEntryGenerationStatus.COMPLETED,
        "related_entry_id": None,
    }


def _history_effect(
    session_id: str,
    entry_id: str,
    tool_name: str,
    raw_arguments: str | None,
    created_at: int,
    *,
    result_message: LLMMessage | None = None,
    presentation: ToolCallPresentation | None = None,
    child_session_id: str | None = None,
) -> PublicEffectEntry:
    arguments = _parse_arguments(raw_arguments)
    if presentation is not None:
        detail = project_effect_detail(tool_name, arguments, presentation)
    else:
        summary = _generic_call_summary(tool_name, arguments)
        display = EffectCallDisplay(
            summary=summary,
            verb="Running",
            message=summary,
            settled_verb="Ran",
            settled_message=summary,
            status_text=f"Running {tool_name}",
        )
        detail = GenericEffectDetail(
            tool_name=tool_name, input=arguments, display=display
        )
    if isinstance(detail, SubagentEffectDetail):
        detail = detail.model_copy(update={"child_session_id": child_session_id})
    entry = PublicEffectEntry(
        **_history_fields(session_id, entry_id, created_at),
        title=tool_name,
        detail=detail,
        state=CompletedEffectState(
            output=None,
            output_text="",
            display=EffectResultDisplay(success=True, message=f"{tool_name} completed"),
        ),
    )
    return entry.model_copy(
        update={"state": _persisted_effect_state(entry, result_message)}
    )


def _persisted_effect_state(
    effect: PublicEffectEntry, message: LLMMessage | None
) -> EffectState:
    if message is None:
        reason = "Tool did not complete before the session ended"
        return CancelledEffectState(
            reason=reason,
            output_text="",
            display=EffectResultDisplay(success=False, message=reason),
        )
    text = TaggedText.from_string(message.content or "")
    display_message = text.message or f"{effect.detail.tool_name} completed"
    if text.tag == CANCELLATION_TAG:
        return CancelledEffectState(
            reason=display_message,
            output_text=text.message,
            display=EffectResultDisplay(success=False, message=display_message),
        )
    if text.tag == TOOL_ERROR_TAG:
        return FailedEffectState(
            error=PublicError(message=display_message),
            output_text=text.message,
            display=EffectResultDisplay(success=False, message=display_message),
        )
    persisted = message.tool_result
    if persisted is not None:
        presentation = persisted.presentation
        display = (
            presentation.display
            if presentation is not None
            else EffectResultDisplay(success=True, message=display_message)
        )
        duration_ms = (persisted.duration or 0.0) * 1000
        if persisted.cancelled:
            return CancelledEffectState(
                reason=display.message,
                output_text=text.message,
                duration_ms=duration_ms,
                display=display,
            )
        kind = presentation.kind if presentation is not None else effect.detail.kind
        output = (
            presentation.projected_output
            if presentation is not None and presentation.projected_output is not None
            else persisted.output
        )
        return CompletedEffectState(
            output=project_effect_output_value(kind, output),
            output_text=text.message,
            duration_ms=duration_ms,
            display=display,
        )
    return CompletedEffectState(
        output=None,
        output_text=text.message,
        display=EffectResultDisplay(success=True, message=display_message),
    )


def _parse_arguments(value: str | None) -> JsonValue:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return parsed


def _generic_call_summary(tool_name: str, arguments: JsonValue) -> str:
    if not isinstance(arguments, dict):
        return tool_name
    rendered = ", ".join(
        f"{key}={value!r}" for key, value in list(arguments.items())[:3]
    )
    return f"{tool_name}({rendered})"
