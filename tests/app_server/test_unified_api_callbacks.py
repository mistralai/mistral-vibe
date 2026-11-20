"""Approval callbacks, and the Turn and status state, the adapter builds from Session API events."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import json
from pathlib import Path
from typing import Any

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionResultToolCallPart,
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
from tests.app_server._session_api_harness import (
    StreamBatches,
    api_adapter,
    forwarding,
    publish,
    until,
)
from tests.stubs.subagent_programs import spawn_only
from vibe.app_server._effect_models import ShellEffectDetail
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_api_commands import SessionApiCommands
from vibe.app_server._session_api_path import SessionApiPath
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
from vibe.app_server.protocol import (
    CallbackResult,
    CallbackResultError,
    CallbackResultParams,
    SessionOptions,
    SessionReadParams,
    TurnStartParams,
)

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
async def test_open_approvals_for_a_reconnect_come_from_the_session_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path, a Turn that waits on
        its model, and an approval the Turn opens. The local runtime's own list
        of open callbacks is taken away, as on the target runtime.
    *Do*: Read the open callbacks a reconnecting client is sent, before and
        after the approval is answered.
    *Assert*: The approval is listed, with the payload the local runtime gave
        before, and is no longer listed once answered.
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
    async with (
        api_adapter(tmp_path, process_services=process_services) as harness,
        forwarding(harness) as forwarded,
    ):
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
        await until(
            lambda: any(isinstance(item.event, CallbackRequested) for item in forwarded)
        )
        [from_local_runtime] = adapter.open_callbacks()
        [delivered] = [
            item.event.callback
            for item in forwarded
            if isinstance(item.event, CallbackRequested)
        ]
        host = adapter._host
        assert host is not None

        def no_local_runtime(_root_session_id: str) -> tuple[()]:
            raise AssertionError("asked the local runtime for its open callbacks")

        monkeypatch.setattr(host, "open_callbacks", no_local_runtime)

        # Do
        [open_approval] = await adapter.read_open_callbacks()
        await adapter.respond_to_callback(
            CallbackResultParams(
                session_id=adapter.session_id,
                result=CallbackResult(
                    callback_id=open_approval.callback_id,
                    output={"decision": {"type": "approve"}},
                ),
            )
        )
        assert (await asyncio.wait_for(approval, timeout=5)).approved
        after_answer = await adapter.read_open_callbacks()
        release.set()

    # Assert
    assert isinstance(open_approval.state, OpenCallbackState)
    assert isinstance(open_approval.detail, ApprovalCallbackDetail)
    # The approval was asked without a tool call in history, so only the
    # Runtime links it to an entry; the stream did not, nor did its delivery.
    unlinked: Any = {
        "created_at": True,
        "updated_at": True,
        "related_entry_id": True,
        "detail": {"related_entry_id"},
    }
    assert open_approval.model_dump(exclude=unlinked) == (
        from_local_runtime.model_dump(exclude=unlinked)
    )
    assert open_approval.related_entry_id == delivered.related_entry_id
    assert open_approval.created_at == delivered.created_at
    assert after_answer == []


class _RootSpawnsAskingChild:
    """The root spawns subagent "checker", whose first completion runs a command that asks."""

    def __init__(self) -> None:
        self.root = 0
        self.child = 0

    async def __call__(
        self, action: Any, *_args: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        if action.turn_id is not None and action.turn_id.startswith("child-"):
            self.child += 1
            if self.child == 1:
                return _completion(
                    action, "bash", json.dumps({"command": COMMAND}), "child-bash-1"
                )
            return _completion(action, text="child done")
        self.root += 1
        if self.root == 1:
            return _completion(
                action,
                "run_typescript",
                json.dumps({"code": spawn_only("checker", "Go")}),
                "program-1",
            )
        return _completion(action, text="done")


def _completion(
    action: Any,
    tool: str | None = None,
    arguments: str = "",
    call_id: str = "",
    *,
    text: str = "",
) -> RustCompletionSucceededEvent:
    parts: list[Any] = (
        [
            RustCompletionResultToolCallPart(
                id=call_id, name=tool, arguments_json=arguments
            )
        ]
        if tool is not None
        else [RustTextContentBlock(text=text)]
    )
    return RustCompletionSucceededEvent(
        action_id=action.action_id,
        result=RustCompletionResult(
            parts=parts,
            finish_reason="tool_call" if tool is not None else "stop",
            usage=USAGE,
        ),
    )


@pytest.mark.asyncio
async def test_a_subagent_approval_open_before_the_stream_follows_it_is_sent_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path whose root spawns a
        subagent. The stream's adoption of the subagent is held until the
        subagent's approval is open, so the adapter has no state for it yet.
    *Do*: Read the open callbacks a reconnecting client is sent, then read
        them again while the subagent cannot be read.
    *Assert*: The subagent's approval is listed, with its permissions, as the
        local runtime lists it; an unreadable subagent is left out, and the
        read still succeeds.
    """
    # Prepare
    monkeypatch.setattr(_local_actions, "execute_completion", _RootSpawnsAskingChild())
    release = asyncio.Event()
    registered = SessionApiPath._subagent_registered

    async def held(self: SessionApiPath, streams: Any, child_id: str) -> Any:
        await release.wait()
        return await registered(self, streams, child_id)

    monkeypatch.setattr(SessionApiPath, "_subagent_registered", held)
    options = SessionOptions(cwd=str(tmp_path), auto_approve=False)
    async with (
        api_adapter(
            tmp_path, options=options, process_services=process_services
        ) as harness,
        forwarding(harness),
    ):
        adapter = harness.adapter
        await adapter.start_turn(
            TurnStartParams(
                session_id=adapter.session_id, message=[TextContentBlock(text="Go")]
            )
        )

        def from_local_runtime() -> list[PublicCallbackEntry]:
            return [
                callback
                for callback in adapter.open_callbacks()
                if callback.session_id != adapter.session_id
            ]

        await until(lambda: bool(from_local_runtime()))
        [expected] = from_local_runtime()

        read_page = SessionApiPath._read_page

        async def subagent_gone(
            self: SessionApiPath, session_id: str, limit: int
        ) -> Any:
            if session_id == expected.session_id:
                raise RuntimeError("The subagent stopped")
            return await read_page(self, session_id, limit)

        # Do
        try:
            sent = await adapter.read_open_callbacks()
            monkeypatch.setattr(SessionApiPath, "_read_page", subagent_gone)
            without_subagent = await adapter.read_open_callbacks()
        finally:
            release.set()

    # Assert
    unlinked: Any = {"created_at": True, "updated_at": True}
    assert [callback.model_dump(exclude=unlinked) for callback in sent] == [
        expected.model_dump(exclude=unlinked)
    ]
    assert without_subagent == []


