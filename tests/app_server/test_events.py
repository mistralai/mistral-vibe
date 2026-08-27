from __future__ import annotations

from pydantic import JsonValue, ValidationError
import pytest

from vibe.app_server._patch import apply_json_patch
from vibe.app_server._projector import EventProjector
from vibe.app_server.events import (
    ClientProjection,
    EventSequenceError,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    MCPAuthorizationRequiredEvent,
    ServerError,
    ServerWarning,
    SessionCompacted,
    SessionSnapshot,
    StatsUpdated,
    UnknownNotificationError,
    parse_server_event,
    reconcile_snapshot,
)
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    CompletedEffectState,
    IdleSessionStatus,
    PublicCallbackEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicError,
    PublicMessageEntry,
    PublicReasoningEntry,
    PublicRetryCategory,
    PublicRetryState,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
    ResourceContentBlock,
    TextContentBlock,
)
from vibe.app_server.protocol import (
    HistoryEntryAddedParams,
    JsonPatchOperation,
    MCPAuthRequiredParams,
    Notification,
)
from vibe.core.tools.builtins.read_file import ReadFile, ReadFileArgs, ReadFileResult
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import (
    AssistantEvent,
    BaseEvent,
    ImageAttachment,
    InlineImageSource,
    ReasoningEvent,
    SessionTitleUpdatedEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserMessageEvent,
)
from vibe.user_content import UserDisplayContent, UserResourceLink


def _projection() -> ClientProjection:
    return ClientProjection(
        PublicSessionState(
            event_id=0,
            session=PublicSession(
                id="session-1", status=IdleSessionStatus(), created_at=1, updated_at=1
            ),
            history=[],
            turns=[],
            active_callbacks=[],
        )
    )


def _notification(sequence: int, update) -> Notification:
    params = update.params.model_copy(update={"event_id": sequence})
    return Notification(
        method=update.method, params=params.model_dump(mode="json", by_alias=True)
    )


@pytest.mark.parametrize(
    ("method", "params", "event_type"),
    [
        (
            "warning",
            {"warning": {"message": "non-fatal", "code": "warning"}},
            ServerWarning,
        ),
        (
            "error",
            {"error": {"message": "background failed", "code": "runtime"}},
            ServerError,
        ),
    ],
)
def test_server_warning_and_error_notifications_are_typed(
    method: str,
    params: dict[str, JsonValue],
    event_type: type[ServerWarning | ServerError],
) -> None:
    event = parse_server_event(Notification(method=method, params=params))

    assert event is not None
    assert isinstance(event, event_type)
    if isinstance(event, ServerWarning):
        error = event.params.warning
    else:
        error = event.params.error
    assert isinstance(error, PublicError)


def test_mcp_authorization_required_notification_is_typed() -> None:
    params = MCPAuthRequiredParams(
        session_id="session-1",
        name="oauth",
        descriptor_revision="revision-2",
        observed_connection_revision="connection-1",
    )

    event = parse_server_event(
        Notification(
            method="mcp_catalog/authRequired",
            params=params.model_dump(mode="json", by_alias=True),
        )
    )

    assert event == MCPAuthorizationRequiredEvent(params)


class PrivateRuntimeEvent(BaseEvent):
    secret: str


def test_snapshot_reconciliation_replays_missing_stream_updates() -> None:
    entry = PublicMessageEntry(
        id="assistant-1",
        session_id="session-1",
        turn_id="turn-1",
        role="assistant",
        content=[TextContentBlock(text="hell")],
        generation_status=PublicEntryGenerationStatus.IN_PROGRESS,
        created_at=1,
        updated_at=1,
    )
    previous = _projection().state.model_copy(update={"history": [entry]}, deep=True)
    completed = entry.model_copy(
        update={
            "content": [TextContentBlock(text="hello")],
            "generation_status": PublicEntryGenerationStatus.COMPLETED,
            "updated_at": 2,
        }
    )
    current = previous.model_copy(
        update={"history": [completed], "event_id": 4}, deep=True
    )

    events = reconcile_snapshot(previous, current)

    assert isinstance(events[0], SessionSnapshot)
    update = next(event for event in events if isinstance(event, HistoryEntryUpdated))
    assert any(
        operation.op == "append"
        and operation.path == "/content/0/text"
        and operation.value == "o"
        for operation in update.patch
    )
    assert apply_json_patch(
        entry.model_dump(mode="json", by_alias=True), update.patch
    ) == completed.model_dump(mode="json", by_alias=True)


