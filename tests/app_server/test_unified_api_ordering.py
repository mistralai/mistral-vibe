"""How reads, ``flush_events`` and compaction replies order against the Session API stream."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionSucceededEvent,
    RustTextContentBlock,
    RustTokenUsage,
)
from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    PageRequest as ApiPageRequest,
    ToolResultHistoryEntry,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    EventsReadParams,
    SessionReadParams as ApiSessionReadParams,
)
from mistralai_vibe_local_harness.vibe import _local_actions
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    NOTICE,
    TURN_QUEUE_UPDATED,
    VibeExtensionEvent,
)
from tests.app_server._session_api_harness import (
    ApiAdapter,
    api_adapter,
    api_transport,
    forwarding,
    until,
    use_model_calling_one_tool,
)
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_backend_port import SessionBackendEvent
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedHarnessBackendAdapter,
)
from vibe.app_server.events import (
    ServerWarning,
    SessionUpdated,
    TurnCompleted,
    TurnQueueUpdated,
    TurnStarted,
)
from vibe.app_server.models import (
    PublicCheckpointEntry,
    PublicEffectEntry,
    SessionTextContentBlock,
    TextContentBlock,
    TurnUserInputEntry,
)
from vibe.app_server.protocol import (
    AgentSwitchParams,
    PageRequest,
    SessionCompactParams,
    SessionReadParams,
    SessionTitleUpdateParams,
    TurnEnqueueParams,
    TurnStartParams,
)

USAGE = RustTokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


@pytest.fixture
def model_gate(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """A model that answers every call, agent or compaction, once the gate is open."""
    gate = asyncio.Event()
    gate.set()

    async def execute_completion(
        action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        await gate.wait()
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=[RustTextContentBlock(text="answer")],
                finish_reason="stop",
                usage=USAGE,
            ),
        )

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)
    return gate


def _of_type[EventT](
    events: list[SessionBackendEvent], event_type: type[EventT]
) -> list[EventT]:
    return [e.event for e in events if isinstance(e.event, event_type)]


async def _answer_one_turn(
    adapter: UnifiedHarnessBackendAdapter, forwarded: list[SessionBackendEvent]
) -> None:
    """Run one Turn to its end, so the Session has a Runtime that can compact."""
    await adapter.start_turn(
        TurnStartParams(
            session_id=adapter.session_id, message=[TextContentBlock(text="Hi")]
        )
    )
    await until(lambda: bool(_of_type(forwarded, TurnCompleted)))
    await adapter.flush_events()


async def _rename(harness: ApiAdapter, title: str) -> None:
    await harness.host.rename(
        SessionTitleUpdateParams(session_id=harness.adapter.session_id, title=title)
    )


async def _last_api_event_id(adapter: UnifiedHarnessBackendAdapter) -> str:
    """The Session API's last event ID, once every Runtime update is in its log."""
    _, extensions = await api_transport(adapter).read_with_extensions(
        ApiSessionReadParams(session_id=adapter.session_id)
    )
    return extensions.last_event_id


async def _batches_until(
    adapter: UnifiedHarnessBackendAdapter, after_event_id: str, last_event_id: str
) -> list[list[tuple[type, str | None]]]:
    """The stream batches after ``after_event_id`` up to the one holding ``last_event_id``.

    Each batch is listed as its items' kinds: the item class and its
    ``vibe.*`` type, if any.
    """
    stream = api_transport(adapter).read_stream(
        EventsReadParams(session_id=adapter.session_id, after_event_id=after_event_id)
    )
    batches: list[list[tuple[type, str | None]]] = []
    try:
        async with asyncio.timeout(5):
            async for batch in stream:
                batches.append([
                    (type(item), getattr(item, "type", None)) for item in batch.events
                ])
                if any(item.event_id == last_event_id for item in batch.events):
                    return batches
    finally:
        await stream.aclose()  # type: ignore[attr-defined]
    raise AssertionError(f"the stream ended before {last_event_id}")


