from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
import json
from pathlib import Path
import time
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import (
    attach_test_app_server_session,
    build_test_app_server,
    create_test_app_server_session,
    start_test_app_server,
)
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server._model import validate_wire
from vibe.app_server._runtime import (
    AgentRuntimeFactory,
    HarnessProcess,
    RootOpenRequest,
)
from vibe.app_server.client import AppServerClient, AppServerConnectionClosed
from vibe.app_server.events import (
    CallbackRequested,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    SessionCompacted,
    SessionContextCleared,
    SessionSnapshot,
    StatsUpdated,
    TurnCompleted,
    TurnStarted,
)
from vibe.app_server.models import (
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    CancelledCallbackState,
    CompletedEffectState,
    FileReadEffectDetail,
    PublicCallbackEntry,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
    ResourceContentBlock,
    ScheduledLoopFiredNoticeDetail,
    UserAnswer,
    UserInputCallbackOutput,
    UserQuestionResult,
)
from vibe.app_server.protocol import (
    SERVER_METHODS,
    AppServerResponseError,
    CallbackCallResponse,
    ClientCapabilities,
    ClientInfo,
    ConfigPatchOpWire,
    ConfigPatchParams,
    Notification,
    ProtocolErrorCode,
    RuntimeUpdatedParams,
    ServerRequest,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionReadyWaitParams,
    SessionResumeParams,
    SessionStartParams,
    SessionUpdatedParams,
    validate_callback_acknowledgement,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair
from vibe.core.agent_loop import AgentLoop
from vibe.core.compaction import CompactionFailedError
from vibe.core.config import SessionLoggingConfig
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.tools.models import ToolPermission
from vibe.core.types import (
    AgentStats,
    AssistantEvent,
    CompactEndEvent,
    CompactStartEvent,
    ContextClearedEvent,
    FunctionCall,
    LLMMessage,
    Role,
    ScheduledLoop,
    ToolCall,
    UserMessageEvent,
)
from vibe.user_content import UserResourceLink


@pytest.mark.asyncio
async def test_explicit_compaction_failure_is_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)

    async def fail_compact(_instructions: str = "") -> str:
        raise CompactionFailedError("tool_call")

    monkeypatch.setattr(agent_loop, "compact", fail_compact)
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await session.compact()
    finally:
        await session.close()
        await agent_loop.aclose()

    assert exc_info.value.error.code is ProtocolErrorCode.COMPACTION_FAILED
    assert exc_info.value.error.data == {"reason": "tool_call"}


@pytest.mark.asyncio
async def test_slow_unsolicited_event_consumer_applies_bounded_backpressure() -> None:
    session = await create_test_app_server_session(build_test_agent_loop())
    queue = session._unsolicited_events
    event = SessionSnapshot(session.state)
    try:
        for _ in range(queue.maxsize):
            queue.put_nowait(event)

        blocked = asyncio.create_task(session._publish_event(event))
        await asyncio.sleep(0)
        assert not blocked.done()

        assert queue.get_nowait() is event
        await asyncio.wait_for(blocked, timeout=1)
        assert queue.qsize() == queue.maxsize
    finally:
        while not queue.empty():
            queue.get_nowait()
        await session.close()


@pytest.mark.asyncio
async def test_session_close_does_not_wait_for_full_event_queues() -> None:
    session = await create_test_app_server_session(build_test_agent_loop())
    event = SessionSnapshot(session.state)
    for queue in (session._events, session._unsolicited_events):
        for _ in range(queue.maxsize):
            queue.put_nowait(event)

    await asyncio.wait_for(session.close(), timeout=1)

    assert session._events.qsize() == 1
    assert session._unsolicited_events.qsize() == 1


@pytest.mark.asyncio
async def test_turn_streams_public_events_over_json_rpc() -> None:
    backend = FakeBackend([mock_llm_chunk(content="hell"), mock_llm_chunk(content="o")])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    session = await create_test_app_server_session(agent_loop)

    try:
        events = [event async for event in session.act("hi", client_message_id="u1")]
    finally:
        await session.close()
        await agent_loop.aclose()

    assert any(isinstance(event, StatsUpdated) for event in events)
    user = next(
        event.entry
        for event in events
        if isinstance(event, HistoryEntryAdded)
        and isinstance(event.entry, PublicMessageEntry)
        and event.entry.role == "user"
    )
    assert user.id == "u1"
    assert user.text == "hi"
    assistant = next(
        entry
        for entry in session.history
        if isinstance(entry, PublicMessageEntry) and entry.role == "assistant"
    )
    assert assistant.text == "hello"
    assert all(entry.generation_status == "completed" for entry in session.history)


@pytest.mark.asyncio
async def test_turn_preserves_resource_blocks_and_normalizes_model_input() -> None:
    backend = FakeBackend([mock_llm_chunk(content="done")])
    agent_loop = build_test_agent_loop(backend=backend)
    session = await create_test_app_server_session(agent_loop)
    resource = UserResourceLink(
        uri="file:///workspace/spec.md",
        media_type="text/markdown",
        title="Specification",
    )

    try:
        await _consume(session.act("Review this", resources=[resource]))
    finally:
        await session.close()
        await agent_loop.aclose()

    request_user = next(
        message for message in backend.requests_messages[0] if message.role is Role.user
    )
    assert request_user.content is not None
    assert "Review this" in request_user.content
    assert "uri: file:///workspace/spec.md" in request_user.content
    public_user = next(
        entry
        for entry in session.history
        if isinstance(entry, PublicMessageEntry) and entry.role == "user"
    )
    assert public_user.text == "Review this"
    assert any(
        isinstance(block, ResourceContentBlock)
        and block.resource.uri == "file:///workspace/spec.md"
        for block in public_user.content
    )


