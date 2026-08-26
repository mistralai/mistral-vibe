from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import (
    attach_test_app_server_session,
    build_test_app_server,
    create_test_app_server_session,
    legacy_backend,
    start_test_app_server,
)
from tests.stubs.fake_backend import FakeBackend, FakeInterruptedStreamingBackend
from tests.stubs.fake_connector_catalog import FakeConnectorCatalogService
from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from vibe.app_server._legacy_composition import create_legacy_app_server
from vibe.app_server._legacy_session_backend import LegacySessionBackend
from vibe.app_server._model import validate_wire
from vibe.app_server._projection import project_history
from vibe.app_server._runtime import (
    AgentRuntimeFactory,
    HarnessProcess,
    RootOpenRequest,
    RuntimeSessionNotFoundError,
    _apply_stored_stats,
)
from vibe.app_server.client import AppServerClient, AppServerConnectionClosed
from vibe.app_server.connector_catalog import ConnectorRuntimeAuthorization
from vibe.app_server.events import (
    CallbackRequested,
    ConnectorAuthorizationRequiredEvent,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    SessionContextCleared,
    SessionSnapshot,
    StatsUpdated,
    TurnCompleted,
    TurnRetrying,
    TurnStarted,
)
from vibe.app_server.models import (
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    CompletedEffectState,
    FileReadEffectDetail,
    PublicCallbackEntry,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
    PublicRetryCategory,
    ResourceContentBlock,
    ScheduledLoopFiredNoticeDetail,
    TurnErrorCode,
    UserAnswer,
    UserInputCallbackOutput,
    UserQuestionResult,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    CallbackCallResponse,
    CallbackResultError,
    ClientCapabilities,
    ClientInfo,
    ConfigWriteOpWire,
    ConfigWriteParams,
    ConnectorAuthRequiredParams,
    Notification,
    PageRequest,
    ProtocolError,
    ProtocolErrorCode,
    RuntimeUpdatedParams,
    ServerRequest,
    SessionHistoryClearParams,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionReadyWaitParams,
    SessionReadyWaitResponse,
    SessionResumeParams,
    SessionStartParams,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    SessionUpdatedParams,
    validate_callback_acknowledgement,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.app_server.transport import memory_transport_pair
from vibe.core.agent_loop import AgentLoop
from vibe.core.compaction import CompactionFailedError, select_model_context
from vibe.core.config import ModelConfig, SessionLoggingConfig
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.session.session_lease import SessionBusyError, SessionLease
from vibe.core.session.session_loader import SessionLoader
from vibe.core.tools.connectors.connector_registry import ConnectorRegistry, RemoteTool
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
from vibe.core.utils import RetryReason
from vibe.user_content import UserResourceLink


def _wire_resume_request(session_id: str) -> dict:
    return SessionResumeParams(session_id=session_id).model_dump(
        mode="json", by_alias=True
    )


def _wire_read_request(session_id: str) -> dict:
    return SessionReadParams(session_id=session_id).model_dump(
        mode="json", by_alias=True
    )


@pytest.mark.asyncio
async def test_legacy_session_start_applies_host_connector_catalog() -> None:
    config = build_test_vibe_config(enable_connectors=True)
    registry = ConnectorRegistry(api_key="fake-key")
    agent_loop = build_test_agent_loop(config=config, connector_registry=registry)
    catalog = FakeConnectorCatalogService(
        FakeConnectorRegistry(
            connectors={
                "github": [RemoteTool(name="search", description="Search GitHub")]
            }
        )
    )

    session = await create_test_app_server_session(
        agent_loop, connector_catalog_service=catalog
    )
    try:
        assert registry._host_managed
        assert registry._host_catalog_entries is not None
        assert [entry.alias for entry in registry._host_catalog_entries] == ["github"]
        assert any("search" in name for name in registry.get_tools())
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_connector_auth_notification_failure_releases_reservation() -> None:
    release_reservation = Mock()
    start_broker = Mock()
    service = SimpleNamespace(
        accept_auth_required=AsyncMock(
            return_value=ConnectorRuntimeAuthorization(
                runtime_updated=cast(RuntimeUpdatedParams, object()),
                start_broker=start_broker,
                release_reservation=release_reservation,
            )
        )
    )
    server = SimpleNamespace(
        _connector_catalog_service=service,
        _root=None,
        _notify=AsyncMock(),
        _route_notification=AsyncMock(side_effect=RuntimeError("disconnected")),
    )
    event = ConnectorAuthorizationRequiredEvent(
        params=ConnectorAuthRequiredParams(
            session_id="session-1",
            alias="github",
            accepted_catalog_revision="catalog-1",
            reason="gateway_rejected",
        ),
        raw_connector_id="github/raw",
        action="oauth",
    )

    with pytest.raises(RuntimeError, match="disconnected"):
        await AppServer._forward_connector_authorization(cast(AppServer, server), event)

    release_reservation.assert_called_once_with()
    start_broker.assert_not_called()


@pytest.mark.asyncio
async def test_injected_turn_hides_user_after_stream_failure() -> None:
    backend = FakeInterruptedStreamingBackend([
        [mock_llm_chunk(content="partial")],
        [mock_llm_chunk(content="recovered")],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    session = await create_test_app_server_session(agent_loop)

    try:
        with pytest.raises(AppServerTurnError) as exc_info:
            await _consume(session.act("hi", client_message_id="u1"))

        assert agent_loop.messages[-1].role is Role.assistant
        assert agent_loop.messages[-1].content == "partial"
        await _consume(session.act("hidden continuation", injected=True))
        restored_history = project_history(agent_loop)
    finally:
        await session.close()
        await agent_loop.aclose()

    assert exc_info.value.error.code == TurnErrorCode.BACKEND_ERROR
    assert backend.streaming_attempts == 2
    assistant_messages = [
        entry
        for entry in session.history
        if isinstance(entry, PublicMessageEntry) and entry.role == "assistant"
    ]
    assert [entry.text for entry in assistant_messages] == ["partial", "recovered"]
    user_entries = [
        entry
        for entry in session.history
        if isinstance(entry, PublicMessageEntry) and entry.role == "user"
    ]
    assert len(user_entries) == 1
    restored_user_entries = [
        entry
        for entry in restored_history
        if isinstance(entry, PublicMessageEntry) and entry.role == "user"
    ]
    restored_assistant_entries = [
        entry
        for entry in restored_history
        if isinstance(entry, PublicMessageEntry) and entry.role == "assistant"
    ]
    assert len(restored_user_entries) == 1
    assert [entry.text for entry in restored_assistant_entries] == [
        "partial",
        "recovered",
    ]
    injected_message = backend.requests_messages[-1][-1]
    assert injected_message.role is Role.user
    assert injected_message.injected is True
    assert injected_message.content == "hidden continuation"


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
async def test_turn_pushes_fresh_context_while_tool_runs() -> None:
    tool_call = ToolCall(
        id="todo-1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )
    backend = FakeBackend([
        [
            mock_llm_chunk(content="", reasoning_content="thinking"),
            mock_llm_chunk(content="Let me check", tool_calls=[tool_call]),
        ],
        [mock_llm_chunk(content="Done")],
    ])
    config = build_test_vibe_config(
        enabled_tools=["todo"],
        tools={"todo": {"permission": ToolPermission.ALWAYS.value}},
    )
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    session = await create_test_app_server_session(agent_loop)

    try:
        events = [event async for event in session.act("use a tool")]
    finally:
        await session.close()
        await agent_loop.aclose()

    tool_done_index = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, HistoryEntryUpdated)
        and isinstance(event.entry, PublicEffectEntry)
        and isinstance(event.entry.state, CompletedEffectState)
    )
    assert any(
        isinstance(event, StatsUpdated) and event.params.stats.context_tokens > 0
        for event in events[:tool_done_index]
    )
    zero_updates = sum(
        isinstance(event, StatsUpdated) and event.params.stats.context_tokens == 0
        for event in events
    )
    assert zero_updates == 1


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
        initialize.assert_called_once_with(defer_new_session_telemetry=False)
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
async def test_in_place_resume_clears_previous_turn_state(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    backend = FakeBackend([[mock_llm_chunk(content="first answer")]])
    client = start_test_app_server(
        build_test_agent_loop(config=config, backend=backend, enable_streaming=True)
    )
    session = await attach_test_app_server_session(client)
    try:
        await _consume(session.act("first question"))
        # Resuming a different session rebinds the loop in place; the reused turn
        # controller must be reset so the previous session's turns do not leak.
        await session.resume(saved_session_id)
        result = await client.request(
            "session/turns/list",
            SessionTurnsListParams(
                session_id=saved_session_id, page=PageRequest(limit=10)
            ),
        )
    finally:
        await session.close()

    assert SessionTurnsListResponse.model_validate(result).items == []


@pytest.mark.asyncio
async def test_in_place_resume_rehydrates_experiments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    source = build_test_agent_loop(config=config)
    hydrated_for: list[str] = []

    async def fake_hydrate() -> None:
        # Runs only after the rebind, so it observes the resumed session id and
        # thus hydrates from the resumed session's persisted experiment state.
        hydrated_for.append(source.session_id)

    monkeypatch.setattr(source, "hydrate_experiments_from_session", fake_hydrate)
    try:
        factory = AgentRuntimeFactory()
        await factory.resume_root(source, saved_session_id)
        await factory.finish_resume_root(source, saved_session_id)
    finally:
        await source.aclose()

    assert hydrated_for == [saved_session_id]


@pytest.mark.asyncio
async def test_in_place_resume_resets_permissions_and_scratchpad(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    source = build_test_agent_loop(config=config)
    source._permission_store.set_tool_permission("bash", ToolPermission.ALWAYS)
    scratchpad_before = source.scratchpad_dir
    try:
        await AgentRuntimeFactory().resume_root(source, saved_session_id)
        # Prior session's approvals must not carry over, the scratchpad must be
        # re-derived for the resumed session, and the fresh session's experiment
        # task must be cancelled so it cannot clobber the hydrated variants.
        assert source._permission_store.get_tool_permission("bash") is None
        assert source.scratchpad_dir != scratchpad_before
        assert source._experiments_task is None
    finally:
        await source.aclose()


@pytest.mark.asyncio
async def test_in_place_resume_leaves_source_untouched_when_session_missing(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    source = build_test_agent_loop(config=config)
    original_session_id = source.session_id
    try:
        with pytest.raises(RuntimeSessionNotFoundError):
            await AgentRuntimeFactory().resume_root(source, "missing-session")
        # The fallible load happens before any mutation, so a failed resume must
        # leave the live loop bound to its original session.
        assert source.session_id == original_session_id
    finally:
        await source.aclose()


@pytest.mark.asyncio
async def test_in_place_resume_releases_lease_when_metadata_preparation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    def fail_stats(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("invalid persisted stats")

    monkeypatch.setattr("vibe.app_server._runtime._build_stats", fail_stats)
    source = build_test_agent_loop(config=config)
    try:
        with pytest.raises(RuntimeError, match="invalid persisted stats"):
            await AgentRuntimeFactory().resume_root(source, saved_session_id)
        with SessionLease(tmp_path, saved_session_id):
            pass
    finally:
        await source.aclose()


@pytest.mark.asyncio
async def test_in_place_resume_rebinds_before_waiting_for_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    source = build_test_agent_loop(config=config)
    session_id_when_awaited: list[str] = []

    async def fake_await_deferred_init() -> None:
        session_id_when_awaited.append(source.session_id)

    monkeypatch.setattr(source, "_await_deferred_init", fake_await_deferred_init)
    try:
        factory = AgentRuntimeFactory()
        await factory.resume_root(source, saved_session_id)
        # The rebind happens before wait_until_ready so the transcript is
        # visible immediately; the session is already the resumed one.
        assert source.session_id == saved_session_id
        await factory.finish_resume_root(source, saved_session_id)
    finally:
        await source.aclose()

    # Deferred init is awaited after the rebind, so it observes the resumed
    # session id — the init thread's update_system_prompt inserts at
    # position 0, landing correctly on top of the resumed messages.
    assert session_id_when_awaited == [saved_session_id]
    assert source.session_id == saved_session_id


@pytest.mark.asyncio
async def test_resume_records_init_duration(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config, defer_heavy_init=True)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    source = build_test_agent_loop(config=config, defer_heavy_init=True)
    try:
        factory = AgentRuntimeFactory()
        await factory.resume_root(source, saved_session_id)
        await factory.finish_resume_root(source, saved_session_id)
        await source.wait_until_ready()
    finally:
        await source.aclose()

    assert source.init_duration_ms is not None
    assert isinstance(source.init_duration_ms, int)
    assert source.init_duration_ms >= 0


@pytest.mark.asyncio
async def test_wait_until_ready_records_init_duration_on_resume_without_finish_resume(
    tmp_path: Path,
) -> None:
    # session/ready/wait reads via wait_until_ready, which must record the
    # duration itself even if the background finish_resume_root hasn't run.
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config, defer_heavy_init=True)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    source = build_test_agent_loop(config=config, defer_heavy_init=True)
    try:
        await AgentRuntimeFactory().resume_root(source, saved_session_id)
        assert source._ready_telemetry_pending is False
        await source.wait_until_ready()
    finally:
        await source.aclose()

    assert source.init_duration_ms is not None
    assert isinstance(source.init_duration_ms, int)
    assert source.init_duration_ms >= 0


@pytest.mark.asyncio
async def test_resume_does_not_emit_ready_or_new_session_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config, defer_heavy_init=True)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    source = build_test_agent_loop(config=config, defer_heavy_init=True)
    emit_ready = Mock()
    emit_new_session = Mock()
    monkeypatch.setattr(source, "emit_ready_telemetry", emit_ready)
    monkeypatch.setattr(source, "emit_new_session_telemetry", emit_new_session)
    try:
        await AgentRuntimeFactory().resume_root(source, saved_session_id)
        await source.wait_until_ready()
    finally:
        await source.aclose()

    emit_ready.assert_not_called()
    emit_new_session.assert_not_called()
    assert source.init_duration_ms is not None


@pytest.mark.asyncio
async def test_resume_does_not_leak_picker_init_duration(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config, defer_heavy_init=True)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    source = build_test_agent_loop(config=config, defer_heavy_init=True)
    try:
        await source.wait_until_ready()
        assert source.init_duration_ms is not None
        picker_duration = source.init_duration_ms

        await AgentRuntimeFactory().resume_root(source, saved_session_id)
        # Rebind clears the picker's duration so it doesn't leak; _init_start_time
        # is kept, so the resumed session re-measures from __init__.
        assert source.init_duration_ms is None

        await source.wait_until_ready()
        assert source.init_duration_ms is not None
        assert source.init_duration_ms >= picker_duration
    finally:
        await source.aclose()


@pytest.mark.asyncio
async def test_non_deferred_resume_stays_none(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config, defer_heavy_init=False)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    source = build_test_agent_loop(config=config, defer_heavy_init=False)
    try:
        factory = AgentRuntimeFactory()
        await factory.resume_root(source, saved_session_id)
        await factory.finish_resume_root(source, saved_session_id)
        await source.wait_until_ready()
    finally:
        await source.aclose()

    assert source.init_duration_ms is None


@pytest.mark.asyncio
async def test_compaction_keeps_session_and_restores_full_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logging = SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    config = build_test_vibe_config(session_logging=logging)
    backend = FakeBackend([
        [mock_llm_chunk(content="Before compaction")],
        [mock_llm_chunk(content="<summary>First turn completed</summary>")],
        [mock_llm_chunk(content="After compaction")],
    ])
    agent_loop = build_test_agent_loop(
        config=config, backend=backend, enable_streaming=True
    )
    session = await create_test_app_server_session(agent_loop)

    try:
        await _consume(session.act("first question", client_message_id="user-1"))
        session_id = session.session_id
        await session.compact()
        assert session.session_id == session_id
        assert [
            entry.text
            for entry in session.history
            if isinstance(entry, PublicMessageEntry)
        ] == ["first question", "Before compaction"]
        assert (
            sum(
                isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
                for entry in session.history
            )
            == 1
        )
        await _consume(session.act("second question", client_message_id="user-2"))
        session_dir = agent_loop.session_logger.session_dir
        assert session_dir is not None
    finally:
        await session.close()

    saved_messages, metadata = SessionLoader.load_session(session_dir)
    assert metadata["session_id"] == session_id
    assert [
        message.content
        for message in saved_messages
        if message.role in {Role.user, Role.assistant} and not message.context_boundary
    ] == ["first question", "Before compaction", "second question", "After compaction"]
    boundaries = [
        message
        for message in saved_messages
        if message.context_boundary == "compaction"
    ]
    assert len(boundaries) == 1
    assert len(SessionLoader.list_sessions(logging)) == 1

    resumed_backend = FakeBackend([[mock_llm_chunk(content="Resumed answer")]])
    monkeypatch.setattr(
        "vibe.core.agent_loop._loop.create_backend", lambda **_: resumed_backend
    )
    resumed = await attach_test_app_server_session(
        start_test_app_server(
            build_test_agent_loop(
                config=config, backend=resumed_backend, enable_streaming=True
            )
        ),
        resume_session_id=session_id,
    )
    try:
        assert any(
            isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
            for entry in resumed.history
        )
        assert [
            entry.text
            for entry in resumed.history
            if isinstance(entry, PublicMessageEntry)
        ] == [
            "first question",
            "Before compaction",
            "second question",
            "After compaction",
        ]
        await _consume(resumed.act("third question", client_message_id="user-3"))
    finally:
        await resumed.close()

    model_context = resumed_backend.requests_messages[0]
    assert len(model_context) == 5
    assert model_context[1].context_boundary == "compaction"
    assert [message.content for message in model_context[2:]] == [
        "second question",
        "After compaction",
        "third question",
    ]


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
async def test_in_place_resume_resets_stats_when_target_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    source = build_test_agent_loop(config=config)
    source.stats.session_prompt_tokens = 123
    source.stats.session_completion_tokens = 45
    monkeypatch.setattr("vibe.app_server._runtime._build_stats", lambda *a, **k: None)
    try:
        await AgentRuntimeFactory().resume_root(source, saved_session_id)
        assert source.stats.session_prompt_tokens == 0
        assert source.stats.session_completion_tokens == 0
        active_model = source.config.get_active_model()
        assert source.stats.input_price_per_million == active_model.input_price
        assert source.stats.output_price_per_million == active_model.output_price
    finally:
        await source.aclose()


@pytest.mark.asyncio
async def test_resume_backfills_cached_price_for_legacy_stats() -> None:
    config = build_test_vibe_config(
        models=[
            ModelConfig(
                name="cached-model",
                provider="mistral",
                alias="cached-model",
                input_price=1.0,
                output_price=2.0,
                cached_input_price=0.1,
            )
        ]
    )
    replacement = build_test_agent_loop(config=config)
    try:
        legacy_metadata: dict[str, object] = {
            "stats": {
                "session_prompt_tokens": 100,
                "session_completion_tokens": 50,
                "input_price_per_million": 1.0,
                "output_price_per_million": 2.0,
            }
        }
        _apply_stored_stats(replacement, legacy_metadata)
        assert replacement.stats.session_prompt_tokens == 100
        assert replacement.stats.cached_input_price_per_million == 0.1
    finally:
        await replacement.aclose()


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
@pytest.mark.parametrize(
    ("message_id", "expected_context"),
    [
        pytest.param("u1", ["first", "first reply"], id="before-compaction"),
        pytest.param(
            "u2",
            ["first compacted context", "second", "second reply"],
            id="between-compactions",
        ),
        pytest.param(
            "u3",
            ["second compacted context", "third", "third reply"],
            id="after-compactions",
        ),
    ],
)
async def test_fork_uses_latest_compaction_boundary_in_copied_prefix(
    message_id: str, expected_context: list[str]
) -> None:
    source = build_test_agent_loop()
    source.messages.reset([
        LLMMessage(role=Role.system, content="system"),
        LLMMessage(role=Role.user, content="first", message_id="u1"),
        LLMMessage(role=Role.assistant, content="first reply"),
        LLMMessage(
            role=Role.user,
            content="first compacted context",
            injected=True,
            context_boundary="compaction",
        ),
        LLMMessage(role=Role.user, content="second", message_id="u2"),
        LLMMessage(role=Role.assistant, content="second reply"),
        LLMMessage(
            role=Role.user,
            content="second compacted context",
            injected=True,
            context_boundary="compaction",
        ),
        LLMMessage(role=Role.user, content="third", message_id="u3"),
        LLMMessage(role=Role.assistant, content="third reply"),
    ])

    forked = await AgentRuntimeFactory().fork(source, message_id)
    try:
        assert forked.parent_session_id == source.session_id
        assert [
            message.content
            for message in select_model_context(forked.messages)
            if message.role is not Role.system
        ] == expected_context
    finally:
        await forked.aclose()
        await source.aclose()


@pytest.mark.asyncio
async def test_detached_fork_transfers_live_runtime_without_session_logging() -> None:
    process = HarnessProcess()
    source_loop = build_test_agent_loop()

    async def open_source(_request: RootOpenRequest) -> AgentLoop:
        return source_loop

    source_client_transport, source_server_transport = memory_transport_pair()
    source_server = create_legacy_app_server(
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
        child_server = create_legacy_app_server(
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
        runtime_policy = legacy_backend(child_server).session.agent_loop.runtime_policy
        assert runtime_policy.max_turns == 7
        assert runtime_policy.max_tokens == 4096
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
    server = create_legacy_app_server(
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
async def test_session_history_get_is_reachable_while_root_active() -> None:
    # The history resource reads through the selected backend Host, so a
    # missing session must surface NOT_FOUND while another root is attached.
    session = await create_test_app_server_session(build_test_agent_loop())
    try:
        with pytest.raises(AppServerResponseError) as excinfo:
            await session.resources.sessions.get_session_history("does-not-exist")
    finally:
        await session.close()

    assert excinfo.value.error.code is ProtocolErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_session_history_get_returns_projected_entries_and_respects_limit() -> (
    None
):
    # No custom save_dir: the backend Host reads from the default
    # test-isolated session directory.
    config = build_test_vibe_config(session_logging=SessionLoggingConfig(enabled=True))
    saved = build_test_agent_loop(config=config)
    saved.messages.reset([
        LLMMessage(role=Role.system, content="system"),
        LLMMessage(role=Role.user, content="first question"),
        LLMMessage(role=Role.assistant, content="first answer"),
        LLMMessage(role=Role.user, content="second question"),
        LLMMessage(role=Role.assistant, content="second answer"),
    ])
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    session = await create_test_app_server_session(build_test_agent_loop(config=config))
    try:
        full = await session.resources.sessions.get_session_history(saved_session_id)
        limited = await session.resources.sessions.get_session_history(
            saved_session_id, history_limit=1
        )
    finally:
        await session.close()

    roles = [e.role for e in full if isinstance(e, PublicMessageEntry)]
    assert Role.user in roles
    assert Role.assistant in roles
    dumped = " ".join(e.model_dump_json() for e in full)
    assert "first question" in dumped
    assert "second question" in dumped
    assert "second answer" in dumped
    assert 0 < len(limited) < len(full)


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
    factory = AgentRuntimeFactory()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    sources = []

    async def resume_root(source, session_id):
        sources.append(source)
        if session_id == "first":
            first_entered.set()
            await release_first.wait()
        source.session_id = session_id  # simulate in-place rebind

    monkeypatch.setattr(factory, "resume_root", resume_root)

    async def open_root(_request: RootOpenRequest):
        return initial

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(
        server_transport, open_root=open_root, runtime_factory=factory
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="resume-test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", SessionStartParams())

    first_resume = asyncio.create_task(
        client.request("session/resume", _wire_resume_request("first"))
    )
    await first_entered.wait()
    second_resume = asyncio.create_task(
        client.request("session/resume", _wire_resume_request("second"))
    )
    await asyncio.sleep(0)
    # The second resume waits behind the lifecycle lock; only the first has
    # started, and both always share the same loop object (in-place mutation).
    assert sources == [initial]
    release_first.set()
    try:
        await asyncio.gather(first_resume, second_resume)
        # Second resume runs against the already-rebounded loop (still initial).
        assert len(sources) == 2
        assert sources[0] is initial
        assert sources[1] is initial
        assert isinstance(server._root, LegacySessionBackend)
        assert server._root.session.agent_loop.session_id == "second"
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
    server = create_legacy_app_server(
        server_transport, open_root=open_root, runtime_factory=factory
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="resume-test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", SessionStartParams())

    resume = asyncio.create_task(
        client.request("session/resume", _wire_resume_request(replacement.session_id))
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
async def test_resume_returns_before_init_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = build_test_agent_loop(session_id="initial")
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def blocking_wait_until_ready() -> None:
        waiting.set()
        await release.wait()

    factory = AgentRuntimeFactory()

    async def fake_resume_root(source: object, session_id: str) -> None:
        source.session_id = session_id  # type: ignore[attr-defined]

    async def fake_finish_resume_root(
        source: object, session_id: str, **kwargs: object
    ) -> None:
        await source.wait_until_ready()  # type: ignore[attr-defined]

    monkeypatch.setattr(factory, "resume_root", fake_resume_root)
    monkeypatch.setattr(factory, "finish_resume_root", fake_finish_resume_root)

    async def open_root(_request: RootOpenRequest):
        return initial

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(
        server_transport, open_root=open_root, runtime_factory=factory
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="resume-test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", SessionStartParams())

    monkeypatch.setattr(initial, "wait_until_ready", blocking_wait_until_ready)
    resume = asyncio.create_task(
        client.request("session/resume", SessionResumeParams(session_id="replacement"))
    )
    await waiting.wait()
    # The resume RPC must return before deferred init finishes — that is the
    # whole point of fast resume. finish_resume_root is still blocked on
    # wait_until_ready, so the resume task should already be done.
    assert resume.done()
    try:
        await resume
    finally:
        release.set()
        await client.close()


@pytest.mark.asyncio
async def test_resume_rebinds_session_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    initial = build_test_agent_loop(session_id="initial")
    initial_close = AsyncMock(wraps=initial.aclose)
    initial.aclose = initial_close
    factory = AgentRuntimeFactory()

    async def fake_resume_root(source: object, session_id: str) -> None:
        # Simulate in-place rebind: only session_id changes, same object.
        source.session_id = session_id  # type: ignore[attr-defined]

    monkeypatch.setattr(factory, "resume_root", fake_resume_root)

    async def open_root(_request: RootOpenRequest):
        return initial

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(
        server_transport, open_root=open_root, runtime_factory=factory
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="resume-test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", SessionStartParams())

    try:
        await client.request(
            "session/resume", SessionResumeParams(session_id="replacement")
        )
        # The same loop object stays active — no new loop was built or swapped in.
        assert isinstance(server._root, LegacySessionBackend)
        assert server._root.session.agent_loop is initial
        assert server._root.session.agent_loop.session_id == "replacement"
        # The runtime is never closed — it's still the active root.
        initial_close.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_failed_resume_keeps_previous_root_and_reports_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = build_test_agent_loop(session_id="initial")
    factory = AgentRuntimeFactory()
    monkeypatch.setattr(
        factory,
        "resume_root",
        AsyncMock(side_effect=RuntimeError("boom while rebuilding")),
    )

    async def open_root(_request: RootOpenRequest):
        return initial

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(
        server_transport, open_root=open_root, runtime_factory=factory
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="resume-test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", SessionStartParams())

    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "session/resume", SessionResumeParams(session_id="replacement")
            )
        # The failure surfaces the underlying reason rather than a generic
        # "Invalid request parameters", and the previous root stays active.
        assert "Failed to resume session replacement" in exc_info.value.error.message
        assert "boom while rebuilding" in exc_info.value.error.message
        assert isinstance(server._root, LegacySessionBackend)
        assert server._root.session.agent_loop.session_id == "initial"
        # The still-attached root keeps serving requests instead of bricking.
        await client.request(
            "session/history/clear", SessionHistoryClearParams(session_id="initial")
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_busy_resume_reports_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    initial = build_test_agent_loop(session_id="initial")
    factory = AgentRuntimeFactory()
    monkeypatch.setattr(
        factory, "resume_root", AsyncMock(side_effect=SessionBusyError("replacement"))
    )

    async def open_root(_request: RootOpenRequest):
        return initial

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(
        server_transport, open_root=open_root, runtime_factory=factory
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="resume-test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", SessionStartParams())

    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "session/resume", SessionResumeParams(session_id="replacement")
            )
        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
        assert exc_info.value.error.message == "Session is already open: replacement"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_resume_reports_success_when_post_rebind_refresh_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    saved_session_id = saved.session_id
    await saved.aclose()

    backend = FakeBackend([[mock_llm_chunk(content="first answer")]])
    client = start_test_app_server(
        build_test_agent_loop(config=config, backend=backend, enable_streaming=True)
    )
    session = await attach_test_app_server_session(client)
    try:
        await _consume(session.act("first question"))

        # The rebind commits before root state is refreshed. A malformed stored
        # transcript can make project_history raise something other than the
        # ValidationError the projection already tolerates (e.g. KeyError). That
        # must not fail the resume nor leave the previous session's history behind.
        def boom(_loop: object) -> list[PublicHistoryEntry]:
            raise KeyError("unexpected stored shape")

        monkeypatch.setattr("vibe.app_server._root_session.project_history", boom)
        await session.resume(saved_session_id)

        checkpoints = [
            entry
            for entry in session.history
            if isinstance(entry, PublicCheckpointEntry) and entry.kind == "resume"
        ]
        # Resume still succeeds: the checkpoint is appended and the view is rebound
        # to the resumed session with a degraded (empty) projected history rather
        # than the previous session's stale transcript.
        assert len(checkpoints) == 1
        assert not any(
            isinstance(entry, PublicMessageEntry) for entry in session.history
        )
        # The root is attached to the resumed session, so it keeps serving RPCs.
        result = await client.request(
            "session/turns/list",
            SessionTurnsListParams(
                session_id=saved_session_id, page=PageRequest(limit=10)
            ),
        )
        assert SessionTurnsListResponse.model_validate(result).items == []
    finally:
        await session.close()


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
async def test_live_retry_is_in_session_read_and_reconnect_snapshot() -> None:
    retry_started = asyncio.Event()
    agent_loop = build_test_agent_loop()

    async def retrying_act(*_args, turn_options, **_kwargs):
        assert turn_options.retry_sink is not None
        await turn_options.retry_sink(RetryReason.from_http_status(429))
        retry_started.set()
        await asyncio.Event().wait()
        yield AssistantEvent(content="unreachable", message_id="assistant-1")

    agent_loop.act = retrying_act
    session = await _create_reconnectable_session(agent_loop)
    stream = session.act("wait for provider")
    retry_snapshot_task = asyncio.create_task(_next_event(stream, SessionSnapshot))

    try:
        await asyncio.wait_for(retry_started.wait(), timeout=1)
        retry_snapshot = await asyncio.wait_for(retry_snapshot_task, timeout=1)
        await _next_event(stream, TurnRetrying)
        client = session._connection.current
        assert client is not None
        response = validate_wire(
            SessionReadResponse,
            await client.request(
                "session/read", SessionReadParams(session_id=session.session_id)
            ),
        )
        retrying = response.state.retrying
        assert retrying is not None
        assert retrying.category is PublicRetryCategory.RATE_LIMITED
        assert retrying.detail == "HTTP 429"
        assert response.state.latest_turn is not None
        assert retrying.turn_id == response.state.latest_turn.id
        assert retry_snapshot.state.retrying == retrying

        await client.close()
        snapshot = await _next_event(stream, SessionSnapshot)
        assert snapshot.state.retrying == retrying

        await session.interrupt()
        await _consume(stream)
    finally:
        if not retry_snapshot_task.done():
            retry_snapshot_task.cancel()
            with suppress(asyncio.CancelledError):
                await retry_snapshot_task
        await stream.aclose()
        await session.close()


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
async def test_callback_result_requires_output_or_error() -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)

    try:
        client = await session._ensure_attached()
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "callback/result",
                {
                    "sessionId": session.session_id,
                    "result": {"callbackId": "callback-1"},
                },
            )
    finally:
        await session.close()
        await agent_loop.aclose()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert (
        exc_info.value.error.message == "Callback result must include output or error"
    )


@pytest.mark.asyncio
async def test_callback_error_is_retry_safe_and_rejects_conflicts() -> None:
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
    session = await create_test_app_server_session(agent_loop)
    error = CallbackResultError(
        message="Client cannot answer this callback",
        code="client_unavailable",
        details={"retryable": False},
    )

    try:
        with pytest.raises(AppServerTurnError, match=error.message):
            async for event in session.act("read todos"):
                if not isinstance(event, CallbackRequested):
                    continue
                await session.reject_callback(event.callback.callback_id, error)
                await session.reject_callback(event.callback.callback_id, error)
                with pytest.raises(AppServerResponseError) as exc_info:
                    await session.reject_callback(
                        event.callback.callback_id,
                        CallbackResultError(message="A different callback error"),
                    )
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
async def test_legacy_interrupt_rejects_the_open_callback_in_core(monkeypatch) -> None:
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

    assert callback_id is not None
    assert reject_request.call_count == 1
    assert reject_request.call_args.args[0] == callback_id
    assert isinstance(reject_request.call_args.args[1], RuntimeError)


@pytest.mark.asyncio
async def test_interrupt_waits_for_pending_turn_events() -> None:
    session = _interrupt_session()
    client = _InterruptClient({"accepted": True, "lastEventId": 1})
    session._ensure_attached = AsyncMock(return_value=client)

    await session.interrupt()

    assert client.requests == [
        {
            "method": "turn/interrupt",
            "session_id": "session-1",
            "expected_turn_id": "turn-1",
            "wait_for_incoming": True,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProtocolError(code=ProtocolErrorCode.STALE_TURN, message="Stale turn"),
        ProtocolError(code=ProtocolErrorCode.CONFLICT, message="No active turn"),
        ProtocolError(
            code=ProtocolErrorCode.CONFLICT, message="No matching active turn"
        ),
    ],
)
async def test_interrupt_ignores_already_settled_turn_errors(
    error: ProtocolError,
) -> None:
    session = _interrupt_session()
    session._ensure_attached = AsyncMock(
        return_value=_InterruptClient(AppServerResponseError(error))
    )

    await session.interrupt()


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
            "config/write",
            ConfigWriteParams(
                session_id=agent_loop.session_id,
                ops=[
                    ConfigWriteOpWire(
                        op="set", path="/disable_welcome_banner_animation", value=True
                    ),
                    ConfigWriteOpWire(
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

        assert legacy_backend(server).session.turns.callbacks == []
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
async def test_initialize_requires_initialized_notification() -> None:
    agent_loop = build_test_agent_loop()
    client = start_test_app_server(agent_loop)

    try:
        response = await client.initialize(
            ClientInfo(name="test", version="1"),
            ClientCapabilities(callback_kinds=["approval", "user_input"]),
        )
        assert response.model_dump(mode="json") == {
            "serverInfo": {
                "name": "vibe-app-server",
                "version": response.server_info.version,
            }
        }

        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "session/read", _wire_read_request(agent_loop.session_id)
            )
        assert exc_info.value.error.code is ProtocolErrorCode.NOT_INITIALIZED

        await client.notify("initialized")
        await client.request("session/start", SessionStartParams())
        public = validate_wire(
            SessionReadResponse,
            await client.request(
                "session/read", _wire_read_request(agent_loop.session_id)
            ),
        )
        assert public.state.session.id == agent_loop.session_id
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
async def test_ready_wait_response_carries_init_duration_ms() -> None:
    agent_loop = build_test_agent_loop(defer_heavy_init=True)
    client = start_test_app_server(agent_loop)
    session = await attach_test_app_server_session(client)
    try:
        raw = await client.request(
            "session/ready/wait", SessionReadyWaitParams(session_id=session.session_id)
        )
        response = validate_wire(SessionReadyWaitResponse, raw)

        assert response.init_duration_ms is not None
        assert isinstance(response.init_duration_ms, int)
        assert response.init_duration_ms >= 0
        assert response.init_duration_ms == agent_loop.init_duration_ms
    finally:
        await session.close()
        await client.close()
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
async def test_auto_compaction_keeps_active_turn_in_same_session() -> None:
    agent_loop = build_test_agent_loop()
    agent_loop.stats.session_prompt_tokens = 11
    agent_loop.stats.session_completion_tokens = 7
    original_session_id = agent_loop.session_id

    async def compacting_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="user-1")
        yield CompactStartEvent(
            current_context_tokens=100, threshold=90, tool_call_id="compact-1"
        )
        agent_loop.stats.session_prompt_tokens += 2
        agent_loop.stats.session_completion_tokens += 1
        yield CompactEndEvent(
            tool_call_id="compact-1",
            summary_length=12,
            old_session_id=original_session_id,
            new_session_id=original_session_id,
        )
        yield AssistantEvent(content="continued", message_id="assistant-1")

    agent_loop.act = compacting_act
    session = await create_test_app_server_session(agent_loop)
    try:
        await _consume(session.act("continue"))
    finally:
        await session.close()

    assert session.session_id == original_session_id
    assert any(
        isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
        for entry in session.history
    )
    assert all(entry.session_id == original_session_id for entry in session.history)
    assert session.state.latest_turn is not None
    assert session.state.latest_turn.session_id == original_session_id
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
async def test_interrupt_routes_during_compaction_without_session_handoff() -> None:
    agent_loop = build_test_agent_loop()
    original_session_id = agent_loop.session_id
    transitioned = asyncio.Event()

    async def compacting_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="user-1")
        yield CompactStartEvent(
            current_context_tokens=100, threshold=90, tool_call_id="compact-1"
        )
        yield CompactEndEvent(
            tool_call_id="compact-1",
            summary_length=12,
            old_session_id=original_session_id,
            new_session_id=original_session_id,
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
        await turn
    finally:
        turn.cancel()
        await session.close()

    assert response["accepted"] is True
    assert isinstance(response["lastEventId"], int)
    assert any(
        isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
        for entry in session.history
    )
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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_call", [1, 2], ids=["legacy-runtime", "app-server"])
async def test_root_replacement_keeps_serving_when_previous_shutdown_fails(
    monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    """*Prepare*: Previous-backend shutdown fails in either replacement cleanup layer.
    *Do*: Attach a fork that replaces the active root.
    *Assert*: The replacement remains active and continues serving requests.
    """
    # Prepare
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)
    original_shutdown = LegacySessionBackend.shutdown
    call_count = 0

    async def failing_shutdown(self: LegacySessionBackend) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == failure_call:
            raise RuntimeError("disk full — shutdown failed")
        await original_shutdown(self)

    monkeypatch.setattr(LegacySessionBackend, "shutdown", failing_shutdown)

    try:
        # Do
        await session.resources.sessions.fork(attach=True)
        await session.resources.refresh()

        # Assert
        assert session.session_id != agent_loop.session_id
        await session.clear_history()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_install_root_restores_previous_backend_when_replacement_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)
    original_initialize = type(agent_loop).start_initialize_experiments

    def fail_replacement_initialize(self, **kwargs: Any) -> None:
        if self is not agent_loop:
            raise RuntimeError("replacement setup failed")
        original_initialize(self, **kwargs)

    monkeypatch.setattr(
        type(agent_loop), "start_initialize_experiments", fail_replacement_initialize
    )

    try:
        with pytest.raises(AppServerResponseError):
            await session.resources.sessions.fork(attach=True)

        await session.resources.refresh()
        assert session.session_id == agent_loop.session_id
        await session.clear_history()
    finally:
        await session.close()


def _interrupt_session() -> AppServerSession:
    session = object.__new__(AppServerSession)
    session._state = cast(Any, SimpleNamespace(session_id="session-1"))
    session._active_public_turn_id = Mock(return_value="turn-1")
    return cast(AppServerSession, session)


class _InterruptClient:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self._result = result
        self.requests: list[dict[str, Any]] = []

    async def request(
        self, method: str, params: Any, *, wait_for_incoming: bool = False
    ) -> dict[str, Any]:
        self.requests.append({
            "method": method,
            "session_id": params.session_id,
            "expected_turn_id": params.expected_turn_id,
            "wait_for_incoming": wait_for_incoming,
        })
        if isinstance(self._result, Exception):
            raise self._result
        return self._result
