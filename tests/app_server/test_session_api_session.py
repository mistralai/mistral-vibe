"""One Session's state kept from Session API reads and stream batches."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    CancelledTermination,
    CompletedTermination,
    PageRequest,
    TextContentBlock,
    TurnStartedEvent,
    TurnState,
    TurnTerminatedEvent,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    EventsReadParams,
    SessionReadParams,
    TurnStartParams,
    TurnStartResponse,
)
from mistralai_vibe_local_harness.session_protocol import (
    CompletedPublicTurn,
    SessionReadParams as RuntimeReadParams,
    SessionStartParams,
)
from mistralai_vibe_local_harness.vibe import UnifiedHarnessSessionBackendHost
from mistralai_vibe_local_harness.vibe._host import _core_config
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig
from mistralai_vibe_local_harness.vibe._session_api_transport import (
    LocalSessionApiTransport,
    session_api_view_of,
)
from tests.app_server._session_api_harness import (
    StreamBatches,
    use_model_calling_one_tool,
)
from vibe.app_server._session_api_session import ApiSessionState, final_answer
from vibe.app_server._unified_history_projection import project_raw_history_entry

_ALL = PageRequest(limit=1_000, direction="forward")


@pytest.fixture
def model_using_one_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    use_model_calling_one_tool(monkeypatch)


@pytest.mark.asyncio
async def test_batches_give_the_state_the_runtime_publishes(
    tmp_path: Path, model_using_one_tool: None
) -> None:
    """*Prepare*: A Session read through the Session API before any Turn.
    *Do*: Run a Turn that calls a tool and answers, applying each stream
        batch to the state until the Turn ends.
    *Assert*: The Session, usage, latest Turn, queue and history equal the
        Runtime's own public state, projected the same way.
    """
    # Prepare
    host = UnifiedHarnessSessionBackendHost(tmp_path)
    host.configure_runtime(
        _core_config("runtime-template"),
        LocalRuntimeAdapterConfig.at(cwd=tmp_path, session_api=True),
    )
    session = await host.start(SessionStartParams(history_limit=50))
    transport = LocalSessionApiTransport(session, session_api_view_of(session))
    await transport.open()
    read, extensions = await transport.read_with_extensions(
        SessionReadParams(session_id=session.session_id, history=_ALL)
    )
    state = ApiSessionState(session.session_id, (tmp_path,))
    state.reset(read, extensions)

    # Do
    await transport.request(
        "app_server/session/turn/start",
        TurnStartParams(
            session_id=session.session_id, message=[TextContentBlock(text="Read a")]
        ),
        TurnStartResponse,
    )
    stream = transport.read_stream(
        EventsReadParams(
            session_id=session.session_id, after_event_id=read.last_event_id
        )
    )
    async with asyncio.timeout(5):
        async for batch in stream:
            state.apply(batch)
            if any(
                isinstance(getattr(item, "payload", None), TurnTerminatedEvent)
                for item in batch.events
            ):
                break
    await stream.aclose()  # type: ignore[attr-defined]
    await session._wait_for_pending_turns()
    runtime = (
        await session.read(
            RuntimeReadParams(session_id=session.session_id, history_limit=50)
        )
    ).snapshot.state

    # Assert
    rebuilt = state.runtime_state()
    assert rebuilt.session == runtime.session
    assert rebuilt.latest_turn == runtime.latest_turn
    assert rebuilt.turn_queue == runtime.turn_queue
    assert rebuilt.active_callbacks == runtime.active_callbacks
    assert state.history == [
        entry
        for raw in runtime.history.entries
        for entry in project_raw_history_entry(raw, (tmp_path,))
    ]
    await transport.close()
    await host.shutdown()


_POLICY_REASON_BLOCKS: JsonValue = [
    {"type": "text", "text": "policy "},
    {"type": "image", "data": "aGk=", "mimeType": "image/png"},
    {"type": "text", "text": "says no"},
]
"""A reason as the target runtime reports it: content blocks, not a string."""


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["policy says no", _POLICY_REASON_BLOCKS])
@pytest.mark.parametrize(
    ("stop_reason", "skip_reason", "reject_reason"),
    [("skipped", "policy says no", None), ("rejected", None, "policy says no")],
)
async def test_a_hook_ended_turn_keeps_its_reason_in_the_latest_turn(
    tmp_path: Path,
    output: JsonValue,
    stop_reason: Literal["skipped", "rejected"],
    skip_reason: str | None,
    reject_reason: str | None,
) -> None:
    """*Prepare*: A Session read through the Session API.
    *Do*: Apply a batch where a hook skips or rejects a Turn, with its reason
        as the local runtime (a string) or the target runtime (blocks) gives it.
    *Assert*: The latest Turn keeps the reason's text as ``skipReason`` or
        ``rejectReason``, as the Runtime's own public Turn does.
    """
    # Prepare
    host = UnifiedHarnessSessionBackendHost(tmp_path)
    host.configure_runtime(
        _core_config("runtime-template"),
        LocalRuntimeAdapterConfig.at(cwd=tmp_path, session_api=True),
    )
    session = await host.start(SessionStartParams(history_limit=50))
    transport = LocalSessionApiTransport(session, session_api_view_of(session))
    await transport.open()
    read, extensions = await transport.read_with_extensions(
        SessionReadParams(session_id=session.session_id, history=_ALL)
    )
    state = ApiSessionState(session.session_id, (tmp_path,))
    state.reset(read, extensions)
    batches = StreamBatches(session.session_id)

    # Do
    state.apply(
        batches.batch(
            TurnStartedEvent(
                turn=TurnState(id="turn-1", status="running", started_at=1)
            ),
            TurnTerminatedEvent(
                turn=TurnState(
                    id="turn-1",
                    status="terminated",
                    started_at=1,
                    completed_at=2,
                    termination=CompletedTermination(
                        stop_reason=stop_reason, output=output
                    ),
                )
            ),
        )
    )

    # Assert
    turn = state.runtime_state().latest_turn
    assert isinstance(turn, CompletedPublicTurn)
    assert (turn.skip_reason, turn.reject_reason) == (skip_reason, reject_reason)
    await transport.close()
    await host.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "public_stop_reason"),
    [("iteration_limit", "limit"), ("output_limit", "length")],
)
async def test_a_turn_ended_at_a_limit_keeps_the_public_stop_reason(
    tmp_path: Path,
    stop_reason: Literal["iteration_limit", "output_limit"],
    public_stop_reason: str,
) -> None:
    """*Prepare*: A Session read through the Session API.
    *Do*: Apply a batch where a Turn ends at its iteration limit or at the output
        limit, with no Vibe turn details.
    *Assert*: The latest Turn's stop reason is ``limit`` or ``length``, as the
        Runtime's own public Turn says.
    """
    # Prepare
    host = UnifiedHarnessSessionBackendHost(tmp_path)
    host.configure_runtime(
        _core_config("runtime-template"),
        LocalRuntimeAdapterConfig.at(cwd=tmp_path, session_api=True),
    )
    session = await host.start(SessionStartParams(history_limit=50))
    transport = LocalSessionApiTransport(session, session_api_view_of(session))
    await transport.open()
    read, extensions = await transport.read_with_extensions(
        SessionReadParams(session_id=session.session_id, history=_ALL)
    )
    state = ApiSessionState(session.session_id, (tmp_path,))
    state.reset(read, extensions)
    batches = StreamBatches(session.session_id)

    # Do
    state.apply(
        batches.batch(
            TurnStartedEvent(
                turn=TurnState(id="turn-1", status="running", started_at=1)
            ),
            TurnTerminatedEvent(
                turn=TurnState(
                    id="turn-1",
                    status="terminated",
                    started_at=1,
                    completed_at=2,
                    termination=CompletedTermination(
                        stop_reason=stop_reason, output="The rep"
                    ),
                )
            ),
        )
    )

    # Assert
    turn = state.runtime_state().latest_turn
    assert isinstance(turn, CompletedPublicTurn)
    assert turn.stop_reason == public_stop_reason
    await transport.close()
    await host.shutdown()


def _completed(output: JsonValue, stop_reason: str | None = None) -> TurnState:
    return TurnState(
        id="turn-1",
        status="terminated",
        started_at=1,
        completed_at=2,
        termination=CompletedTermination.model_validate({
            "output": output,
            "stopReason": stop_reason,
        }),
    )


@pytest.mark.parametrize(
    ("output", "stop_reason"),
    [
        (
            [
                {"type": "text", "text": "First part."},
                {"type": "image", "data": "aGk=", "mimeType": "image/png"},
                {"type": "text", "text": "Second part."},
            ],
            None,
        ),
        ([{"type": "text", "text": "First part.\n\nSecond part."}], "output_limit"),
        ("First part.\n\nSecond part.", "iteration_limit"),
    ],
)
def test_a_completed_turn_answers_with_the_text_of_its_output(
    output: JsonValue, stop_reason: str | None
) -> None:
    """*Prepare*: A Turn that completed with an answer, as content blocks or a
        string, ended normally or at a limit.
    *Do*: Take its final answer.
    *Assert*: It is the output's texts joined by a blank line, images left out.
    """
    assert final_answer(_completed(output, stop_reason)) == (
        "First part.\n\nSecond part."
    )


@pytest.mark.parametrize(
    "turn",
    [
        _completed([{"type": "text", "text": "No."}], "skipped"),
        _completed([{"type": "text", "text": "No."}], "rejected"),
        _completed([{"type": "text", "text": "Stopped."}], "repeated_tool_failure"),
        _completed([{"type": "text", "text": "Over budget."}], "budget_exhausted"),
        _completed([]),
        _completed([{"type": "text", "text": "  "}]),
        _completed([{"type": "image", "data": "aGk=", "mimeType": "image/png"}]),
        _completed(None),
        TurnState(
            id="turn-1",
            status="terminated",
            termination=CancelledTermination(
                output=[{"type": "text", "text": "partial"}]
            ),
        ),
        TurnState(id="turn-1", status="running"),
    ],
)
def test_a_turn_without_an_answer_of_its_own_has_no_final_answer(
    turn: TurnState,
) -> None:
    """*Prepare*: A Turn a hook or Core ended with a stated reason, one ended
        for a reason a newer runtime added, one whose answer has no text, one
        that was interrupted, and one still running.
    *Do*: Take its final answer.
    *Assert*: It has none, so a client falls back to what it did before.
    """
    assert final_answer(turn) is None