@pytest.mark.asyncio
async def test_a_subagent_approval_open_before_the_stream_follows_it_is_sent_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path whose root spawns a
        subagent. The stream's adoption of the subagent is held until the
        subagent's approval is open, so its stream never brings it.
    *Do*: Let the stream adopt the subagent, and let it run on.
    *Assert*: The connected client is sent the subagent's approval once, with
        its permissions, without a reconnect.
    """
    # Prepare
    monkeypatch.setattr(_local_actions, "execute_completion", _RootSpawnsAskingChild())
    release = asyncio.Event()
    registered = SessionApiPath._subagent_registered

    async def held(self: SessionApiPath, streams: Any, child_id: str) -> Any:
        await release.wait()
        return await registered(self, streams, child_id)

    monkeypatch.setattr(SessionApiPath, "_subagent_registered", held)
    options = SessionOptions(cwd=str(tmp_path), auto_approve=False)
    async with (
        api_adapter(
            tmp_path, options=options, process_services=process_services
        ) as harness,
        forwarding(harness) as forwarded,
    ):
        adapter = harness.adapter
        await adapter.start_turn(
            TurnStartParams(
                session_id=adapter.session_id, message=[TextContentBlock(text="Go")]
            )
        )

        def from_local_runtime() -> list[PublicCallbackEntry]:
            return [
                callback
                for callback in adapter.open_callbacks()
                if callback.session_id != adapter.session_id
            ]

        def requested() -> list[PublicCallbackEntry]:
            return [
                item.event.callback
                for item in forwarded
                if isinstance(item.event, CallbackRequested)
            ]

        await until(lambda: bool(from_local_runtime()))
        [expected] = from_local_runtime()

        # Do
        release.set()
        await until(lambda: bool(requested()))
        # A second copy would follow on the stream's next batches.
        await asyncio.sleep(0.2)
        sent = requested()

    # Assert
    [live] = sent
    assert (live.callback_id, live.session_id, live.turn_id) == (
        expected.callback_id,
        expected.session_id,
        expected.turn_id,
    )
    assert isinstance(live.state, OpenCallbackState)
    assert isinstance(live.detail, ApprovalCallbackDetail)
    assert isinstance(expected.detail, ApprovalCallbackDetail)
    assert live.detail.required_permissions == expected.detail.required_permissions


@pytest.mark.asyncio
async def test_an_approval_the_stream_has_not_brought_is_sent_live_not_on_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path whose stream is held
        while its Turn opens an approval.
    *Do*: Read the open callbacks a reconnecting client is sent, then release
        the stream.
    *Assert*: The read leaves the approval out, as the stream has not brought
        it; once released, the stream sends it to the client once.
    """
    # Prepare
    release_model = asyncio.Event()

    async def execute_completion(
        action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        await release_model.wait()
        return _completion(action, text="done")

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)
    release_stream = asyncio.Event()
    batch_events = SessionApiPath._batch_events

    async def held(self: SessionApiPath, streams: Any, session_batch: Any) -> Any:
        await release_stream.wait()
        async for backend_event in batch_events(self, streams, session_batch):
            yield backend_event

    async with (
        api_adapter(tmp_path, process_services=process_services) as harness,
        forwarding(harness) as forwarded,
    ):
        adapter = harness.adapter
        await adapter.start_turn(
            TurnStartParams(
                session_id=adapter.session_id, message=[TextContentBlock(text="Go")]
            )
        )
        session = adapter._session
        turn_id = session.active_turn_id
        assert turn_id is not None
        monkeypatch.setattr(SessionApiPath, "_batch_events", held)
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

        def requested() -> list[str]:
            return [
                item.event.callback.callback_id
                for item in forwarded
                if isinstance(item.event, CallbackRequested)
            ]

        try:
            await until(lambda: bool(session.open_callbacks()))
            [opened] = session.open_callbacks()

            # Do
            on_reconnect = await adapter.read_open_callbacks()
            release_stream.set()
            await until(lambda: bool(requested()))
            await asyncio.sleep(0.2)
            sent = requested()
        finally:
            release_stream.set()
            approval.cancel()
            release_model.set()

    # Assert
    assert on_reconnect == []
    assert sent == [opened["callbackId"]]


