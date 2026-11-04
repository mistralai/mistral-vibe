"""Telemetry, stats, notices and streamed history on the adapter's Session API path."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    CheckpointHistoryEntry,
    HistoryEntryAddedEvent,
    HistoryEntryCompletedEvent,
    HistoryEntryPatchedEvent,
    JsonPatchAppendOperation,
    MessageHistoryEntry,
    ToolCallHistoryEntry,
    ToolResultHistoryEntry,
    TurnStartedEvent,
    TurnState,
)
from mistralai_vibe_local_harness.vibe._runtime_config import RequestSentTelemetry
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    NOTICE,
    TOOL_CLASSIFICATION,
    USAGE_UPDATED,
)
from tests.app_server._session_api_harness import (
    StreamBatches,
    api_adapter,
    api_host,
    api_transport,
    publish,
)
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedHarnessBackendAdapter,
)
from vibe.app_server.events import HistoryEntryUpdated, ServerWarning
from vibe.app_server.protocol import (
    AgentStatsSnapshot,
    PageRequest,
    SessionOptions,
    SessionReadParams,
    SessionStartParams,
)


def _message(
    entry_id: str, index: int, role: str, text: str, *, generating: bool = False
) -> MessageHistoryEntry:
    return MessageHistoryEntry.model_validate({
        "id": entry_id,
        "index": index,
        "turn_id": "turn-root",
        "generation_status": "generating" if generating else "complete",
        "created_at": 1,
        "updated_at": 1,
        "role": role,
        "content": [{"type": "text", "text": text}],
    })


def _subagent_wait(state: dict[str, JsonValue], *, finished: bool) -> list[Any]:
    """The API entries of a ``subagent.wait`` effect, call then result."""
    detail: dict[str, JsonValue] = {
        "kind": "tool",
        "toolName": "subagent.wait",
        "input": {
            "agentName": "secret-agent-name",
            "timeoutMs": 10,
            "prompt": "secret prompt",
        },
        "display": {"summary": "Waiting for secret-agent-name", "statusText": "Wait"},
    }
    output = state.pop("output", None)
    common: dict[str, Any] = {
        "turn_id": "turn-root",
        "created_at": 1,
        "updated_at": 2 if finished else 1,
        "tool_call_id": "call-wait",
    }
    return [
        ToolCallHistoryEntry(
            id="effect-wait-1:call",
            index=0,
            generation_status="complete",
            tool_kind="local",
            name="subagent.wait",
            arguments=detail["input"],
            **common,
        ),
        ToolResultHistoryEntry(
            id="effect-wait-1:result",
            index=1,
            generation_status="complete" if finished else "generating",
            name="subagent.wait",
            result=output,
            annotations={
                "vibe.effect": {
                    "detail": detail,
                    "state": state,
                    "title": "subagent.wait",
                }
            },
            **common,
        ),
    ]


def _automatic_compaction() -> CheckpointHistoryEntry:
    return CheckpointHistoryEntry(
        id="checkpoint-compaction-auto",
        index=0,
        turn_id="turn-root",
        generation_status="complete",
        created_at=1,
        updated_at=2,
        checkpoint_kind="compaction",
        message="Context compacted",
        details={"trigger": "automatic", "attempt": 1, "summaryLength": 10},
    )


def _usage(
    token: tuple[int, int] | None, context: tuple[int, int] | None, *, cached: int = 0
) -> dict[str, JsonValue]:
    def usage(tokens: tuple[int, int] | None, cached: int) -> JsonValue:
        if tokens is None:
            return None
        return {
            "inputTokens": tokens[0],
            "outputTokens": tokens[1],
            "totalTokens": sum(tokens),
            "cachedInputTokens": cached,
        }

    return {"tokenUsage": usage(token, cached), "contextUsage": usage(context, 0)}


@asynccontextmanager
async def _subscribed_with_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    usage: dict[str, JsonValue],
    process_services: ProcessServices,
) -> AsyncIterator[UnifiedHarnessBackendAdapter]:
    """A real adapter whose subscription read reports ``usage``, as a resume does.

    The shared harness subscribes as soon as it starts the Session, so this
    starts it itself and patches the root's ``read_with_extensions`` first.
    """
    host = await api_host(tmp_path, process_services=process_services)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    adapter = cast(UnifiedHarnessBackendAdapter, started.backend)
    transport = api_transport(adapter)
    read_with_extensions = transport.read_with_extensions

    async def read_with_usage(params: Any) -> Any:
        read, extensions = await read_with_extensions(params)
        if params.session_id != adapter.session_id:
            return read, extensions
        stored = {**extensions.extensions, USAGE_UPDATED: usage}
        return read, extensions.model_copy(update={"extensions": stored})

    monkeypatch.setattr(transport, "read_with_extensions", read_with_usage)
    subscription = await adapter.subscribe(
        SessionReadParams(session_id=adapter.session_id, history=PageRequest(limit=50))
    )
    try:
        yield adapter
    finally:
        await cast(Any, subscription.events).aclose()
        await host.shutdown()


def _stats(adapter: UnifiedHarnessBackendAdapter) -> AgentStatsSnapshot:
    return adapter.runtime_updated_params().runtime.stats


@pytest.mark.asyncio
async def test_streamed_text_sends_only_the_changed_entry_as_an_append(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter whose Session holds a question and a started answer.
    *Do*: Publish a batch that appends text to the answer.
    *Assert*: Only the answer is sent to the client, as an append.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        batches = StreamBatches(adapter.session_id)
        question = _message("question", 0, "user", "Stream please")
        started = _message("answer", 1, "assistant", "Hello", generating=True)
        await publish(
            adapter,
            batches.batch(
                HistoryEntryAddedEvent(entry=question),
                HistoryEntryAddedEvent(entry=started),
            ),
        )

        # Do
        events = await publish(
            adapter,
            batches.batch(
                HistoryEntryPatchedEvent(
                    entry_id="answer",
                    entry_index=1,
                    patches=[
                        JsonPatchAppendOperation(path="/content/0/text", value=" there")
                    ],
                )
            ),
        )

        # Assert
        updates = [
            event.event
            for event in events
            if isinstance(event.event, HistoryEntryUpdated)
        ]
        assert [update.entry.id for update in updates] == ["answer"]
        assert [
            (op.op, op.path, op.value)
            for op in updates[0].patch
            if op.path == "/content/0/text"
        ] == [("append", "/content/0/text", " there")]


@pytest.mark.asyncio
async def test_a_terminal_subagent_wait_emits_one_content_free_event(
    tmp_path: Path,
    telemetry_events: list[dict[str, Any]],
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A real adapter whose Session holds a running ``subagent.wait``.
    *Do*: Publish the wait's timed-out completion twice.
    *Assert*: One ``vibe.tool_call_finished`` with only the approved
        dimensions, and none of the agent name, prompt or error detail.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        batches = StreamBatches(adapter.session_id)
        running = _subagent_wait(
            {"status": "running", "outputText": ""}, finished=False
        )
        await publish(
            adapter, batches.batch(*(HistoryEntryAddedEvent(entry=e) for e in running))
        )
        _, timed_out = _subagent_wait(
            {
                "status": "completed",
                "outputText": "",
                "durationMs": 1,
                "display": {"success": True, "message": "Wait completed"},
                "output": {
                    "type": "success",
                    "content": [],
                    "structured_content": {
                        "type": "error",
                        "error": "subagent_wait_timeout: secret timeout detail",
                    },
                },
            },
            finished=True,
        )

        # Do
        for _ in range(2):
            await publish(
                adapter, batches.batch(HistoryEntryCompletedEvent(entry=timed_out))
            )

        # Assert
        finished = [
            event
            for event in telemetry_events
            if event["event_name"] == "vibe.tool_call_finished"
        ]
        assert len(finished) == 1
        properties = finished[0]["properties"]
        assert properties["harness_backend"] == "unified"
        assert properties["tool_name"] == "subagent.wait"
        assert properties["status"] == "failure"
        assert properties["subagent_operation"] == "wait"
        assert properties["subagent_outcome"] == "timeout"
        assert properties["subagent_depth"] == 1
        serialized = repr(properties)
        assert "secret-agent-name" not in serialized
        assert "secret prompt" not in serialized
        assert "secret timeout detail" not in serialized


@pytest.mark.asyncio
async def test_model_calls_count_only_the_sessions_own_agent_calls(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """*Prepare*: A real adapter whose Session is in ``turn-root``, and the
        runtime reporting two agent calls for that Turn, one for a subagent's
        Turn and one compaction.
    *Do*: Publish the Session's next batch.
    *Assert*: The stats count the Session's own two agent calls only.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        batches = StreamBatches(adapter.session_id)
        await publish(
            adapter,
            batches.batch(
                TurnStartedEvent(
                    turn=TurnState(id="turn-root", status="running", started_at=1)
                )
            ),
        )
        for purpose, turn_id in [
            ("agent", "turn-root"),
            ("agent", "child-turn"),
            ("compaction", "turn-root"),
            ("agent", "turn-root"),
        ]:
            adapter._context.request_sent.record(
                RequestSentTelemetry(
                    model="m1",
                    purpose=cast(Any, purpose),
                    iteration=0,
                    nb_context_chars=1,
                    nb_context_messages=1,
                    nb_prompt_chars=1,
                    turn_id=turn_id,
                )
            )

        # Do
        await publish(
            adapter, batches.batch(extensions=[(USAGE_UPDATED, _usage((10, 5), None))])
        )

        # Assert
        assert _stats(adapter).model_calls == 2