@pytest.mark.asyncio
async def test_a_read_replies_with_the_cursor_of_the_batch_holding_its_bookmark(
    tmp_path: Path, model_gate: asyncio.Event, process_services: ProcessServices
) -> None:
    """*Prepare*: A subscribed adapter whose stream is paused right after it
        yielded the client event of a first rename's batch, before marking
        that batch published.
    *Do*: Read, rename again, then let the stream publish the second batch
        before the read resumes.
    *Assert*: The read waited for its own batch and replies with the cursor
        that batch ended on and the state of that moment, not the later
        cursor of the second rename.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        first_pending = asyncio.ensure_future(anext(harness.events))
        await until(lambda: adapter._events_subscribed)
        await _rename(harness, "first")
        first = await asyncio.wait_for(first_pending, timeout=5)

        # Do
        reading = asyncio.create_task(
            adapter.read(SessionReadParams(session_id=adapter.session_id))
        )
        api_path = adapter._api_path
        assert api_path is not None
        await until(lambda: bool(api_path._pending_reads))
        waited = not reading.done()
        await _rename(harness, "second")
        await _last_api_event_id(adapter)
        second = await asyncio.wait_for(anext(harness.events), timeout=5)
        response = await asyncio.wait_for(reading, timeout=5)

        # Assert
        assert waited
        assert isinstance(first.event, SessionUpdated)
        assert isinstance(second.event, SessionUpdated)
        assert first.event_id is not None and second.event_id is not None
        assert second.event_id > first.event_id
        assert adapter._event_id == second.event_id
        assert response.state.session.title == "first"
        assert response.last_event_id == first.event_id
        assert response.state.event_id == first.event_id
        assert not api_path._pending_reads


@pytest.mark.asyncio
async def test_an_agent_switch_reaches_the_client_with_the_agent_it_picked(
    tmp_path: Path, model_gate: asyncio.Event, process_services: ProcessServices
) -> None:
    """*Prepare*: A subscribed adapter whose events are forwarded, after one Turn.
    *Do*: Switch the agent twice, flushing events after each switch.
    *Assert*: After each switch, the last ``SessionUpdated`` carries the agent
        just picked, not the one picked before it.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        async with forwarding(harness) as forwarded:
            await _answer_one_turn(adapter, forwarded)
            for agent_name in ("auto-approve", "accept-edits"):
                # Do
                await adapter.switch_agent(
                    AgentSwitchParams(
                        session_id=adapter.session_id, agent_name=agent_name
                    )
                )
                await adapter.flush_events()

                # Assert
                updates = _of_type(forwarded, SessionUpdated)
                assert updates[-1].session.agent is not None
                assert updates[-1].session.agent.name == agent_name


@pytest.mark.asyncio
async def test_flush_events_returns_before_anyone_consumes_the_stream(
    tmp_path: Path, model_gate: asyncio.Event, process_services: ProcessServices
) -> None:
    """*Prepare*: A subscribed adapter whose events nobody iterates yet, and a
        Runtime update the stream has not published.
    *Do*: Flush events.
    *Assert*: The flush returns at once instead of waiting for a stream that
        has not started.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        await _rename(harness, "unpublished")
        last_event_id = await _last_api_event_id(adapter)
        assert adapter._api_path is not None
        streams = adapter._api_path._streams
        assert streams is not None
        assert not streams.is_observed(adapter.session_id, last_event_id)

        # Do
        await asyncio.wait_for(adapter.flush_events(), timeout=0.5)

        # Assert
        assert not adapter._events_subscribed


async def _publish_notice(
    harness: ApiAdapter, forwarded: list[SessionBackendEvent]
) -> None:
    """A Runtime notice: its batch holds one ``vibe.notice`` and no API event."""
    del forwarded
    harness.adapter._session.publish_notice("a plugin source is gone")


async def _enqueue_behind_a_running_turn(
    harness: ApiAdapter, forwarded: list[SessionBackendEvent]
) -> None:
    """A queue change: its batch holds one ``vibe.turn_queue_updated``."""
    adapter = harness.adapter
    await adapter.start_turn(
        TurnStartParams(
            session_id=adapter.session_id, message=[TextContentBlock(text="Hi")]
        )
    )
    await until(lambda: bool(_of_type(forwarded, TurnStarted)))
    await adapter.flush_events()
    enqueued = await adapter.enqueue_turn(
        TurnEnqueueParams(
            session_id=adapter.session_id,
            entries=[
                TurnUserInputEntry(content=[SessionTextContentBlock(text="queued")])
            ],
        )
    )
    assert enqueued.after_response is not None
    enqueued.after_response()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("publish", "item_type", "client_event"),
    [
        pytest.param(_publish_notice, NOTICE, ServerWarning, id="notice"),
        pytest.param(
            _enqueue_behind_a_running_turn,
            TURN_QUEUE_UPDATED,
            TurnQueueUpdated,
            id="queue",
        ),
    ],
)
async def test_flush_events_returns_after_a_batch_of_only_vibe_items(
    tmp_path: Path,
    model_gate: asyncio.Event,
    publish: Callable[[ApiAdapter, list[SessionBackendEvent]], Awaitable[None]],
    item_type: str,
    client_event: type,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A subscribed adapter whose events are forwarded, with the model
        held so a running Turn stays running.
    *Do*: Cause a Runtime update whose batch holds only one ``vibe.*`` item
        (a notice, or a queue change), then flush events.
    *Assert*: The batch counts as published: the flush returns, after the
        item's client event went out.
    """
    # Prepare
    model_gate.clear()
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        try:
            async with forwarding(harness) as forwarded:
                await adapter.flush_events()
                before = await _last_api_event_id(adapter)

                # Do
                await publish(harness, forwarded)
                await asyncio.wait_for(adapter.flush_events(), timeout=2)

                # Assert
                last_event_id = await _last_api_event_id(adapter)
                batches = await _batches_until(adapter, before, last_event_id)
                assert batches[-1] == [(VibeExtensionEvent, item_type)]
                assert len(_of_type(forwarded, client_event)) == 1
        finally:
            model_gate.set()