@pytest.mark.asyncio
async def test_open_callbacks_read_without_a_stream_come_from_a_read_of_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path whose stream nobody
        follows, and an approval its Turn opens.
    *Do*: Read the open callbacks, then read them again while the root
        Session cannot be read.
    *Assert*: The approval is listed from a read of the root; when the root
        cannot be read, the read fails rather than report no callbacks.
    """
    # Prepare
    release = asyncio.Event()

    async def execute_completion(
        action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        await release.wait()
        return _completion(action, text="done")

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        assert not adapter._events_subscribed
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
        await until(lambda: bool(session.open_callbacks()))
        [opened] = session.open_callbacks()

        async def root_gone(self: SessionApiPath, session_id: str, limit: int) -> Any:
            raise RuntimeError("The root cannot be read")

        # Do
        try:
            [listed] = await adapter.read_open_callbacks()
            monkeypatch.setattr(SessionApiPath, "_read_page", root_gone)
            with pytest.raises(RuntimeError, match="The root cannot be read"):
                await adapter.read_open_callbacks()
        finally:
            approval.cancel()
            release.set()

    # Assert
    assert listed.callback_id == opened["callbackId"]
    assert isinstance(listed.detail, ApprovalCallbackDetail)


@pytest.mark.asyncio
async def test_a_read_gives_an_open_approval_the_time_its_live_event_gave_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter on the Session API path, a Turn that waits on
        its model, and an approval the Turn opens later than it started.
    *Do*: Read the Session, as a client resyncing does.
    *Assert*: The read's open approval has the times the live event gave it,
        not the Turn's start, so a resync holds what the stream built.
    """
    # Prepare
    release = asyncio.Event()

    async def execute_completion(
        action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        await release.wait()
        return _completion(action, text="done")

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)
    async with (
        api_adapter(tmp_path, process_services=process_services) as harness,
        forwarding(harness) as forwarded,
    ):
        adapter = harness.adapter
        await adapter.start_turn(
            TurnStartParams(
                session_id=adapter.session_id, message=[TextContentBlock(text="Go")]
            )
        )
        session = adapter._session
        turn_id = session.active_turn_id
        assert turn_id is not None
        await asyncio.sleep(0.05)
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
        await until(
            lambda: any(isinstance(item.event, CallbackRequested) for item in forwarded)
        )
        [delivered] = [
            item.event.callback
            for item in forwarded
            if isinstance(item.event, CallbackRequested)
        ]

        # Do
        try:
            read = await adapter.read(SessionReadParams(session_id=adapter.session_id))
        finally:
            approval.cancel()
            release.set()

    # Assert
    [read_approval] = read.state.active_callbacks
    turn = read.state.latest_turn
    assert turn is not None
    assert delivered.created_at > turn.started_at
    assert (read_approval.created_at, read_approval.updated_at) == (
        delivered.created_at,
        delivered.updated_at,
    )


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