@pytest.mark.asyncio
async def test_prepared_image_crosses_turn_start_boundary() -> None:
    config = build_test_vibe_config()
    config.get_active_model().supports_images = True
    backend = FakeBackend([mock_llm_chunk(content="done")])
    agent_loop = build_test_agent_loop(config=config, backend=backend)
    session = await create_test_app_server_session(agent_loop)
    Path("shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    try:
        prepared = await session.resources.workspace.prepare_prompt("look @shot.png")
        await _consume(
            session.act(prepared.prompt_text, images=prepared.images or None)
        )
    finally:
        await session.close()
        await agent_loop.aclose()

    assert prepared.images[0].alias == "shot.png"
    request_user = next(
        message for message in backend.requests_messages[0] if message.role is Role.user
    )
    assert request_user.images is not None
    assert request_user.images[0].alias == "shot.png"


@pytest.mark.asyncio
async def test_at_file_mention_projects_without_presentation_snapshot() -> None:
    Path("notes.md").write_text("hello world", encoding="utf-8")
    backend = FakeBackend([mock_llm_chunk(content="done")])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    session = await create_test_app_server_session(agent_loop)

    try:
        await _consume(session.act("read @notes.md what does this do"))
    finally:
        await session.close()
        await agent_loop.aclose()

    read_effect = next(
        entry
        for entry in session.history
        if isinstance(entry, PublicEffectEntry)
        and isinstance(entry.detail, FileReadEffectDetail)
    )
    assert isinstance(read_effect.state, CompletedEffectState)

    injected_call = next(
        tool_call
        for message in agent_loop.messages
        for tool_call in message.tool_calls or []
        if tool_call.function.name == "read_file"
    )
    assert injected_call.presentation is not None


@pytest.mark.asyncio
async def test_completed_turn_refreshes_runtime_projection() -> None:
    backend = FakeBackend([mock_llm_chunk(content="done")])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    session = await create_test_app_server_session(agent_loop)
    await agent_loop.config_orchestrator.set_field(
        "/theme", "server-updated-theme", target_layer=OverridesLayer.NAME
    )
    agent_loop.agent_manager.invalidate_config()

    try:
        _ = [event async for event in session.act("refresh runtime")]
    finally:
        await session.close()
        await agent_loop.aclose()

    assert session.resources.config.current.theme == "server-updated-theme"


@pytest.mark.asyncio
async def test_session_start_initializes_experiments_server_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_loop = build_test_agent_loop()
    initialize = Mock()
    monkeypatch.setattr(agent_loop, "start_initialize_experiments", initialize)

    session = await create_test_app_server_session(agent_loop)
    try:
        initialize.assert_called_once_with()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_persisted_session_resume_appends_checkpoint(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    session_id = saved.session_id
    await saved.aclose()

    session = await attach_test_app_server_session(
        start_test_app_server(build_test_agent_loop(config=config)),
        resume_session_id=session_id,
    )
    try:
        checkpoints = [
            entry
            for entry in session.history
            if isinstance(entry, PublicCheckpointEntry) and entry.kind == "resume"
        ]
    finally:
        await session.close()

    assert len(checkpoints) == 1
    assert checkpoints[0].message == "Session resumed"


@pytest.mark.asyncio
async def test_in_process_resume_rebases_exit_usage(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    saved.stats.session_prompt_tokens = 11
    saved.stats.session_completion_tokens = 7
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    session = await create_test_app_server_session(build_test_agent_loop(config=config))
    try:
        await session.resume(saved_session_id)
        summary = session.exit_summary()
    finally:
        await session.close()

    assert summary.usage.input_tokens == 0
    assert summary.usage.output_tokens == 0


@pytest.mark.asyncio
async def test_attached_fork_rebases_exit_usage(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(config=config)
    agent_loop.stats.session_prompt_tokens = 11
    agent_loop.stats.session_completion_tokens = 7
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.resources.sessions.fork(attach=True)
        await session.resources.refresh()
        summary = session.exit_summary()
    finally:
        await session.close()

    assert summary.usage.input_tokens == 0
    assert summary.usage.output_tokens == 0


@pytest.mark.asyncio
async def test_detached_fork_transfers_live_runtime_without_session_logging() -> None:
    process = HarnessProcess()
    source_loop = build_test_agent_loop()

    async def open_source(_request: RootOpenRequest) -> AgentLoop:
        return source_loop

    source_client_transport, source_server_transport = memory_transport_pair()
    source_server = AppServer(
        source_server_transport,
        open_root=open_source,
        runtime_factory=process.runtime_factory,
        stage_root=process.stage_root,
    )
    source = await AppServerSession.start(
        AppServerClient(source_client_transport, run_peer=source_server.serve),
        client_info=ClientInfo(name="fork-test", version="1"),
        capabilities=ClientCapabilities(),
    )
    child: AppServerSession | None = None
    try:
        await source.resources.agents.switch("plan")
        await source.resources.sessions.update_settings(max_turns=7, max_tokens=4096)
        fork = await source.resources.sessions.fork(attach=False)

        child_client_transport, child_server_transport = memory_transport_pair()
        child_server = AppServer(
            child_server_transport,
            open_root=process.open_root,
            runtime_factory=process.runtime_factory,
            stage_root=process.stage_root,
        )
        child = await AppServerSession.start(
            AppServerClient(child_client_transport, run_peer=child_server.serve),
            client_info=ClientInfo(name="fork-test", version="1"),
            capabilities=ClientCapabilities(),
            resume_session_id=fork.state.session.id,
        )

        assert child.session_id == fork.state.session.id
        assert child.resources.agents.active.name == "plan"
        assert child_server._agent_loop.runtime_policy.max_turns == 7
        assert child_server._agent_loop.runtime_policy.max_tokens == 4096
    finally:
        if child is not None:
            await child.close()
        await source.close()
        await process.close()


@pytest.mark.asyncio
async def test_harness_process_closes_unclaimed_fork_runtime() -> None:
    process = HarnessProcess()
    staged = build_test_agent_loop()
    close = AsyncMock(wraps=staged.aclose)
    staged.aclose = close

    await process.stage_root(staged)
    await process.close()

    close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_detached_fork_reserves_source_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = HarnessProcess()
    source_loop = build_test_agent_loop()
    entered = asyncio.Event()
    release = asyncio.Event()
    fork_runtime = process.runtime_factory.fork

    async def blocking_fork(source: AgentLoop, message_id: str | None) -> AgentLoop:
        entered.set()
        await release.wait()
        return await fork_runtime(source, message_id)

    monkeypatch.setattr(process.runtime_factory, "fork", blocking_fork)

    async def open_source(_request: RootOpenRequest) -> AgentLoop:
        return source_loop

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        open_root=open_source,
        runtime_factory=process.runtime_factory,
        stage_root=process.stage_root,
    )
    session = await AppServerSession.start(
        AppServerClient(client_transport, run_peer=server.serve),
        client_info=ClientInfo(name="fork-test", version="1"),
        capabilities=ClientCapabilities(),
    )
    fork = asyncio.create_task(session.resources.sessions.fork(attach=False))
    await entered.wait()
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await _consume(session.act("racing prompt"))
        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    finally:
        release.set()
        await fork
        await session.close()
        await process.close()


@pytest.mark.asyncio
async def test_due_loop_runs_as_an_unsolicited_server_turn(tmp_path: Path) -> None:
    backend = FakeBackend([mock_llm_chunk(content="scheduled response")])
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    metadata = agent_loop.session_logger.session_metadata
    assert metadata is not None
    before_fire = time.time()
    metadata.loops = [
        ScheduledLoop(
            id="scheduled-1",
            interval_seconds=30,
            prompt="scheduled prompt",
            next_fire_at=before_fire - 1,
            created_at=before_fire - 31,
        )
    ]
    session = await create_test_app_server_session(agent_loop)

    try:
        events = []
        async with asyncio.timeout(2):
            async for event in session.events():
                events.append(event)
                if isinstance(event, TurnCompleted):
                    break
    finally:
        await session.close()

    assert any(isinstance(event, TurnStarted) for event in events)
    assert any(
        isinstance(event, HistoryEntryAdded)
        and isinstance(event.entry, PublicMessageEntry)
        and event.entry.role == "user"
        and event.entry.text == "scheduled prompt"
        for event in events
    )
    entries = [event.entry for event in events if isinstance(event, HistoryEntryAdded)]
    notice = next(entry for entry in entries if isinstance(entry, PublicNoticeEntry))
    assert isinstance(notice.detail, ScheduledLoopFiredNoticeDetail)
    assert notice.detail.loop_id == "scheduled-1"
    assert notice.message == "Loop `scheduled-1` fired"
    user_index = next(
        index
        for index, entry in enumerate(entries)
        if isinstance(entry, PublicMessageEntry) and entry.role == "user"
    )
    assert entries.index(notice) == user_index + 1
    assert session.state.latest_turn is not None
    assert session.state.latest_turn.status == "completed"
    assert metadata.loops[0].next_fire_at >= before_fire + 30


@pytest.mark.asyncio
async def test_history_list_can_filter_by_turn() -> None:
    backend = FakeBackend([
        [mock_llm_chunk(content="first answer")],
        [mock_llm_chunk(content="second answer")],
    ])
    session = await create_test_app_server_session(
        build_test_agent_loop(backend=backend, enable_streaming=True)
    )
    try:
        await _consume(session.act("first question"))
        first_turn = session.state.latest_turn
        assert first_turn is not None

        await _consume(session.act("second question"))
        page = await session.resources.sessions.list_history(turn_id=first_turn.id)
    finally:
        await session.close()

    assert page.entries
    assert all(entry.turn_id == first_turn.id for entry in page.entries)
    assert not any(
        isinstance(entry, PublicMessageEntry) and entry.text == "second question"
        for entry in page.entries
    )


@pytest.mark.asyncio
async def test_headless_session_does_not_run_persisted_loops(tmp_path: Path) -> None:
    backend = FakeBackend([mock_llm_chunk(content="direct response")])
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    metadata = agent_loop.session_logger.session_metadata
    assert metadata is not None
    now = time.time()
    metadata.loops = [
        ScheduledLoop(
            id="scheduled-1",
            interval_seconds=30,
            prompt="scheduled prompt",
            next_fire_at=now - 1,
            created_at=now - 31,
        )
    ]
    session = await attach_test_app_server_session(
        start_test_app_server(agent_loop), session_options=SessionOptions(headless=True)
    )

    try:
        await asyncio.sleep(0.1)
        assert session.state.latest_turn is None
        await _consume(session.act("direct prompt"))
    finally:
        await session.close()

    assert all(
        not isinstance(entry, PublicMessageEntry) or entry.text != "scheduled prompt"
        for entry in session.history
    )


@pytest.mark.asyncio
async def test_concurrent_resumes_replace_roots_serially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = build_test_agent_loop(session_id="initial")
    first = build_test_agent_loop(session_id="first")
    second = build_test_agent_loop(session_id="second")
    factory = AgentRuntimeFactory()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    sources = []

    async def resume_root(source, session_id):
        sources.append(source)
        if session_id == "first":
            first_entered.set()
            await release_first.wait()
            return first
        return second

    monkeypatch.setattr(factory, "resume_root", resume_root)

    async def open_root(_request: RootOpenRequest):
        return initial

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root, runtime_factory=factory)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="resume-test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", SessionStartParams())

    first_resume = asyncio.create_task(
        client.request("session/resume", SessionResumeParams(session_id="first"))
    )
    await first_entered.wait()
    second_resume = asyncio.create_task(
        client.request("session/resume", SessionResumeParams(session_id="second"))
    )
    await asyncio.sleep(0)
    assert sources == [initial]
    release_first.set()
    try:
        await asyncio.gather(first_resume, second_resume)
        assert sources == [initial, first]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_resume_reserves_session_before_loading_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = build_test_agent_loop(session_id="initial")
    replacement = build_test_agent_loop(session_id="replacement")
    entered = asyncio.Event()
    release = asyncio.Event()
    factory = AgentRuntimeFactory()

    async def resume_root(_source, _session_id):
        entered.set()
        await release.wait()
        return replacement

    monkeypatch.setattr(factory, "resume_root", resume_root)

    async def open_root(_request: RootOpenRequest):
        return initial

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root, runtime_factory=factory)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="resume-test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", SessionStartParams())

    resume = asyncio.create_task(
        client.request(
            "session/resume", SessionResumeParams(session_id=replacement.session_id)
        )
    )
    await entered.wait()
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "turn/start",
                {
                    "sessionId": initial.session_id,
                    "input": [{"type": "text", "text": "racing prompt"}],
                },
            )
        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    finally:
        release.set()
        await resume
        await client.close()