def test_live_snapshot_preserves_loaded_history_and_turn_prefixes() -> None:
    history = [
        PublicMessageEntry(
            id=f"message-{index}",
            session_id="session-1",
            turn_id=f"turn-{index}",
            role="assistant",
            content=[TextContentBlock(text=str(index))],
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            created_at=index + 1,
            updated_at=index + 1,
        )
        for index in range(250)
    ]
    turns = [
        PublicTurn(
            id=f"turn-{index}",
            session_id="session-1",
            status=PublicTurnStatus.COMPLETED,
            started_at=index + 1,
            completed_at=index + 1,
        )
        for index in range(250)
    ]
    state = _projection().state.model_copy(
        update={
            "history": history,
            "history_before_cursor": "before-loaded-history",
            "turns": turns,
        },
        deep=True,
    )
    projection = ClientProjection(state)
    retrying = PublicRetryState(
        turn_id=turns[-1].id,
        category=PublicRetryCategory.RATE_LIMITED,
        detail="HTTP 429",
    )
    snapshot = state.model_copy(
        update={
            "event_id": 1,
            "history": history[-200:],
            "history_before_cursor": history[-200].id,
            "turns": turns[-200:],
            "retrying": retrying,
        },
        deep=True,
    )

    event = projection.consume(
        Notification(
            method="session/snapshot",
            params={
                "eventId": 1,
                "sessionId": "session-1",
                "emittedAt": 1,
                "state": snapshot.model_dump(mode="json", by_alias=True),
            },
        )
    )

    assert isinstance(event, SessionSnapshot)
    assert [entry.id for entry in projection.history] == [entry.id for entry in history]
    assert projection.history_before_cursor == "before-loaded-history"
    assert [turn.id for turn in projection.state.turns or []] == [
        turn.id for turn in turns
    ]
    assert projection.state.retrying == retrying


def _read_call() -> ToolCallEvent:
    event = ToolCallEvent(
        tool_call_id="tool-1",
        tool_name="read_file",
        tool_class=ReadFile,
        args=ReadFileArgs(file_path="README.md"),
    )
    return event.model_copy(
        update={
            "presentation": ToolUIDataAdapter(ReadFile).get_call_presentation(event)
        }
    )


def _read_result() -> ToolResultEvent:
    event = ToolResultEvent(
        tool_call_id="tool-1",
        tool_name="read_file",
        tool_class=ReadFile,
        result=ReadFileResult(
            file_path="README.md", content="hello", num_lines=1, start_line=1
        ),
        duration=0.25,
    )
    return event.model_copy(
        update={
            "presentation": ToolUIDataAdapter(ReadFile).get_result_presentation(event)
        }
    )


def test_private_runtime_events_are_not_mirrored_to_public_history() -> None:
    projector = EventProjector("session-1", "turn-1")

    assert projector.project(PrivateRuntimeEvent(secret="do not expose")) == []
    assert projector.history == []


def test_tool_is_one_public_lifecycle_entry() -> None:
    projector = EventProjector("session-1", "turn-1")
    call = projector.project(_read_call())
    stream = projector.project(
        ToolStreamEvent(tool_call_id="tool-1", tool_name="read_file", message="reading")
    )
    result = projector.project(_read_result())

    assert call[0].method == "history/entryAdded"
    assert all(update.method == "history/entryUpdated" for update in [*stream, *result])
    assert len(projector.history) == 1
    entry = projector.history[0]
    assert isinstance(entry, PublicEffectEntry)
    assert isinstance(entry.state, CompletedEffectState)
    assert entry.generation_status == "completed"
    assert entry.state.duration_ms == 250

    projection = _projection()
    added = projection.consume(_notification(1, call[0]))
    streamed = projection.consume(_notification(2, stream[0]))
    completed = projection.consume(_notification(3, result[0]))
    assert isinstance(added, HistoryEntryAdded)
    assert isinstance(streamed, HistoryEntryUpdated)
    assert isinstance(completed, HistoryEntryUpdated)
    reduced = projection.history[0]
    assert isinstance(reduced, PublicEffectEntry)
    assert isinstance(reduced.state, CompletedEffectState)
    assert isinstance(reduced.state.output, dict)
    assert reduced.state.output["content"] == "hello"


