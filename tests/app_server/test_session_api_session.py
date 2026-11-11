"""One Session's state kept from Session API reads and stream batches."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

import pytest

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
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
from vibe.app_server._session_api_session import ApiSessionState
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "skip_reason", "reject_reason"),
    [("skipped", "policy says no", None), ("rejected", None, "policy says no")],
)
async def test_a_hook_ended_turn_keeps_its_reason_in_the_latest_turn(
    tmp_path: Path,
    stop_reason: Literal["skipped", "rejected"],
    skip_reason: str | None,
    reject_reason: str | None,
) -> None:
    """*Prepare*: A Session read through the Session API.
    *Do*: Apply a batch where a hook skips or rejects a Turn, with its reason.
    *Assert*: The latest Turn keeps the reason as ``skipReason`` or
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
                        stop_reason=stop_reason, output="policy says no"
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