@pytest.mark.asyncio
async def test_compaction_reserves_session_before_awaiting_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def compact(_instructions: str = "") -> str:
        entered.set()
        await release.wait()
        return "summary"

    monkeypatch.setattr(agent_loop, "compact", compact)
    compaction = asyncio.create_task(session.compact())
    await entered.wait()
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await _consume(session.act("racing prompt"))
        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    finally:
        release.set()
        await compaction
        await session.close()


@pytest.mark.asyncio
async def test_failed_root_close_discards_staged_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = build_test_agent_loop(session_id="initial")
    replacement = build_test_agent_loop(session_id="replacement")
    initial.aclose = AsyncMock(side_effect=RuntimeError("close failed"))
    replacement_close = AsyncMock(wraps=replacement.aclose)
    replacement.aclose = replacement_close
    factory = AgentRuntimeFactory()
    monkeypatch.setattr(factory, "resume_root", AsyncMock(return_value=replacement))

    async def open_root(_request: RootOpenRequest):
        return initial

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root, runtime_factory=factory)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="resume-test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", SessionStartParams())

    try:
        with pytest.raises(AppServerResponseError, match="close failed"):
            await client.request(
                "session/resume", SessionResumeParams(session_id="replacement")
            )
        replacement_close.assert_awaited_once_with()
        assert server._root is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_callback_round_trips_user_input() -> None:
    tool_call = ToolCall(
        id="question-1",
        index=0,
        function=FunctionCall(
            name="ask_user_question",
            arguments=json.dumps({
                "questions": [
                    {
                        "question": "Ship it?",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    }
                ]
            }),
        ),
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Done")],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    session = await create_test_app_server_session(agent_loop)
    callback: PublicCallbackEntry | None = None

    try:
        events = []
        async for event in session.act("ask me"):
            if isinstance(event, CallbackRequested):
                callback = event.callback
                await session.respond_to_callback(
                    event.callback.callback_id,
                    UserInputCallbackOutput(
                        result=UserQuestionResult(
                            answers=[UserAnswer(question="Ship it?", answer="Yes")]
                        )
                    ),
                )
            events.append(event)
    finally:
        await session.close()
        await agent_loop.aclose()

    assert any(isinstance(event, HistoryEntryUpdated) for event in events)
    assert callback is not None
    assert callback.related_entry_id == "question-1"
    effect = next(
        entry for entry in session.history if isinstance(entry, PublicEffectEntry)
    )
    assert isinstance(effect.state, CompletedEffectState)
    assert isinstance(effect.state.output, dict)
    assert effect.state.output["cancelled"] is False
    answers = effect.state.output["answers"]
    assert isinstance(answers, list)
    first_answer = answers[0]
    assert isinstance(first_answer, dict)
    assert first_answer["answer"] == "Yes"
    assert session.state.active_callbacks == []


@pytest.mark.asyncio
async def test_callback_round_trips_approval() -> None:
    tool_call = ToolCall(
        id="todo-1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Done")],
    ])
    config = build_test_vibe_config(
        enabled_tools=["todo"], tools={"todo": {"permission": ToolPermission.ASK.value}}
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    session = await create_test_app_server_session(agent_loop)

    try:
        async for event in session.act("read todos"):
            if isinstance(event, CallbackRequested):
                await session.respond_to_callback(
                    event.callback.callback_id,
                    ApprovalCallbackOutput(
                        decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
                    ),
                )
    finally:
        await session.close()
        await agent_loop.aclose()

    effect = next(
        entry for entry in session.history if isinstance(entry, PublicEffectEntry)
    )
    assert isinstance(effect.state, CompletedEffectState)


@pytest.mark.asyncio
async def test_agent_switches_during_approval_and_remains_selected() -> None:
    tool_call = ToolCall(
        id="todo-1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Done")],
    ])
    config = build_test_vibe_config(
        enabled_tools=["todo"], tools={"todo": {"permission": ToolPermission.ASK.value}}
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    session = await create_test_app_server_session(agent_loop)

    try:
        async for event in session.act("read todos"):
            if not isinstance(event, CallbackRequested):
                continue
            assert (await session.resources.agents.switch("plan")).name == "plan"
            assert (await session.resources.agents.switch("default")).name == "default"
            assert (await session.resources.agents.switch("plan")).name == "plan"
            await session.respond_to_callback(
                event.callback.callback_id,
                ApprovalCallbackOutput(
                    decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
                ),
            )
        assert session.resources.agents.active.name == "plan"
    finally:
        await session.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_reconnect_resumes_live_turn_and_redelivers_open_callback() -> None:
    tool_call = ToolCall(
        id="todo-1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Done")],
    ])
    config = build_test_vibe_config(
        enabled_tools=["todo"], tools={"todo": {"permission": ToolPermission.ASK.value}}
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    session = await _create_reconnectable_session(agent_loop)
    stream = session.act("read todos")

    try:
        first_callback = await _next_event(stream, CallbackRequested)
        disconnected_client = session._connection.current
        assert disconnected_client is not None
        await disconnected_client.close()

        snapshot = await _next_event(stream, SessionSnapshot)
        redelivered = await _next_event(stream, CallbackRequested)
        assert snapshot.state.session.id == session.session_id
        assert redelivered.callback.callback_id == first_callback.callback.callback_id
        assert session._connection.current is not disconnected_client

        await session.respond_to_callback(
            redelivered.callback.callback_id,
            ApprovalCallbackOutput(
                decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
            ),
        )
        _ = [event async for event in stream]
    finally:
        await stream.aclose()
        await session.close()

    effect = next(
        entry for entry in session.history if isinstance(entry, PublicEffectEntry)
    )
    assert isinstance(effect.state, CompletedEffectState)
    assert not any(
        isinstance(entry, PublicCheckpointEntry) and entry.kind == "resume"
        for entry in session.history
    )


@pytest.mark.asyncio
async def test_reconnect_replays_turn_output_emitted_while_detached() -> None:
    backend_started = asyncio.Event()
    release_backend = asyncio.Event()
    allow_reconnect = asyncio.Event()

    class GatedBackend(FakeBackend):
        async def complete_streaming(self, **kwargs):
            backend_started.set()
            await release_backend.wait()
            async for chunk in super().complete_streaming(**kwargs):
                yield chunk

    agent_loop = build_test_agent_loop(
        backend=GatedBackend([mock_llm_chunk(content="Recovered output")]),
        enable_streaming=True,
    )
    session = await _create_reconnectable_session(
        agent_loop, reconnect_gate=allow_reconnect
    )
    stream = session.act("finish while detached")
    consume = asyncio.create_task(_consume(stream))

    try:
        await asyncio.wait_for(backend_started.wait(), timeout=1)
        disconnected_client = session._connection.current
        assert disconnected_client is not None
        await disconnected_client.close()
        release_backend.set()
        await asyncio.sleep(0.05)
        allow_reconnect.set()
        events = await asyncio.wait_for(consume, timeout=1)
    finally:
        allow_reconnect.set()
        release_backend.set()
        if not consume.done():
            consume.cancel()
            with suppress(asyncio.CancelledError):
                await consume
        await stream.aclose()
        await session.close()

    assert any(isinstance(event, SessionSnapshot) for event in events)
    recovered = [
        event.entry
        for event in events
        if isinstance(event, HistoryEntryAdded)
        and isinstance(event.entry, PublicMessageEntry)
        and event.entry.role == "assistant"
    ]
    assert [entry.text for entry in recovered] == ["Recovered output"]


@pytest.mark.asyncio
async def test_callback_opened_while_detached_is_delivered_after_reconnect() -> None:
    backend_started = asyncio.Event()
    release_backend = asyncio.Event()
    allow_reconnect = asyncio.Event()
    tool_call = ToolCall(
        id="todo-1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )

    class GatedBackend(FakeBackend):
        async def complete_streaming(self, **kwargs):
            backend_started.set()
            await release_backend.wait()
            async for chunk in super().complete_streaming(**kwargs):
                yield chunk

    backend = GatedBackend([
        [mock_llm_chunk(content="", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Done")],
    ])
    config = build_test_vibe_config(
        enabled_tools=["todo"], tools={"todo": {"permission": ToolPermission.ASK.value}}
    )
    session = await _create_reconnectable_session(
        build_test_agent_loop(config=config, backend=backend, enable_streaming=True),
        reconnect_gate=allow_reconnect,
    )
    stream = session.act("open approval while detached")
    callback_task = asyncio.create_task(_next_event(stream, CallbackRequested))

    try:
        await asyncio.wait_for(backend_started.wait(), timeout=1)
        disconnected_client = session._connection.current
        assert disconnected_client is not None
        await disconnected_client.close()
        release_backend.set()
        await asyncio.sleep(0.05)
        assert not callback_task.done()

        allow_reconnect.set()
        callback = await asyncio.wait_for(callback_task, timeout=1)
        await session.respond_to_callback(
            callback.callback.callback_id,
            ApprovalCallbackOutput(
                decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
            ),
        )
        _ = [event async for event in stream]
    finally:
        allow_reconnect.set()
        release_backend.set()
        if not callback_task.done():
            callback_task.cancel()
            with suppress(asyncio.CancelledError):
                await callback_task
        await stream.aclose()
        await session.close()

    effect = next(
        entry for entry in session.history if isinstance(entry, PublicEffectEntry)
    )
    assert isinstance(effect.state, CompletedEffectState)


@pytest.mark.asyncio
async def test_callback_response_is_retry_safe_and_rejects_conflicts() -> None:
    tool_call = ToolCall(
        id="todo-1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Done")],
    ])
    config = build_test_vibe_config(
        enabled_tools=["todo"], tools={"todo": {"permission": ToolPermission.ASK.value}}
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    session = await create_test_app_server_session(agent_loop)
    approved = ApprovalCallbackOutput(
        decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
    )
    denied = ApprovalCallbackOutput(
        decision=ApprovalDecision(type=ApprovalDecisionType.DENY)
    )

    try:
        async for event in session.act("read todos"):
            if not isinstance(event, CallbackRequested):
                continue
            await session.respond_to_callback(event.callback.callback_id, approved)
            await session.respond_to_callback(event.callback.callback_id, approved)
            with pytest.raises(AppServerResponseError) as exc_info:
                await session.respond_to_callback(event.callback.callback_id, denied)
            assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    finally:
        await session.close()
        await agent_loop.aclose()


def test_callback_delivery_acknowledgement_has_no_semantic_output() -> None:
    response = CallbackCallResponse(callback_id="approval-1")

    assert validate_callback_acknowledgement("approval-1", response) is response
    assert response.model_dump(mode="json", by_alias=True) == {
        "callbackId": "approval-1",
        "accepted": True,
    }

    with pytest.raises(ValueError, match="does not match"):
        validate_callback_acknowledgement("approval-2", response)


@pytest.mark.asyncio
async def test_interrupt_cancels_open_callback(monkeypatch) -> None:
    tool_call = ToolCall(
        id="todo-1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )
    backend = FakeBackend([[mock_llm_chunk(content="", tool_calls=[tool_call])]])
    config = build_test_vibe_config(
        enabled_tools=["todo"], tools={"todo": {"permission": ToolPermission.ASK.value}}
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    reject_request = Mock(wraps=agent_loop.reject_request)
    monkeypatch.setattr(agent_loop, "reject_request", reject_request)
    session = await create_test_app_server_session(agent_loop)
    callback_id: str | None = None

    try:
        async for event in session.act("read todos"):
            if isinstance(event, CallbackRequested):
                callback_id = event.callback.callback_id
                await session.interrupt()
    finally:
        await session.close()
        await agent_loop.aclose()

    callback = next(
        entry
        for entry in session.history
        if isinstance(entry, PublicCallbackEntry) and entry.callback_id == callback_id
    )
    assert isinstance(callback.state, CancelledCallbackState)
    assert callback.generation_status == "completed"
    assert session.state.active_callbacks == []
    assert reject_request.call_count == 1
    assert reject_request.call_args.args[0] == callback_id
    assert isinstance(reject_request.call_args.args[1], RuntimeError)


@pytest.mark.asyncio
async def test_attach_sends_snapshot_response_before_buffered_live_events() -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    try:
        server._begin_attachment()
        await server._notify(
            "session/updated",
            SessionUpdatedParams(
                event_id=0, session_id="session-1", patch=[], emitted_at=1
            ),
        )
        await server._send({"jsonrpc": "2.0", "id": "attach", "result": {}})
        await server._finish_attachment(True)

        messages = client_transport.messages()
        response = await anext(messages)
        notification = await anext(messages)

        assert response == {"jsonrpc": "2.0", "id": "attach", "result": {}}
        assert notification["method"] == "session/updated"
        assert notification["params"]["eventId"] == 1
    finally:
        await client_transport.close()
        await server_transport.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_runtime_mutation_result_publishes_the_coherent_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    client = AppServerClient(client_transport, run_peer=server.serve)
    apply_patch = AsyncMock(wraps=agent_loop.config_orchestrator.apply_patch)
    monkeypatch.setattr(agent_loop.config_orchestrator, "apply_patch", apply_patch)

    try:
        await client.initialize(ClientInfo(name="runtime-update-test", version="1"))
        await client.notify("initialized")
        await client.request("session/start", SessionStartParams())
        await client.request(
            "config/patch",
            ConfigPatchParams(
                session_id=agent_loop.session_id,
                ops=[
                    ConfigPatchOpWire(
                        op="set", path="/disable_welcome_banner_animation", value=True
                    ),
                    ConfigPatchOpWire(
                        op="set", path="/autocopy_to_clipboard", value=False
                    ),
                ],
            ),
        )

        apply_patch.assert_awaited_once()
        call = apply_patch.await_args
        assert call is not None
        operations = call.args[0]
        assert [operation.path for operation in operations] == [
            "/disable_welcome_banner_animation",
            "/autocopy_to_clipboard",
        ]

        incoming = client.incoming()
        async with asyncio.timeout(1):
            while True:
                message = await anext(incoming)
                if (
                    isinstance(message, Notification)
                    and message.method == "runtime/updated"
                ):
                    break
        await incoming.aclose()

        update = validate_wire(RuntimeUpdatedParams, message.params)
        assert update.session_id == agent_loop.session_id
        assert update.runtime.active_agent.name == agent_loop.agent_profile.name
    finally:
        await client.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_completed_turn_does_not_retain_callback_for_redelivery() -> None:
    tool_call = ToolCall(
        id="todo-1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[tool_call])],
        [mock_llm_chunk(content="Done")],
    ])
    config = build_test_vibe_config(
        enabled_tools=["todo"], tools={"todo": {"permission": ToolPermission.ASK.value}}
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(agent_loop, server_transport)
    session = await attach_test_app_server_session(
        AppServerClient(client_transport, run_peer=server.serve)
    )

    try:
        async for event in session.act("read todos"):
            if isinstance(event, CallbackRequested):
                await session.respond_to_callback(
                    event.callback.callback_id,
                    ApprovalCallbackOutput(
                        decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
                    ),
                )

        assert server._turns.callbacks == []
    finally:
        await session.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_initialize_must_be_first_request() -> None:
    agent_loop = build_test_agent_loop()
    client = start_test_app_server(agent_loop)

    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request("session/read", {"sessionId": agent_loop.session_id})
    finally:
        await client.close()
        await agent_loop.aclose()

    assert exc_info.value.error.code is ProtocolErrorCode.NOT_INITIALIZED


@pytest.mark.asyncio
async def test_initialize_requires_initialized_notification_and_advertises_capabilities() -> (
    None
):
    agent_loop = build_test_agent_loop()
    client = start_test_app_server(agent_loop)

    try:
        response = await client.initialize(
            ClientInfo(name="test", version="1"),
            ClientCapabilities(callback_kinds=["approval", "user_input"]),
        )
        assert response.protocol_version == "1"
        assert response.capabilities.methods == list(SERVER_METHODS)
        assert response.capabilities.callback_kinds == ["approval", "user_input"]
        assert response.capabilities.transports == ["in_process"]

        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "session/read", SessionReadParams(session_id=agent_loop.session_id)
            )
        assert exc_info.value.error.code is ProtocolErrorCode.NOT_INITIALIZED

        await client.notify("initialized")
        await client.request("session/start", SessionStartParams())
        session = SessionReadResponse.model_validate(
            await client.request(
                "session/read", SessionReadParams(session_id=agent_loop.session_id)
            )
        )
        assert session.state.session.id == agent_loop.session_id
    finally:
        await client.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_initialize_rejects_unknown_capability_fields() -> None:
    agent_loop = build_test_agent_loop()
    client = start_test_app_server(agent_loop)

    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "initialize",
                {
                    "clientInfo": {"name": "test", "version": "1"},
                    "capabilities": {"futureCapability": True},
                },
            )
    finally:
        await client.close()
        await agent_loop.aclose()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS


@pytest.mark.asyncio
async def test_cancelled_client_request_is_removed_from_pending_requests() -> None:
    client_transport, _ = memory_transport_pair()
    client = AppServerClient(client_transport)
    task = asyncio.create_task(client.request("test/wait"))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client._pending == {}
    await client.close()


@pytest.mark.asyncio
async def test_transport_eof_fails_pending_requests() -> None:
    client_transport, server_transport = memory_transport_pair()
    client = AppServerClient(client_transport)
    task = asyncio.create_task(client.request("test/wait"))

    await asyncio.sleep(0)
    await server_transport.close()

    with pytest.raises(AppServerConnectionClosed):
        await task
    assert client._pending == {}
    await client.close()


@pytest.mark.asyncio
async def test_server_messages_preserve_wire_order() -> None:
    client_transport, server_transport = memory_transport_pair()
    client = AppServerClient(client_transport)
    await client.start()

    await server_transport.send({
        "jsonrpc": "2.0",
        "method": "history/entryAdded",
        "params": {"entry": {}},
    })
    await server_transport.send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "callback/call",
        "params": {},
    })

    incoming = client.incoming()
    first = await anext(incoming)
    second = await anext(incoming)

    assert isinstance(first, Notification)
    assert first.method == "history/entryAdded"
    assert isinstance(second, ServerRequest)
    assert second.method == "callback/call"

    await incoming.aclose()
    await client.close()
    await server_transport.close()