def test_callback_entry_is_emitted_before_related_effect_is_blocked() -> None:
    projector = EventProjector("session-1", "turn-1")
    projector.project(_read_call())

    updates = projector.open_callback(
        "callback-1",
        ApprovalCallbackDetail(
            effect=projector.effect_detail("tool-1"), related_entry_id="tool-1"
        ),
        "Allow read_file?",
    )

    assert [update.method for update in updates] == [
        "history/entryAdded",
        "history/entryUpdated",
    ]
    assert isinstance(updates[0].params, HistoryEntryAddedParams)
    assert updates[0].params.entry.type == "callback"


def test_paged_history_and_callback_redelivery_share_one_identity_index() -> None:
    projector = EventProjector("session-1", "turn-1")
    projector.project(_read_call())
    projector.open_callback(
        "callback-1",
        ApprovalCallbackDetail(
            effect=projector.effect_detail("tool-1"), related_entry_id="tool-1"
        ),
        "Allow read_file?",
    )
    callback = projector.history[-1]
    assert isinstance(callback, PublicCallbackEntry)
    projection = _projection()
    projection.state.active_callbacks.append(callback)

    assert projection.ensure_callback(callback)
    assert projection.state.active_callbacks == [callback]

    projection.prepend_history_page([callback])

    assert projection.history == [callback]
    assert not projection.ensure_callback(callback)


def test_paged_history_rejects_conflicting_duplicate_identity() -> None:
    entry = PublicMessageEntry(
        id="user-1",
        session_id="session-1",
        role="user",
        content=[TextContentBlock(text="original")],
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        created_at=1,
        updated_at=1,
    )
    projection = _projection()
    projection.prepend_history_page([entry])
    conflicting = entry.model_copy(
        update={"content": [TextContentBlock(text="different")]}
    )

    with pytest.raises(ValueError, match="Conflicting paged history entry"):
        projection.prepend_history_page([conflicting])


def test_preview_and_title_updates_reduce_to_snapshot_metadata() -> None:
    projector = EventProjector("session-1", "turn-1")
    projection = _projection()
    updates = [
        *projector.project(
            UserMessageEvent(content="First prompt", message_id="user-1")
        ),
        *projector.project(
            SessionTitleUpdatedEvent(title="A useful title", session_id="session-1")
        ),
    ]

    for event_id, update in enumerate(updates, start=1):
        projection.consume(_notification(event_id, update))

    assert projection.state.session.preview == "First prompt"
    assert projection.state.session.title == "A useful title"


def test_user_message_projection_preserves_images_and_display_metadata() -> None:
    projector = EventProjector("session-1", "turn-1")
    metadata = UserDisplayContent(
        version="1.0.0",
        host="mistral-vscode",
        content=[{"type": "workspace_mention", "name": "app.py"}],
    )

    projector.project(
        UserMessageEvent(
            content="Look at app.py",
            message_id="user-1",
            images=[
                ImageAttachment(
                    source=InlineImageSource(data="aW1hZ2U="),
                    alias="diagram.png",
                    mime_type="image/png",
                )
            ],
            user_display_content=metadata,
        )
    )

    entry = projector.history[0]
    assert isinstance(entry, PublicMessageEntry)
    assert [image.alias for image in entry.images] == ["diagram.png"]
    assert entry.user_display_content == metadata


def test_user_message_projection_preserves_structured_resources() -> None:
    projector = EventProjector("session-1", "turn-1")

    projector.project(
        UserMessageEvent(
            content="Review the reference",
            message_id="user-resource",
            resources=[
                UserResourceLink(
                    uri="file:///workspace/spec.md",
                    media_type="text/markdown",
                    title="Specification",
                )
            ],
        )
    )

    entry = projector.history[0]
    assert isinstance(entry, PublicMessageEntry)
    assert entry.text == "Review the reference"
    resource = next(
        block for block in entry.content if isinstance(block, ResourceContentBlock)
    )
    assert resource.resource.uri == "file:///workspace/spec.md"
    assert isinstance(resource.resource, UserResourceLink)
    assert resource.resource.title == "Specification"