@pytest.mark.asyncio
async def test_a_stale_compaction_callback_cannot_release_a_newer_compaction(
    tmp_path: Path, model_gate: asyncio.Event, process_services: ProcessServices
) -> None:
    """*Prepare*: A forwarded adapter with one answered Turn, a first compaction
        already replied to, and a second compaction whose reply is pending.
    *Do*: Repeat both callbacks of the first compaction.
    *Assert*: The second compaction's updates stay held until its own reply
        is sent.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        params = SessionCompactParams(session_id=adapter.session_id)
        async with forwarding(harness) as forwarded:
            await _answer_one_turn(adapter, forwarded)
            first = await adapter.compact(params)
            assert first.after_response is not None
            assert first.on_response_abandoned is not None
            first.after_response()
            await adapter.flush_events()
            second = await adapter.compact(params)
            assert second.after_response is not None
            delivered_before_release = len(forwarded)

            # Do
            first.after_response()
            first.on_response_abandoned()
            await asyncio.wait_for(adapter.flush_events(), timeout=1)

            # Assert
            assert adapter._api_path is not None
            assert not adapter._api_path._events_released.is_set()
            assert len(forwarded) == delivered_before_release
            second.after_response()
            await asyncio.wait_for(adapter.flush_events(), timeout=2)
            assert len(forwarded) > delivered_before_release


@pytest.mark.asyncio
async def test_a_read_whose_page_edge_splits_a_tool_still_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A Session whose Turn called one tool, then answered.
    *Do*: Read it with a page as long as the Session API entries from the
        tool's result to the end.
    *Assert*: The tool is in the reply: the page edge fell between the tool's
        call and its result, and the read took the call too.
    """
    # Prepare
    use_model_calling_one_tool(monkeypatch)
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        async with forwarding(harness) as forwarded:
            await _answer_one_turn(adapter, forwarded)
        entries = await _api_history(adapter)
        [result] = [
            index
            for index, entry in enumerate(entries)
            if isinstance(entry, ToolResultHistoryEntry)
        ]
        limit = len(entries) - result

        # Do
        response = await adapter.read(
            SessionReadParams(
                session_id=adapter.session_id, history=PageRequest(limit=limit)
            )
        )

    # Assert
    history = response.state.history or []
    assert any(isinstance(entry, PublicEffectEntry) for entry in history)


@pytest.mark.asyncio
async def test_a_split_tool_read_keeps_the_call_when_the_session_moves_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A Session whose Turn called one tool, then answered, and an
        entry the Session adds just after the read's first page.
    *Do*: Read it with a page edge between the tool's call and its result.
    *Assert*: The tool is in the reply: the read took the entry just before
        its own page edge, not one more of the latest entries.
    """
    # Prepare
    use_model_calling_one_tool(monkeypatch)
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        async with forwarding(harness) as forwarded:
            await _answer_one_turn(adapter, forwarded)
        entries = await _api_history(adapter)
        [result] = [
            index
            for index, entry in enumerate(entries)
            if isinstance(entry, ToolResultHistoryEntry)
        ]
        limit = len(entries) - result
        transport = api_transport(adapter)
        read_with_extensions = transport.read_with_extensions
        runtime = adapter._session._runtime
        assert runtime is not None
        appended = False

        async def read_then_append(params: Any) -> Any:
            nonlocal appended
            read = await read_with_extensions(params)
            if not appended:
                appended = True
                await runtime.append_public_history_entries([
                    _message("message-after-read")
                ])
            return read

        monkeypatch.setattr(transport, "read_with_extensions", read_then_append)

        # Do
        response = await adapter.read(
            SessionReadParams(
                session_id=adapter.session_id, history=PageRequest(limit=limit)
            )
        )

    # Assert
    assert appended
    history = response.state.history or []
    assert any(isinstance(entry, PublicEffectEntry) for entry in history)


def _message(entry_id: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": entry_id,
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "completed",
        "role": "assistant",
        "content": [{"type": "text", "text": "Added after the read"}],
    }


async def _api_history(adapter: UnifiedHarnessBackendAdapter) -> list[Any]:
    """Every Session API history entry of the Session, oldest first."""
    read, _ = await api_transport(adapter).read_with_extensions(
        ApiSessionReadParams(
            session_id=adapter.session_id,
            history=ApiPageRequest(limit=1_000, direction="forward"),
        )
    )
    assert read.state.history is not None
    return list(read.state.history.items)


@pytest.mark.asyncio
async def test_a_compaction_after_the_stream_ended_replies_with_its_checkpoint(
    tmp_path: Path, model_gate: asyncio.Event, process_services: ProcessServices
) -> None:
    """*Prepare*: An adapter with one answered Turn whose event stream ended,
        so it keeps the state it translated last.
    *Do*: Compact.
    *Assert*: The reply's history ends with the compaction's checkpoint: the
        reply is read again, since no stream moved the kept state on.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        async with forwarding(harness) as forwarded:
            await _answer_one_turn(adapter, forwarded)
        await until(lambda: not adapter._events_subscribed)
        assert adapter._translated_state is not None

        # Do
        result = await adapter.compact(
            SessionCompactParams(session_id=adapter.session_id)
        )

    # Assert
    history = result.response.state.history or []
    assert history
    assert isinstance(history[-1], PublicCheckpointEntry)
    assert history[-1].kind == "compaction"