@pytest.mark.asyncio
async def test_active_turn_fails_when_server_connection_closes() -> None:
    agent_loop = build_test_agent_loop()
    client = start_test_app_server(agent_loop)
    session = await attach_test_app_server_session(client)
    started = asyncio.Event()

    async def blocking_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="blocked-user")
        started.set()
        await asyncio.Event().wait()

    agent_loop.act = blocking_act
    turn = asyncio.create_task(_consume(session.act("wait")))
    try:
        await started.wait()
        peer = client._run_peer
        assert peer is not None
        await cast(Any, peer).__self__._transport.close()
        with pytest.raises(AppServerConnectionClosed):
            await asyncio.wait_for(turn, timeout=1)
    finally:
        turn.cancel()
        await session.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_connection_shutdown_cancels_blocked_request_without_drain_delay() -> (
    None
):
    agent_loop = build_test_agent_loop()
    client = start_test_app_server(agent_loop)
    session = await attach_test_app_server_session(client)
    entered = asyncio.Event()

    async def wait_until_ready() -> None:
        entered.set()
        await asyncio.Event().wait()

    agent_loop.wait_until_ready = wait_until_ready
    request = asyncio.create_task(
        client.request(
            "session/ready/wait", SessionReadyWaitParams(session_id=session.session_id)
        )
    )
    await entered.wait()

    await asyncio.wait_for(client.close(), timeout=1)
    with pytest.raises(AppServerConnectionClosed):
        await request
    await session.close()


