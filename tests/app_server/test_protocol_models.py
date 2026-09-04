"""Tests for the live app-server wire models."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from vibe.app_server._model import validate_wire
from vibe.app_server.models import (
    IdleSessionStatus,
    MCPSourceSummary,
    PublicQueuedTurn,
    PublicRetryCategory,
    PublicRetryState,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnQueue,
    PublicTurnStatus,
)
from vibe.app_server.protocol import (
    SERVER_METHODS,
    AgentConfig,
    CallbackResult,
    CallbackResultError,
    CallbackResultResponse,
    EventWatermarkResponse,
    InitializeParams,
    MessageAnnotations,
    PageRequest,
    SessionContinueResponse,
    SessionEmbeddedResourceContentBlock,
    SessionForkResponse,
    SessionHistoryListParams,
    SessionImageContentBlock,
    SessionReadResponse,
    SessionResourceLinkContentBlock,
    SessionResumeResponse,
    SessionShellCommandResponse,
    SessionStartParams,
    SessionStartResponse,
    SessionTextContentBlock,
    TurnContextInputEntry,
    TurnEnqueueParams,
    TurnEnqueueResponse,
    TurnInterruptResponse,
    TurnQueueReadResponse,
    TurnQueueRemoveParams,
    TurnQueueRemoveResponse,
    TurnQueueReplaceParams,
    TurnQueueReplaceResponse,
    TurnQueueResumeResponse,
    TurnQueueUpdatedParams,
    TurnStartResponse,
    TurnSteerResponse,
    TurnUserInputEntry,
)
from vibe.user_content import UserDisplayContent


def _turn_queue() -> PublicTurnQueue:
    return PublicTurnQueue(
        items=[
            PublicQueuedTurn(
                id="queue-1",
                created_at=10,
                entries=[
                    TurnUserInputEntry(
                        entry_id="user-2",
                        content=[SessionTextContentBlock(text="next prompt")],
                    )
                ],
            )
        ],
        paused=True,
    )


def test_public_protocol_has_no_session_mcp_methods() -> None:
    assert not [
        method for method in SERVER_METHODS if method.startswith("session/mcp/")
    ]


def test_wire_models_serialize_camel_case_and_reject_snake_case_wire_keys() -> None:
    params = SessionHistoryListParams.model_validate({
        "sessionId": "session-1",
        "page": {"cursor": "entry-1", "limit": 10, "direction": "backward"},
    })

    assert params.model_dump(mode="json") == {
        "sessionId": "session-1",
        "turnId": None,
        "page": {"cursor": "entry-1", "limit": 10, "direction": "backward"},
    }

    with pytest.raises(ValidationError):
        validate_wire(SessionHistoryListParams, {"session_id": "session-1"})


def test_public_session_state_carries_optional_retry_state() -> None:
    session = PublicSession(
        id="session-1", status=IdleSessionStatus(), created_at=1, updated_at=1
    )

    idle = PublicSessionState(event_id=0, session=session)
    retrying = PublicSessionState(
        event_id=1,
        session=session,
        retrying=PublicRetryState(
            turn_id="turn-1",
            category=PublicRetryCategory.RATE_LIMITED,
            detail="HTTP 429",
        ),
    )

    assert idle.model_dump(mode="json")["retrying"] is None
    assert retrying.model_dump(mode="json")["retrying"] == {
        "turnId": "turn-1",
        "category": "rate_limited",
        "detail": "HTTP 429",
    }


def test_public_session_state_carries_optional_runtime_quiescence() -> None:
    session = PublicSession(
        id="session-1", status=IdleSessionStatus(), created_at=1, updated_at=1
    )

    unknown = PublicSessionState(event_id=0, session=session)
    pending = PublicSessionState(event_id=1, session=session, is_quiescent=False)

    assert unknown.model_dump(mode="json")["isQuiescent"] is None
    assert pending.model_dump(mode="json")["isQuiescent"] is False


def test_public_session_harness_field_defaults_to_none() -> None:
    session = PublicSession(
        id="session-1", status=IdleSessionStatus(), created_at=1, updated_at=1
    )

    assert session.harness is None
    assert session.model_dump(mode="json")["harness"] is None


@pytest.mark.parametrize("harness", ["legacy", "unified"])
def test_public_session_harness_field_accepts_provenance(harness: str) -> None:
    session = PublicSession(
        id="session-1",
        status=IdleSessionStatus(),
        created_at=1,
        updated_at=1,
        harness=harness,  # type: ignore[arg-type]
    )

    assert session.harness == harness
    assert session.model_dump(mode="json")["harness"] == harness


def test_public_session_harness_field_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        PublicSession(
            id="session-1",
            status=IdleSessionStatus(),
            created_at=1,
            updated_at=1,
            harness="other",  # type: ignore[arg-type]
        )


def test_agent_config_carries_app_server_and_vibe_launch_fields() -> None:
    config = AgentConfig.model_validate({
        "completion": {"type": "mistral", "model": "mistral-large-latest"},
        "sandbox": {"type": "managed", "networkAccess": False},
        "instructions": "Use the project conventions.",
        "workdir": "/workspace",
        "workspaceRoots": ["/workspace", "/shared"],
        "agent": "plan",
        "tools": [
            {
                "name": "select_customer",
                "description": "Ask the client to select a customer.",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
            }
        ],
        "hooks": [{"type": "before_tool", "name": "guard"}],
    })

    assert config.completion is not None
    assert config.completion.model == "mistral-large-latest"
    assert config.workdir == "/workspace"
    assert config.workspace_roots == ["/workspace", "/shared"]
    assert config.tools[0].input_schema == {"type": "object"}


def test_session_start_wraps_vibe_configuration_in_agent_config() -> None:
    params = SessionStartParams(
        agent_config=AgentConfig(cwd="/workspace", headless=True), history_limit=100
    )

    assert params.model_dump(mode="json") == {
        "agentConfig": {
            "completion": None,
            "sandbox": None,
            "instructions": "",
            "workdir": None,
            "tools": [],
            "hooks": [],
            "cwd": "/workspace",
            "workspaceRoots": [],
            "worktree": None,
            "agent": None,
            "autoApprove": False,
            "enabledTools": None,
            "disabledTools": [],
            "maxTurns": None,
            "maxPrice": None,
            "maxSessionTokens": None,
            "headless": True,
            "trustWorkspace": False,
            "mcpServers": [],
        },
        "historyLimit": 100,
        "idempotencyKey": None,
        "kind": "normal",
    }


def test_initialize_accepts_the_desktop_entrypoint_off_the_wire() -> None:
    """Vibe Desktop identifies itself here; the value drives analytics attribution."""
    params = validate_wire(
        InitializeParams,
        {
            "clientInfo": {
                "name": "vibe_desktop",
                "title": "Vibe Desktop",
                "version": "1.2.3",
                "entrypoint": "desktop",
            }
        },
    )

    assert params.client_info.entrypoint == "desktop"
    assert params.client_info.name == "vibe_desktop"


def test_page_request_uses_canonical_pagination_shape() -> None:
    assert PageRequest(limit=10, direction="forward").model_dump(mode="json") == {
        "cursor": None,
        "limit": 10,
        "direction": "forward",
    }


def test_public_turn_queue_serializes_order_and_turn_link() -> None:
    queue = _turn_queue()
    turn = PublicTurn(
        id="turn-2",
        session_id="session-1",
        status=PublicTurnStatus.IN_PROGRESS,
        started_at=20,
        queue_item_id="queue-1",
    )

    assert queue.model_dump(mode="json") == {
        "items": [
            {
                "id": "queue-1",
                "createdAt": 10,
                "entries": [
                    {
                        "role": "user",
                        "entryId": "user-2",
                        "content": [{"type": "text", "text": "next prompt"}],
                        "annotations": {},
                    }
                ],
            }
        ],
        "paused": True,
        "maxItems": 32,
    }
    assert turn.model_dump(mode="json")["queueItemId"] == "queue-1"


def test_turn_queue_protocol_models_use_camel_case() -> None:
    queue = _turn_queue()
    params = TurnEnqueueParams(
        idempotency_key="enqueue-1",
        session_id="session-1",
        entries=[
            TurnUserInputEntry(
                entry_id="user-2", content=[SessionTextContentBlock(text="next prompt")]
            )
        ],
    )
    replace = TurnQueueReplaceParams(
        idempotency_key="replace-1",
        session_id="session-1",
        queue_item_id="queue-1",
        entries=params.entries,
    )
    remove = TurnQueueRemoveParams(session_id="session-1", queue_item_id="queue-1")
    notification = TurnQueueUpdatedParams(
        event_id=3, session_id="session-1", emitted_at=30, queue=queue
    )

    assert params.model_dump(mode="json") == {
        "idempotencyKey": "enqueue-1",
        "sessionId": "session-1",
        "entries": [
            {
                "role": "user",
                "entryId": "user-2",
                "content": [{"type": "text", "text": "next prompt"}],
                "annotations": {},
            }
        ],
    }
    assert params.input == params.entries[0].content
    assert replace.model_dump(mode="json") == {
        "idempotencyKey": "replace-1",
        "sessionId": "session-1",
        "entries": params.model_dump(mode="json")["entries"],
        "queueItemId": "queue-1",
    }
    assert remove.model_dump(mode="json") == {
        "sessionId": "session-1",
        "queueItemId": "queue-1",
    }
    assert notification.model_dump(mode="json") == {
        "eventId": 3,
        "sessionId": "session-1",
        "emittedAt": 30,
        "queue": queue.model_dump(mode="json"),
    }

    assert (
        TurnEnqueueParams(
            session_id="session-1",
            entries=[
                TurnUserInputEntry(
                    content=[SessionTextContentBlock(text="next prompt")]
                )
            ],
        ).idempotency_key
        is None
    )


def test_canonical_enqueue_rejects_the_old_vibe_payload() -> None:
    with pytest.raises(ValidationError):
        validate_wire(
            TurnEnqueueParams,
            {
                "sessionId": "session-1",
                "messageEntryId": "user-2",
                "message": [{"type": "text", "text": "next prompt"}],
                "replaceQueueItemId": "queue-1",
            },
        )


def test_canonical_enqueue_accepts_context_user_content_and_annotations() -> None:
    display = UserDisplayContent(
        version="1", host="vibe", content=[{"type": "text", "text": "display text"}]
    )
    params = TurnEnqueueParams(
        session_id="session-1",
        entries=[
            TurnContextInputEntry(
                entry_id="context-1",
                content=[SessionTextContentBlock(text="hidden context")],
            ),
            TurnUserInputEntry(
                entry_id="user-1",
                content=[
                    SessionTextContentBlock(text="visible prompt"),
                    SessionImageContentBlock(
                        uri="data:image/png;base64,aGVsbG8=",
                        media_type="image/png",
                        alt_text="image.png",
                    ),
                    SessionResourceLinkContentBlock(
                        uri="file:///workspace/notes.md", name="notes.md"
                    ),
                    SessionEmbeddedResourceContentBlock(
                        uri="file:///workspace/context.txt",
                        media_type="text/plain",
                        text="attached context",
                    ),
                ],
                annotations=MessageAnnotations.model_validate({
                    "vibe.userDisplayContent": display
                }),
            ),
        ],
    )

    dumped = params.model_dump(mode="json")

    assert [entry["role"] for entry in dumped["entries"]] == ["context", "user"]
    assert [block["type"] for block in dumped["entries"][1]["content"]] == [
        "text",
        "image",
        "resource_link",
        "embedded_resource",
    ]
    assert dumped["entries"][1]["annotations"] == {
        "vibe.userDisplayContent": display.model_dump(mode="json")
    }


def test_canonical_enqueue_requires_the_user_entry_to_be_last() -> None:
    with pytest.raises(ValidationError, match="final turn input entry"):
        TurnEnqueueParams(
            session_id="session-1",
            entries=[
                TurnUserInputEntry(
                    content=[SessionTextContentBlock(text="visible prompt")]
                ),
                TurnContextInputEntry(
                    content=[SessionTextContentBlock(text="hidden context")]
                ),
            ],
        )


def test_turn_queue_methods_are_advertised() -> None:
    assert {
        "app_server/session/turn/enqueue",
        "app_server/session/turn/queue/read",
        "app_server/session/turn/queue/remove",
        "app_server/session/turn/queue/replace",
        "app_server/session/turn/queue/resume",
    }.issubset(SERVER_METHODS)
    assert {
        "turn/enqueue",
        "turn/queue/read",
        "turn/queue/remove",
        "turn/queue/resume",
        "vibe/turn/queue/replace",
    }.isdisjoint(SERVER_METHODS)


def test_turn_queue_command_results_are_minimal() -> None:
    assert TurnEnqueueResponse(queue_item_id="queue-1").model_dump(mode="json") == {
        "queueItemId": "queue-1"
    }
    assert TurnQueueReplaceResponse(queue_item_id="queue-1").model_dump(
        mode="json"
    ) == {"queueItemId": "queue-1"}
    assert TurnQueueReadResponse(queue=_turn_queue()).model_dump(mode="json") == {
        "queue": _turn_queue().model_dump(mode="json")
    }
    assert TurnQueueRemoveResponse().model_dump(mode="json") == {}
    assert TurnQueueResumeResponse().model_dump(mode="json") == {}


@pytest.mark.parametrize(
    "response_type",
    [
        SessionStartResponse,
        SessionReadResponse,
        SessionResumeResponse,
        SessionContinueResponse,
        SessionForkResponse,
        TurnStartResponse,
        TurnSteerResponse,
        TurnInterruptResponse,
        CallbackResultResponse,
    ],
)
def test_event_watermark_responses_share_a_base(
    response_type: type[EventWatermarkResponse],
) -> None:
    assert issubclass(response_type, EventWatermarkResponse)


def test_event_watermark_defaults_and_shell_requires_an_event_id() -> None:
    assert TurnSteerResponse().model_dump(mode="json") == {
        "lastEventId": 0,
        "accepted": True,
    }

    with pytest.raises(ValidationError):
        validate_wire(SessionShellCommandResponse, {"accepted": True})


def test_callback_result_accepts_rfc_output_or_error_shapes() -> None:
    assert CallbackResult.model_validate({
        "callbackId": "callback-1",
        "output": {"approved": True},
    }).model_dump(mode="json") == {
        "callbackId": "callback-1",
        "output": {"approved": True},
        "error": None,
    }
    assert CallbackResult(
        callback_id="callback-1",
        error=CallbackResultError(
            message="Client tool is unavailable",
            code="client_unavailable",
            details={"retryable": False},
        ),
    ).model_dump(mode="json") == {
        "callbackId": "callback-1",
        "output": None,
        "error": {
            "message": "Client tool is unavailable",
            "code": "client_unavailable",
            "details": {"retryable": False},
        },
    }

    assert CallbackResult(callback_id="callback-1").model_dump(mode="json") == {
        "callbackId": "callback-1",
        "output": None,
        "error": None,
    }


def test_an_mcp_source_names_its_owning_plugin_without_breaking_older_clients() -> None:
    configured = validate_wire(
        MCPSourceSummary,
        {
            "name": "linear",
            "kind": "server",
            "transport": "http",
            "status": "connected",
        },
    )
    owned = validate_wire(
        MCPSourceSummary,
        {
            "name": "figma",
            "kind": "server",
            "transport": "http",
            "status": "needs_auth",
            "pluginName": "figma",
        },
    )

    assert configured.plugin_name is None
    assert owned.plugin_name == "figma"
    assert owned.model_dump(mode="json")["pluginName"] == "figma"

    with pytest.raises(ValidationError):
        validate_wire(
            MCPSourceSummary,
            {
                "name": "figma",
                "kind": "server",
                "transport": "http",
                "status": "needs_auth",
                "plugin_name": "figma",
            },
        )
