"""Tests for the live app-server wire models."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from vibe.app_server._model import validate_wire
from vibe.app_server.protocol import (
    AgentConfig,
    CallbackResult,
    CallbackResultError,
    CallbackResultResponse,
    EventWatermarkResponse,
    PageRequest,
    SessionContinueResponse,
    SessionForkResponse,
    SessionHistoryListParams,
    SessionReadResponse,
    SessionResumeResponse,
    SessionShellCommandResponse,
    SessionStartParams,
    SessionStartResponse,
    TurnInterruptResponse,
    TurnStartResponse,
    TurnSteerResponse,
)


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


def test_page_request_uses_canonical_pagination_shape() -> None:
    assert PageRequest(limit=10, direction="forward").model_dump(mode="json") == {
        "cursor": None,
        "limit": 10,
        "direction": "forward",
    }


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