@pytest.mark.asyncio
async def test_interrupt_completes_active_turn() -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)
    started = asyncio.Event()

    async def blocking_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="blocked-user")
        started.set()
        await asyncio.Event().wait()

    agent_loop.act = blocking_act

    task = asyncio.create_task(_consume(session.act("wait")))
    try:
        await started.wait()
        await session.interrupt()
        await task
    finally:
        task.cancel()
        await session.close()
        await agent_loop.aclose()

    user = next(
        entry
        for entry in session.history
        if isinstance(entry, PublicMessageEntry) and entry.role == "user"
    )
    assert user.text == "wait"
    assert session.state.session.status.type == "idle"


@pytest.mark.asyncio
async def test_steer_adds_a_public_user_message_to_the_active_turn() -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def blocking_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="initial-user")
        started.set()
        await finish.wait()

    agent_loop.act = blocking_act
    task = asyncio.create_task(_consume(session.act("wait")))
    try:
        await started.wait()
        active_turn = session.state.latest_turn
        assert active_turn is not None
        await session.inject_user_context(
            "follow up", as_message=True, client_message_id="steer-user"
        )
        for _ in range(20):
            if any(entry.id == "steer-user" for entry in session.history):
                break
            await asyncio.sleep(0)

        steered = next(entry for entry in session.history if entry.id == "steer-user")
        assert isinstance(steered, PublicMessageEntry)
        assert steered.text == "follow up"
        assert steered.source == "turn_steer"
        assert steered.turn_id == active_turn.id
    finally:
        finish.set()
        await task
        await session.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_clear_history_adopts_replacement_before_next_turn() -> None:
    backend = FakeBackend([
        [mock_llm_chunk(content="Before")],
        [mock_llm_chunk(content="After")],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    session = await create_test_app_server_session(agent_loop)

    try:
        await _consume(session.act("first"))
        original_session_id = session.session_id
        await session.clear_history()
        replacement_session_id = session.session_id
        events = await _consume(session.act("second"))
    finally:
        await session.close()

    assert replacement_session_id != original_session_id
    assert any(isinstance(event, HistoryEntryAdded) for event in events)
    assert all(entry.session_id == replacement_session_id for entry in session.history)
    assert any(
        isinstance(entry, PublicCheckpointEntry) and entry.kind == "clear"
        for entry in session.history
    )


@pytest.mark.asyncio
async def test_auto_compaction_hands_active_turn_to_replacement_session() -> None:
    agent_loop = build_test_agent_loop()
    agent_loop.stats.session_prompt_tokens = 11
    agent_loop.stats.session_completion_tokens = 7
    original_session_id = agent_loop.session_id
    replacement_session_id = f"{original_session_id}-compacted"

    async def compacting_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="user-1")
        yield CompactStartEvent(
            current_context_tokens=100, threshold=90, tool_call_id="compact-1"
        )
        agent_loop.session_id = replacement_session_id
        agent_loop.parent_session_id = original_session_id
        agent_loop.session_logger.reset_session(
            replacement_session_id, parent_session_id=original_session_id
        )
        agent_loop.stats.session_prompt_tokens += 2
        agent_loop.stats.session_completion_tokens += 1
        yield CompactEndEvent(
            tool_call_id="compact-1",
            summary_length=12,
            old_session_id=original_session_id,
            new_session_id=replacement_session_id,
        )
        yield AssistantEvent(content="continued", message_id="assistant-1")

    agent_loop.act = compacting_act
    session = await create_test_app_server_session(agent_loop)
    try:
        events = await _consume(session.act("continue"))
    finally:
        await session.close()

    assert session.session_id == replacement_session_id
    assert any(isinstance(event, SessionCompacted) for event in events)
    assert all(entry.session_id == replacement_session_id for entry in session.history)
    assert session.state.latest_turn is not None
    assert session.state.latest_turn.session_id == replacement_session_id
    assert session.state.latest_turn.status == "completed"
    assert session.exit_summary().usage.input_tokens == 2
    assert session.exit_summary().usage.output_tokens == 1


@pytest.mark.asyncio
async def test_plan_clear_hands_active_turn_to_replacement_session() -> None:
    agent_loop = build_test_agent_loop()
    agent_loop.stats.session_prompt_tokens = 11
    agent_loop.stats.session_completion_tokens = 7
    original_session_id = agent_loop.session_id
    replacement_session_id = f"{original_session_id}-plan"

    async def clearing_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="user-1")
        agent_loop.session_id = replacement_session_id
        agent_loop.parent_session_id = None
        agent_loop.session_logger.reset_session(replacement_session_id)
        agent_loop.stats = AgentStats()
        yield ContextClearedEvent(plan_file_path=None)
        agent_loop.stats.session_prompt_tokens = 2
        agent_loop.stats.session_completion_tokens = 1
        yield AssistantEvent(content="implementing", message_id="assistant-1")

    agent_loop.act = clearing_act
    session = await create_test_app_server_session(agent_loop)
    try:
        events = await _consume(session.act("accept plan"))
    finally:
        await session.close()

    assert session.session_id == replacement_session_id
    assert any(isinstance(event, SessionContextCleared) for event in events)
    assert all(entry.session_id == replacement_session_id for entry in session.history)
    assert session.state.latest_turn is not None
    assert session.state.latest_turn.status == "completed"
    assert session.exit_summary().usage.input_tokens == 2
    assert session.exit_summary().usage.output_tokens == 1


