"""Subagent updates, retries and Smart Approve telemetry as the root's client sees them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio

from mistralai_vibe_local_harness.protocol import (
    RustCompletionFinishReason,
    RustCompletionResult,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustTextContentBlock,
    RustTokenUsage,
)
from mistralai_vibe_local_harness.vibe import _local_actions
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    RETRYING_UPDATED,
    TOOL_CLASSIFICATION,
)
from tests.app_server._session_api_harness import (
    ApiAdapter,
    StreamBatches,
    api_adapter,
    forwarding,
    publish,
)
from tests.stubs.subagent_programs import spawn_only
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_backend_port import SessionBackendEvent
from vibe.app_server.events import (
    ChildSessionUpdated,
    SessionSnapshot,
    TurnCompleted,
    TurnRetrying,
)
from vibe.app_server.models import (
    IdleSessionStatus,
    PublicRetryCategory,
    TextContentBlock,
)
from vibe.app_server.protocol import (
    PageRequest,
    SessionOptions,
    SessionReadParams,
    TurnStartParams,
)

USAGE = RustTokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


class _RootSpawnsHeldChild:
    """The root spawns subagent "researcher", which answers only once released."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.root_completions = 0

    async def __call__(
        self, action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        if action.turn_id is not None and action.turn_id.startswith("child-"):
            await self.release.wait()
            return _answer(action, [RustTextContentBlock(text="child done")])
        self.root_completions += 1
        if self.root_completions > 1:
            return _answer(action, [RustTextContentBlock(text="done")])
        spawn = RustCompletionResultToolCallPart(
            id="program-1",
            name="run_typescript",
            arguments_json=json.dumps({"code": spawn_only("researcher", "Look")}),
        )
        return _answer(action, [spawn], finish_reason="tool_call")


def _answer(
    action: Any, parts: list[Any], *, finish_reason: RustCompletionFinishReason = "stop"
) -> RustCompletionSucceededEvent:
    return RustCompletionSucceededEvent(
        action_id=action.action_id,
        result=RustCompletionResult(
            parts=parts, finish_reason=finish_reason, usage=USAGE
        ),
    )


@pytest_asyncio.fixture
async def root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> AsyncIterator[ApiAdapter]:
    """A root on the Session API path whose model spawns one held subagent."""
    model = _RootSpawnsHeldChild()
    monkeypatch.setattr(_local_actions, "execute_completion", model)
    options = SessionOptions(cwd=str(tmp_path), auto_approve=True)
    try:
        async with api_adapter(
            tmp_path, options=options, process_services=process_services
        ) as harness:
            yield harness
    finally:
        model.release.set()


@dataclass
class _Spawned:
    child_id: str
    updates: list[SessionBackendEvent]


async def _spawn_child(root: ApiAdapter) -> _Spawned:
    """Start the root Turn that spawns the subagent, and read the root's events
    until a ``ChildSessionUpdated`` shows the subagent running.
    """
    await root.adapter.start_turn(
        TurnStartParams(
            session_id=root.adapter.session_id,
            message=[TextContentBlock(text="Delegate")],
        )
    )
    updates: list[SessionBackendEvent] = []
    async with asyncio.timeout(10):
        async for backend_event in root.events:
            event = backend_event.event
            if not isinstance(event, ChildSessionUpdated):
                continue
            updates.append(backend_event)
            if event.child_session.status.type == "running":
                return _Spawned(event.child_session.id, updates)
    raise AssertionError("The root's event stream ended before the subagent ran")


async def _until_turn_completed(root: ApiAdapter) -> None:
    async with asyncio.timeout(10):
        async for backend_event in root.events:
            if isinstance(backend_event.event, TurnCompleted):
                return
    raise AssertionError("The root's event stream ended before its Turn completed")


@pytest.mark.asyncio
async def test_a_subscription_replaces_a_seeded_subagent_with_its_live_state(
    root: ApiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A root whose subagent runs after the root's Turn ended, seeded
        again from its persisted child record with a stale idle state.
    *Do*: Subscribe to the root, which opens the subagent's stream.
    *Assert*: The subagent's state is its live, running state, not the seeded one.
    """
    # Prepare
    adapter = root.adapter
    child_id = (await _spawn_child(root)).child_id
    await _until_turn_completed(root)
    await cast(Any, root.events).aclose()
    del adapter._child_states[child_id]
    read_child = adapter._read_child_response

    async def stale_child_read(params: Any, **kwargs: Any) -> Any:
        response = await read_child(params, **kwargs)
        session = response.state.session.model_copy(
            update={"status": IdleSessionStatus(), "updated_at": 1}
        )
        return response.model_copy(
            update={"state": response.state.model_copy(update={"session": session})}
        )

    with monkeypatch.context() as patched:
        patched.setattr(adapter, "_read_child_response", stale_child_read)
        await adapter.seed_persisted_child_states()
    assert adapter._child_states[child_id].session.updated_at == 1

    # Do
    subscription = await adapter.subscribe(
        SessionReadParams(session_id=adapter.session_id, history=PageRequest(limit=100))
    )
    await cast(Any, subscription.events).aclose()

    # Assert
    live = adapter._child_states[child_id].session
    assert live.status.type == "running"
    assert live.updated_at > 1
    [listed] = subscription.snapshot.state.child_sessions
    assert listed.status.type == "running"


@pytest.mark.asyncio
async def test_a_switch_turned_off_mid_session_leaves_its_subagents_on_the_api(
    root: ApiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A root opened with the Session API switch on, then the
        switch turned off and the configuration pushed into the root again,
        as a rollback of the experiment does.
    *Do*: Start the root Turn that spawns a subagent.
    *Assert*: The subagent runs and the root follows it, because the root
        keeps the path it opened with and its subagents inherit it.
    """
    # Prepare
    adapter = root.adapter
    monkeypatch.setenv("VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API", "false")
    await adapter._context.config_orchestrator.reload()
    assert not adapter._context.config_orchestrator.config.experimental_local_runtime_session_api
    await adapter._apply_derivation()

    # Do
    child_id = (await _spawn_child(root)).child_id

    # Assert
    running = adapter._session.adapter_config
    assert running is not None
    assert running.session_api
    assert adapter._child_states[child_id].session.status.type == "running"


@pytest.mark.asyncio
async def test_a_subagent_retry_notifies_under_its_own_id_and_clearing_it_is_silent(
    root: ApiAdapter,
) -> None:
    """*Prepare*: A root whose subagent runs and is followed on its own stream.
    *Do*: Publish a subagent batch that starts a provider retry, then one that
        clears it.
    *Assert*: The start sends one ``turn/retrying`` under the subagent's ID and
        no snapshot; the clear sends nothing and leaves no retry in its state.
    """
    # Prepare
    adapter = root.adapter
    child_id = (await _spawn_child(root)).child_id
    batches = StreamBatches(child_id)
    retry = {
        "turnId": "child-turn-1",
        "category": "rate_limited",
        "detail": "HTTP 429",
        "retryAt": 1,
        "retryAttempt": 1,
    }

    # Do
    started = await publish(
        adapter,
        batches.batch(extensions=[(RETRYING_UPDATED, {"retrying": retry})]),
        session_id=child_id,
    )
    cleared = await publish(
        adapter,
        batches.batch(extensions=[(RETRYING_UPDATED, {"retrying": None})]),
        session_id=child_id,
    )

    # Assert
    notices = [event for event in started if event.method == "turn/retrying"]
    assert len(notices) == 1
    assert isinstance(notices[0].event, TurnRetrying)
    assert notices[0].session_id == child_id
    assert notices[0].event.params.category is PublicRetryCategory.RATE_LIMITED
    assert notices[0].event_id is None
    assert not any(isinstance(event.event, SessionSnapshot) for event in started)
    assert cleared == []
    assert adapter._child_states[child_id].retrying is None


@pytest.mark.asyncio
async def test_a_subagent_classification_reaches_telemetry_not_the_client(
    root: ApiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A root whose subagent runs, a recorder for telemetry events,
        and the root as a client reads it.
    *Do*: Publish a subagent batch holding only a ``vibe.tool_classification``.
    *Assert*: One ``vibe.tool_classification`` telemetry event is sent, and the
        client sees nothing change: the batch publishes no event, a new read
        of the root and its subagent list is the same, and the next root
        update has nothing to publish either.
    """
    # Prepare
    adapter = root.adapter
    child_id = (await _spawn_child(root)).child_id
    sent: list[tuple[str, dict[str, object]]] = []

    def record(name: str, properties: dict[str, object], **_: object) -> None:
        sent.append((name, properties))

    monkeypatch.setattr(adapter._telemetry, "send_telemetry_event", record)
    read_root = SessionReadParams(
        session_id=adapter.session_id, history=PageRequest(limit=100)
    )
    classification: dict[str, Any] = {
        "type": "tool_classification",
        "tool_name": "file_system.bash",
        "verdict": "allow",
        "tier": "fast",
        "reason": "read-only",
        "outcome": "auto_approved",
        "escalated": False,
        "latency_ms": 5.0,
        "classifier_model": "mistral-small-latest",
        "turn_id": "t1",
        "call_id": "c1",
    }

    # A read waits until the stream published its bookmark, so the stream must
    # be consumed while the test reads.
    async with forwarding(root):
        before = (await adapter.read(read_root)).state

        # Do
        events = await publish(
            adapter,
            StreamBatches(child_id).batch(
                extensions=[(TOOL_CLASSIFICATION, classification)]
            ),
            session_id=child_id,
        )
        after = (await adapter.read(read_root)).state
        # The adapter publishes a root update as the difference from the last
        # state it sent, so a state the classification changed would show here.
        next_root_update = await publish(
            adapter, StreamBatches(adapter.session_id).batch()
        )

    # Assert
    assert events == []
    assert len(sent) == 1
    assert sent[0][0] == "vibe.tool_classification"
    assert sent[0][1]["tool_name"] == "file_system.bash"
    assert after.model_dump(exclude={"event_id"}) == before.model_dump(
        exclude={"event_id"}
    )
    assert [listed.id for listed in after.child_sessions] == [child_id]
    assert next_root_update == []