@pytest.mark.asyncio
async def test_automatic_compaction_reports_the_context_before_it(
    tmp_path: Path,
    telemetry_events: list[dict[str, Any]],
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A real adapter whose Session measured a 4321-token context.
    *Do*: Publish a usage update with no context reading, then an automatic
        compaction whose batch resets the context reading.
    *Assert*: The gauge keeps 4321 across the first batch, and
        ``auto_compact_triggered`` reports it as the size before compaction.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        batches = StreamBatches(adapter.session_id)
        await publish(
            adapter,
            batches.batch(
                extensions=[(USAGE_UPDATED, _usage((5000, 400), (4000, 321)))]
            ),
        )

        # Do
        await publish(
            adapter,
            batches.batch(extensions=[(USAGE_UPDATED, _usage((5000, 400), None))]),
        )
        kept = adapter._context_tokens_before
        await publish(
            adapter,
            batches.batch(
                HistoryEntryAddedEvent(entry=_automatic_compaction()),
                extensions=[(USAGE_UPDATED, _usage((5100, 410), (90, 10)))],
            ),
        )

        # Assert
        assert kept == 4321
        triggered = [
            event
            for event in telemetry_events
            if event["event_name"] == "vibe.auto_compact_triggered"
        ]
        assert len(triggered) == 1
        assert triggered[0]["properties"]["nb_context_tokens_before"] == 4321


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("classification", "expected", "absent"),
    [
        pytest.param(
            {
                "verdict": "prompt",
                "outcome": "escalated_denied",
                "risk_level": None,
                "user_authorization": None,
                "error_type": "ClassifierTimeout",
            },
            {
                "verdict": "prompt",
                "tool_name": "file_system.bash",
                "outcome": "escalated_denied",
                "escalated": True,
                "error_type": "ClassifierTimeout",
            },
            ["risk_level", "user_authorization", "approval_grant"],
            id="drops-null-fields",
        ),
        pytest.param(
            {"outcome": "escalated_approved", "approval_grant": "approve_for_session"},
            {"outcome": "escalated_approved", "approval_grant": "approve_for_session"},
            [],
            id="forwards-the-approval-grant",
        ),
    ],
)
async def test_a_classification_goes_to_telemetry_not_the_ui(
    tmp_path: Path,
    telemetry_events: list[dict[str, Any]],
    classification: dict[str, JsonValue],
    expected: dict[str, object],
    absent: list[str],
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A real adapter and a Smart Approve classification, as the
        runtime emits it.
    *Do*: Publish a batch holding only its ``vibe.tool_classification`` item.
    *Assert*: One ``vibe.tool_classification`` telemetry event with its
        fields, null fields dropped, and no client event.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        data: dict[str, JsonValue] = {
            "type": "tool_classification",
            "tool_name": "file_system.bash",
            "verdict": "prompt",
            "tier": "fast",
            "reason": "risky",
            "escalated": True,
            "latency_ms": 12.0,
            "classifier_model": "mistral-small-latest",
            "turn_id": "t1",
            "call_id": "c1",
            **classification,
        }

        # Do
        events = await publish(
            adapter,
            StreamBatches(adapter.session_id).batch(
                extensions=[(TOOL_CLASSIFICATION, data)]
            ),
        )

        # Assert
        assert events == []
        sent = [
            event
            for event in telemetry_events
            if event["event_name"] == "vibe.tool_classification"
        ]
        assert len(sent) == 1
        properties = sent[0]["properties"]
        assert {key: properties.get(key) for key in expected} == expected
        assert [key for key in absent if key in properties] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("notice", "code"),
    [
        pytest.param(
            {"level": "warning", "code": "request_too_long"},
            "request_too_long",
            id="core-code",
        ),
        pytest.param({"level": "warning"}, "warning", id="level-without-code"),
    ],
)
async def test_a_notice_shows_as_a_warning_with_its_core_code(
    tmp_path: Path,
    notice: dict[str, JsonValue],
    code: str,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A real adapter and a runtime notice, with a Core code or without.
    *Do*: Publish a batch holding its ``vibe.notice`` item.
    *Assert*: The client gets one warning with the notice's message, carrying
        the Core code, else the level.
    """
    # Prepare
    async with api_adapter(tmp_path, process_services=process_services) as harness:
        adapter = harness.adapter
        message = "The request was too long; compacting and retrying."

        # Do
        events = await publish(
            adapter,
            StreamBatches(adapter.session_id).batch(
                extensions=[(NOTICE, {**notice, "message": message})]
            ),
        )

        # Assert
        assert [event.method for event in events] == ["warning"]
        warning = events[0].event
        assert isinstance(warning, ServerWarning)
        assert warning.params.warning.code == code
        assert warning.params.warning.message == message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        pytest.param(
            _usage((4000, 900), None, cached=3000),
            {
                "session_prompt_tokens": 4000,
                "session_completion_tokens": 900,
                "session_cached_tokens": 3000,
                "last_turn_total_tokens": 0,
                "context_tokens": 0,
            },
            id="no-context-reading",
        ),
        pytest.param(
            _usage((303_000, 75), (102_000, 25)),
            {"session_prompt_tokens": 303_000, "context_tokens": 102_025},
            id="measured-context",
        ),
    ],
)
async def test_stats_seed_from_a_resumed_sessions_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    usage: dict[str, JsonValue],
    expected: dict[str, int],
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A Session whose read reports running token totals, with or
        without the context its last call measured, subscribed through the
        Session API.
    *Do*: Read its stats.
    *Assert*: The stats start from those totals and are priced; the context
        gauge starts at the measured context, else at zero.
    """
    # Prepare
    async with _subscribed_with_usage(
        tmp_path, monkeypatch, usage, process_services
    ) as adapter:
        # Do
        stats = _stats(adapter)

    # Assert
    assert {key: getattr(stats, key) for key in expected} == expected
    assert stats.input_price_per_million > 0