@pytest.mark.asyncio
async def test_interrupt_routes_from_replaced_session_during_handoff_race() -> None:
    agent_loop = build_test_agent_loop()
    original_session_id = agent_loop.session_id
    replacement_session_id = f"{original_session_id}-compacted"
    transitioned = asyncio.Event()

    async def compacting_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="user-1")
        yield CompactStartEvent(
            current_context_tokens=100, threshold=90, tool_call_id="compact-1"
        )
        agent_loop.session_id = replacement_session_id
        agent_loop.parent_session_id = original_session_id
        agent_loop.session_logger.reset_session(
            replacement_session_id, parent_session_id=original_session_id
        )
        yield CompactEndEvent(
            tool_call_id="compact-1",
            summary_length=12,
            old_session_id=original_session_id,
            new_session_id=replacement_session_id,
        )
        transitioned.set()
        await asyncio.Event().wait()

    agent_loop.act = compacting_act
    session = await create_test_app_server_session(agent_loop)
    turn = asyncio.create_task(_consume(session.act("continue")))
    try:
        await transitioned.wait()
        latest_turn = session.state.latest_turn
        assert latest_turn is not None
        turn_id = latest_turn.id
        client = session._connection.current
        assert client is not None
        response = await client.request(
            "turn/interrupt",
            {"sessionId": original_session_id, "expectedTurnId": turn_id},
        )
        events = await turn
    finally:
        turn.cancel()
        await session.close()

    assert response == {"interrupted": True}
    assert any(isinstance(event, SessionCompacted) for event in events)
    assert session.state.latest_turn is not None
    assert session.state.latest_turn.status == "interrupted"


