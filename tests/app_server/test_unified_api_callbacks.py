"""Approval callbacks, and the Turn and status state, the adapter builds from Session API events."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionSucceededEvent,
    RustRuntimeBuiltinToolCall,
    RustRuntimeBuiltinToolCallAction,
    RustTextContentBlock,
    RustTokenUsage,
)
from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    AnsweredCallbackOutcome,
    ApprovalCallback as ApiApprovalCallback,
    CallbackOpenedEvent,
    CallbackResolvedEvent,
    FailedTermination,
    ProtocolError,
    TurnStartedEvent,
    TurnState,
    TurnTerminatedEvent,
    TurnUpdatedEvent,
    UnknownTermination,
)
from mistralai_vibe_local_harness.vibe import _local_actions
from mistralai_vibe_local_harness.vibe._session_api_extensions import RETRYING_UPDATED
from tests.app_server._session_api_harness import StreamBatches, api_adapter, publish
from vibe.app_server._effect_models import ShellEffectDetail
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_api_commands import SessionApiCommands
from vibe.app_server.events import (
    AppServerEvent,
    CallbackRequested,
    HistoryEntryUpdated,
    SessionSnapshot,
    TurnStarted,
)
from vibe.app_server.models import (
    AnsweredCallbackState,
    ApprovalCallbackDetail,
    BlockedSessionStatus,
    FailedSessionStatus,
    IdleSessionStatus,
    OpenCallbackState,
    PublicCallbackEntry,
    PublicSessionState,
    PublicTurnStatus,
    RunningSessionStatus,
    TextContentBlock,
)
from vibe.app_server.protocol import CallbackResultError, TurnStartParams

COMMAND = "vibe-test-tool --check"
"""A command no allowlist covers, so it asks; it does not exist, so it is harmless."""

USAGE = RustTokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


def _settled_callback(events: Sequence[AppServerEvent]) -> PublicCallbackEntry:
    [settled] = [
        event.entry
        for event in events
        if isinstance(event, HistoryEntryUpdated)
        and isinstance(event.entry, PublicCallbackEntry)
    ]
    return settled


@pytest.mark.asyncio
async def test_a_snapshot_sent_while_a_callback_is_open_still_lists_it(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path, and a published batch
        that blocks the Session on an approval callback.
    *Do*: Publish an unrelated batch, a provider retry, which sends the client
        a whole ``session/snapshot``.
    *Assert*: The snapshot still lists the callback as active, so it does not
        tell the client the callback is gone.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        batches = StreamBatches(adapter.session_id)
        await publish(
            adapter,
            batches.batch(
                TurnStartedEvent(turn=TurnState(id="turn-1", status="blocked")),
                CallbackOpenedEvent(
                    callback=ApiApprovalCallback(
                        id="approval-call-1",
                        turn_id="turn-1",
                        tool_call_id="call-1",
                        name="file_system.bash",
                        title="Approve file_system.bash",
                        input={"command": COMMAND},
                    )
                ),
            ),
        )
        retrying: JsonValue = {
            "turnId": "turn-1",
            "category": "rate_limited",
            "detail": "Rate limited",
            "retryAt": 2,
            "retryAttempt": 1,
        }

        # Do
        events = await publish(
            adapter,
            batches.batch(extensions=[(RETRYING_UPDATED, {"retrying": retrying})]),
        )

    # Assert
    [snapshot] = [
        event.event for event in events if isinstance(event.event, SessionSnapshot)
    ]
    assert [callback.callback_id for callback in snapshot.state.active_callbacks] == [
        "approval-call-1"
    ]


@pytest.mark.asyncio
async def test_api_events_alone_give_turn_status_and_callback_state(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path, and batches that hold
        only Session API events, with no ``vibe.*`` item for Turns, status or
        callbacks, as the target runtime sends them.
    *Do*: Publish a Turn start, an approval opening, the approval's answer,
        then the Turn's failure.
    *Assert*: The running Turn reads as running; the approval reaches the
        client as an open callback, is kept for its answer and blocks the
        Session; the answer closes it; the failed Turn carries its error and
        fails the Session.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        batches = StreamBatches(adapter.session_id)
        approval = ApiApprovalCallback(
            id="approval-1",
            turn_id="turn-1",
            tool_call_id="call-1",
            name="file_system.bash",
            title="Approve file_system.bash",
            input={"command": COMMAND},
        )
        error = ProtocolError(message="The provider refused", code="provider_error")

        # Do
        started = await publish(
            adapter,
            batches.batch(
                TurnStartedEvent(
                    turn=TurnState(id="turn-1", status="running", started_at=10)
                )
            ),
        )
        running = _translated(adapter)
        opened = await publish(
            adapter,
            batches.batch(
                TurnUpdatedEvent(
                    turn=TurnState(id="turn-1", status="blocked", started_at=10)
                ),
                CallbackOpenedEvent(callback=approval),
                emitted_at=11,
            ),
        )
        blocked = _translated(adapter)
        answerable = list(adapter._open_callbacks)
        resolved = await publish(
            adapter,
            batches.batch(
                CallbackResolvedEvent(
                    callback_id="approval-1",
                    outcome=AnsweredCallbackOutcome(
                        output={"type": "approval", "decision": {"type": "deny"}}
                    ),
                ),
                TurnUpdatedEvent(
                    turn=TurnState(id="turn-1", status="running", started_at=10)
                ),
                emitted_at=12,
            ),
        )
        answered = _translated(adapter)
        await publish(
            adapter,
            batches.batch(
                TurnTerminatedEvent(
                    turn=TurnState(
                        id="turn-1",
                        status="terminated",
                        started_at=10,
                        completed_at=13,
                        termination=FailedTermination(error=error),
                    )
                )
            ),
        )
        failed = _translated(adapter)

    # Assert
    assert [e.event.turn.id for e in started if isinstance(e.event, TurnStarted)] == [
        "turn-1"
    ]
    assert running.session.status == RunningSessionStatus(active_turn_id="turn-1")
    assert [turn.status for turn in running.turns or []] == [
        PublicTurnStatus.IN_PROGRESS
    ]

    [requested] = [
        event.event.callback
        for event in opened
        if isinstance(event.event, CallbackRequested)
    ]
    assert answerable == [(adapter.session_id, "approval-1")]
    assert requested.turn_id == "turn-1"
    assert requested.state == OpenCallbackState()
    assert isinstance(requested.detail, ApprovalCallbackDetail)
    assert isinstance(requested.detail.effect, ShellEffectDetail)
    assert requested.detail.effect.input is not None
    assert requested.detail.effect.input.command == COMMAND
    assert blocked.session.status == BlockedSessionStatus(
        active_turn_id="turn-1", callback_id="approval-1", reason="approval"
    )
    assert [callback.callback_id for callback in blocked.active_callbacks] == [
        "approval-1"
    ]

    settled = _settled_callback([event.event for event in resolved])
    assert settled.callback_id == "approval-1"
    assert isinstance(settled.state, AnsweredCallbackState)
    assert adapter._open_callbacks == {}
    assert answered.active_callbacks == []
    assert answered.session.status == RunningSessionStatus(active_turn_id="turn-1")

    [turn] = failed.turns or []
    assert turn.status is PublicTurnStatus.FAILED
    assert turn.completed_at == 13
    assert turn.error is not None
    assert (turn.error.message, turn.error.code) == (
        "The provider refused",
        "provider_error",
    )
    assert failed.session.status == FailedSessionStatus(message="The provider refused")


@pytest.mark.asyncio
async def test_a_rejected_delivery_closes_the_approval_through_the_session_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path, a Turn that waits on
        its model, and an approval the Turn opens.
    *Do*: Reject the approval's delivery, as the app server does when the
        client refuses the callback request.
    *Assert*: The rejection goes through the Session API's ``callback/result``
        and denies the approval, so the Turn is not left waiting.
    """
    # Prepare
    release = asyncio.Event()

    async def execute_completion(
        action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        await release.wait()
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=[RustTextContentBlock(text="done")],
                finish_reason="stop",
                usage=USAGE,
            ),
        )

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)
    sent: list[str] = []
    request = SessionApiCommands.respond_to_callback

    async def recording(self: SessionApiCommands, params: Any) -> Any:
        sent.append(params.result.callback_id)
        return await request(self, params)

    monkeypatch.setattr(SessionApiCommands, "respond_to_callback", recording)
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        await adapter.start_turn(
            TurnStartParams(
                session_id=adapter.session_id, message=[TextContentBlock(text="Go")]
            )
        )
        session = adapter._session
        turn_id = session.active_turn_id
        assert turn_id is not None
        approval = asyncio.create_task(
            session._request_approval(
                RustRuntimeBuiltinToolCallAction(
                    action_id="approval-action",
                    turn_id=turn_id,
                    call_id="approval-call",
                    call=RustRuntimeBuiltinToolCall(
                        name="file_system.bash", arguments={"command": COMMAND}
                    ),
                )
            )
        )
        async with asyncio.timeout(5):
            while not session.open_callbacks():
                await asyncio.sleep(0)
        [callback] = session.open_callbacks()
        callback_id = str(callback["callbackId"])

        # Do
        await adapter.reject_callback_delivery(
            adapter.session_id,
            callback_id,
            CallbackResultError(message="Client did not accept callback delivery"),
        )

        # Assert
        grant = await asyncio.wait_for(approval, timeout=5)
        assert sent == [callback_id]
        assert not grant.approved
        assert session.open_callbacks() == ()
        release.set()


@pytest.mark.asyncio
async def test_a_turn_that_ends_with_an_outcome_from_a_newer_runtime_reads_as_interrupted(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path, and a started Turn.
    *Do*: Publish the Turn's end with an outcome this version does not know.
    *Assert*: The Turn reads as interrupted and the Session is idle.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        batches = StreamBatches(adapter.session_id)
        await publish(
            adapter,
            batches.batch(
                TurnStartedEvent(
                    turn=TurnState(id="turn-1", status="running", started_at=10)
                )
            ),
        )
        termination = UnknownTermination.model_validate({
            "outcome": "budget_exceeded",
            "spent": 3,
        })

        # Do
        await publish(
            adapter,
            batches.batch(
                TurnTerminatedEvent(
                    turn=TurnState(
                        id="turn-1",
                        status="terminated",
                        started_at=10,
                        completed_at=13,
                        termination=termination,
                    )
                )
            ),
        )
        ended = _translated(adapter)

    # Assert
    [turn] = ended.turns or []
    assert turn.status is PublicTurnStatus.INTERRUPTED
    assert turn.completed_at == 13
    assert ended.session.status == IdleSessionStatus()


def _translated(adapter: Any) -> PublicSessionState:
    state = adapter._translated_state
    assert state is not None
    return state