def test_streaming_message_is_added_patched_and_frozen() -> None:
    projector = EventProjector("session-1", "turn-1")
    added = projector.project(AssistantEvent(content="hell", message_id="message-1"))
    patched = projector.project(AssistantEvent(content="o", message_id="message-1"))
    completed = projector.finalize()

    assert added[0].method == "history/entryAdded"
    assert patched[0].method == "history/entryUpdated"
    assert completed[0].method == "history/entryUpdated"
    assert projector.history[0].generation_status == "completed"

    projection = _projection()
    projection.consume(_notification(1, added[0]))
    projection.consume(_notification(2, patched[0]))
    projection.consume(_notification(3, completed[0]))
    entry = projection.history[0]
    assert isinstance(entry, PublicMessageEntry)
    assert entry.text == "hello"

    with pytest.raises(ValueError, match="frozen"):
        projection.consume(_notification(4, patched[0]))


def test_in_progress_entry_identity_is_frozen() -> None:
    projector = EventProjector("session-1", "turn-1")
    projector.project(AssistantEvent(content="hello", message_id="message-1"))

    with pytest.raises(ValueError, match="identity is frozen"):
        projector._patch(
            "message-1",
            [JsonPatchOperation(op="replace", path="/turnId", value="turn-2")],
        )

    assert projector.history[0].turn_id == "turn-1"


def test_tool_call_completes_streamed_text_before_adding_effect() -> None:
    projector = EventProjector("session-1", "turn-1")
    projector.project(ReasoningEvent(content="thinking", message_id="reasoning-1"))
    projector.project(AssistantEvent(content="answer", message_id="message-1"))

    updates = projector.project(_read_call())

    assert [update.method for update in updates] == [
        "history/entryUpdated",
        "history/entryUpdated",
        "history/entryAdded",
    ]
    reasoning, message, effect = projector.history
    assert isinstance(reasoning, PublicReasoningEntry)
    assert isinstance(message, PublicMessageEntry)
    assert isinstance(effect, PublicEffectEntry)
    assert reasoning.generation_status == "completed"
    assert message.generation_status == "completed"
    assert effect.generation_status == "in_progress"

    with pytest.raises(ValueError, match="frozen"):
        projector.project(ReasoningEvent(content="late", message_id="reasoning-1"))


def test_event_sequence_rejects_gaps_and_read_resynchronizes() -> None:
    projector = EventProjector("session-1", "turn-1")
    added = projector.project(AssistantEvent(content="hello", message_id="message-1"))
    projection = _projection()
    projection.consume(_notification(1, added[0]))

    with pytest.raises(EventSequenceError, match="expected 2, received 3"):
        projection.consume(_notification(3, added[0]))

    state = projection.state.model_copy(update={"event_id": 3})
    with pytest.raises(EventSequenceError, match="expected 2, received 3"):
        projection.consume(
            Notification(
                method="session/snapshot",
                params={
                    "eventId": 3,
                    "sessionId": "session-1",
                    "emittedAt": 3,
                    "state": state.model_dump(mode="json", by_alias=True),
                },
            )
        )

    projection.replace_state(state)
    added_after_resync = projector.project(
        AssistantEvent(content="again", message_id="message-2")
    )
    assert isinstance(
        projection.consume(_notification(4, added_after_resync[0])), HistoryEntryAdded
    )


def test_session_handoff_atomically_replaces_projection_and_watermark() -> None:
    projection = _projection()
    replacement = PublicSessionState(
        event_id=1,
        session=PublicSession(
            id="session-2", status=IdleSessionStatus(), created_at=2, updated_at=2
        ),
        history=[],
        turns=[],
        active_callbacks=[],
    )

    event = projection.consume(
        Notification(
            method="session/compacted",
            params={
                "eventId": 1,
                "sessionId": "session-2",
                "oldSessionId": "session-1",
                "emittedAt": 2,
                "state": replacement.model_dump(mode="json", by_alias=True),
                "sessionLog": {"enabled": False},
                "summaryLength": 42,
            },
        )
    )

    assert isinstance(event, SessionCompacted)
    assert projection.state.session.id == "session-2"
    assert projection.state.event_id == 1