@pytest.mark.asyncio
async def test_event_gap_resynchronizes_through_session_read(monkeypatch) -> None:
    agent_loop = build_test_agent_loop()
    turn_started = asyncio.Event()
    release_turn = asyncio.Event()

    async def blocking_act(msg: str, **_kwargs):
        turn_started.set()
        await release_turn.wait()
        yield UserMessageEvent(content=msg, message_id="resynced-user")
        yield AssistantEvent(content="Recovered", message_id="resynced-assistant")

    agent_loop.act = blocking_act
    client = start_test_app_server(agent_loop)
    run_peer = client._run_peer
    assert run_peer is not None
    server = cast(AppServer, cast(Any, run_peer).__self__)
    session = await attach_test_app_server_session(client)
    request = client.request
    methods = []
    resynced = asyncio.Event()

    async def tracked_request(method, params=None, *, wait_for_incoming=False):
        methods.append(method)
        return await request(method, params, wait_for_incoming=wait_for_incoming)

    monkeypatch.setattr(client, "request", tracked_request)
    resync = session._resync

    async def tracked_resync(resync_client) -> None:
        await resync(resync_client)
        resynced.set()

    monkeypatch.setattr(session, "_resync", tracked_resync)
    server._event_watermarks[session.session_id] = session.state.event_id + 1

    turn = asyncio.create_task(_consume(session.act("recover")))
    try:
        await turn_started.wait()
        await resynced.wait()
        release_turn.set()
        events = await turn
    finally:
        release_turn.set()
        turn.cancel()
        await session.close()

    assert "session/read" in methods
    assert any(isinstance(event, SessionSnapshot) for event in events)
    assert session._state.projection._last_event_id == server._event_watermark(
        session.session_id
    )
    assert any(
        isinstance(entry, PublicMessageEntry)
        and entry.role == "assistant"
        and entry.text == "Recovered"
        for entry in session.history
    )