def test_stats_notification_updates_snapshot_token_usage() -> None:
    projection = _projection()

    event = projection.consume(
        Notification(
            method="session/statsUpdated",
            params={
                "eventId": 1,
                "sessionId": "session-1",
                "emittedAt": 2,
                "contextWindow": 100,
                "stats": {
                    "sessionPromptTokens": 12,
                    "sessionCompletionTokens": 8,
                    "sessionCachedTokens": 5,
                    "lastTurnCachedTokens": 3,
                },
            },
        )
    )

    usage = projection.state.session.token_usage
    assert usage is not None
    assert usage.input_tokens == 12
    assert usage.output_tokens == 8
    assert usage.total_tokens == 20
    assert isinstance(event, StatsUpdated)
    assert event.params.stats.session_cached_tokens == 5
    assert event.params.stats.last_turn_cached_tokens == 3


@pytest.mark.parametrize("event_id", [0, -1, True])
def test_event_sequence_requires_positive_integer_ids(event_id: int | bool) -> None:
    projection = _projection()
    projector = EventProjector("session-1", "turn-1")
    added = projector.project(AssistantEvent(content="hello", message_id="message-1"))

    with pytest.raises((EventSequenceError, ValidationError)):
        notification = _notification(1, added[0])
        notification.params["eventId"] = event_id
        projection.consume(notification)


def test_event_sequence_requires_one_after_initial_watermark() -> None:
    projection = _projection()
    projector = EventProjector("session-1", "turn-1")
    added = projector.project(AssistantEvent(content="hello", message_id="message-1"))

    with pytest.raises(EventSequenceError, match="expected 1, received 2"):
        projection.consume(_notification(2, added[0]))


def test_event_sequence_rejects_notifications_for_another_session() -> None:
    projection = _projection()
    projector = EventProjector("session-1", "turn-1")
    added = projector.project(AssistantEvent(content="hello", message_id="message-1"))

    with pytest.raises(EventSequenceError, match="session-2.*session-1"):
        notification = _notification(1, added[0])
        notification.params["sessionId"] = "session-2"
        projection.consume(notification)


def test_snapshot_requires_matching_session_and_watermark() -> None:
    projection = _projection()
    state = projection.state.model_copy(update={"event_id": 1})

    with pytest.raises(EventSequenceError, match="watermark"):
        projection.consume(
            Notification(
                method="session/snapshot",
                params={
                    "eventId": 1,
                    "sessionId": "session-1",
                    "emittedAt": 1,
                    "state": state.model_copy(update={"event_id": 2}).model_dump(
                        mode="json", by_alias=True
                    ),
                },
            )
        )

    with pytest.raises(EventSequenceError, match="snapshot session"):
        projection.consume(
            Notification(
                method="session/snapshot",
                params={
                    "eventId": 1,
                    "sessionId": "session-1",
                    "emittedAt": 1,
                    "state": state.model_copy(
                        update={
                            "session": state.session.model_copy(
                                update={"id": "session-2"}
                            )
                        }
                    ).model_dump(mode="json", by_alias=True),
                },
            )
        )

    projection.replace_state(state)
    with pytest.raises(EventSequenceError, match="watermark"):
        projection.consume(
            Notification(
                method="session/snapshot",
                params={
                    "eventId": 1,
                    "sessionId": "session-1",
                    "emittedAt": 1,
                    "state": state.model_copy(update={"event_id": 2}).model_dump(
                        mode="json", by_alias=True
                    ),
                },
            )
        )


@pytest.mark.parametrize("method", ["future/resourceUpdated", "unknown/event"])
def test_unknown_notifications_fail_strictly(method: str) -> None:
    projection = _projection()

    with pytest.raises(UnknownNotificationError, match=method):
        projection.consume(Notification(method=method, params={"revision": 1}))