@pytest.mark.asyncio
async def test_terminal_event_gap_completes_waiting_turn_from_snapshot(
    monkeypatch,
) -> None:
    agent_loop = build_test_agent_loop()

    async def completed_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="user-1")
        yield AssistantEvent(content="done", message_id="assistant-1")

    agent_loop.act = completed_act
    session = await create_test_app_server_session(agent_loop)
    client = session._connection.current
    assert client is not None
    request = client.request
    methods: list[str] = []

    async def tracked_request(method, params=None, *, wait_for_incoming=False):
        methods.append(method)
        return await request(method, params, wait_for_incoming=wait_for_incoming)

    monkeypatch.setattr(client, "request", tracked_request)
    consume = session._state.projection.consume
    gap_injected = False

    def consume_with_terminal_gap(notification):
        nonlocal gap_injected
        if notification.method == "turn/completed" and not gap_injected:
            gap_injected = True
            notification.params["eventId"] += 1
        return consume(notification)

    monkeypatch.setattr(session._state.projection, "consume", consume_with_terminal_gap)
    try:
        events = await asyncio.wait_for(_consume(session.act("finish")), timeout=1)
    finally:
        await session.close()

    assert gap_injected
    assert "session/read" in methods
    assert any(isinstance(event, SessionSnapshot) for event in events)
    assert session.state.latest_turn is not None
    assert session.state.latest_turn.status == "completed"


@pytest.mark.asyncio
async def test_rewind_uses_public_history_entry_id() -> None:
    agent_loop = build_test_agent_loop()
    user_index = len(agent_loop.messages)
    agent_loop.messages.append(
        LLMMessage(role=Role.user, content="rewind me", message_id="user-1")
    )
    agent_loop.messages.append(
        LLMMessage(role=Role.assistant, content="response", message_id="assistant-1")
    )
    agent_loop.rewind_manager.restorable_paths_at = lambda message_index: (
        ["x.py"] if message_index == user_index else []
    )
    rewind = AsyncMock(return_value=("rewind me", ["restore warning"], ["x.py"]))
    agent_loop.rewind_manager.rewind_to_message = rewind
    session = await create_test_app_server_session(agent_loop)

    try:
        assert await session.resources.sessions.rewind_preview("user-1") == ["x.py"]
        assert await session.resources.sessions.rewind_has_file_changes("user-1")
        result = await session.resources.sessions.rewind(
            "user-1", restore_files=True, inplace=True
        )
    finally:
        await session.close()

    rewind.assert_awaited_once_with(user_index, restore_files=True, inplace=True)
    assert result.message == "rewind me"
    assert result.restore_errors == ["restore warning"]
    assert result.restored_paths == ["x.py"]


@pytest.mark.asyncio
async def test_rewind_reserves_session_before_restoring_files() -> None:
    agent_loop = build_test_agent_loop()
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content="rewind me", message_id="user-1"),
        LLMMessage(role=Role.assistant, content="response", message_id="assistant-1"),
    ])
    entered = asyncio.Event()
    release = asyncio.Event()

    async def rewind(
        message_index: int, *, restore_files: bool, inplace: bool = False
    ) -> tuple[str, list[str], list[str]]:
        del message_index, restore_files, inplace
        entered.set()
        await release.wait()
        return "rewind me", [], []

    agent_loop.rewind_manager.rewind_to_message = rewind
    session = await create_test_app_server_session(agent_loop)
    rewinding = asyncio.create_task(
        session.resources.sessions.rewind("user-1", restore_files=True)
    )
    await entered.wait()
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await _consume(session.act("racing prompt"))
        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    finally:
        release.set()
        await rewinding
        await session.close()


async def _consume(events):
    return [event async for event in events]


async def _next_event[EventT](
    events: AsyncIterator[object], event_type: type[EventT]
) -> EventT:
    async for event in events:
        if isinstance(event, event_type):
            return event
    raise AssertionError(f"Event stream ended before {event_type.__name__}")


async def _create_reconnectable_session(
    agent_loop, *, reconnect_gate: asyncio.Event | None = None
) -> AppServerSession:
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(agent_loop, server_transport)

    def make_client(
        next_client_transport, next_server_transport, *, wait_for_gate: bool
    ) -> AppServerClient:
        async def serve_connection() -> None:
            if wait_for_gate and reconnect_gate is not None:
                await reconnect_gate.wait()
            await server.serve_connection(
                next_server_transport, close_on_disconnect=False
            )

        return AppServerClient(next_client_transport, run_peer=serve_connection)

    def reconnect() -> AppServerClient:
        return make_client(*memory_transport_pair(), wait_for_gate=True)

    return await AppServerSession.start(
        make_client(client_transport, server_transport, wait_for_gate=False),
        client_info=ClientInfo(name="reconnect-client", version="1"),
        capabilities=ClientCapabilities(callback_kinds=["approval", "user_input"]),
        client_factory=reconnect,
    )
