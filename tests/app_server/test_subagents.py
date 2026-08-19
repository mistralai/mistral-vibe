from __future__ import annotations

import asyncio
from contextlib import suppress
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_config,
    make_test_models,
    set_agent_config,
)
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import (
    attach_test_app_server_session,
    create_test_app_server_session,
    legacy_backend,
    start_test_app_server,
)
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server._model import validate_wire
from vibe.app_server._runtime import AgentRuntimeFactory
from vibe.app_server._sessions import SessionRuntimeRegistry
from vibe.app_server._turns import TurnController
from vibe.app_server.events import CallbackRequested
from vibe.app_server.models import (
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    CompletedEffectState,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicMessageEntry,
    PublicSessionState,
    SubagentEffectDetail,
    UserAnswer,
    UserInputCallbackOutput,
    UserQuestionResult,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolErrorCode,
    SessionReadParams,
    SessionReadResponse,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_loader import MESSAGES_FILENAME
from vibe.core.tools.models import ToolPermission
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall
from vibe.utils.tool_presentation import (
    EffectCallDisplay,
    ToolCallPresentation,
    ToolEffectKind,
)


def _task_call(agent: str = "explore") -> ToolCall:
    return ToolCall(
        id="task-1",
        index=0,
        function=FunctionCall(
            name="task",
            arguments=json.dumps({"task": "Inspect the project", "agent": agent}),
        ),
    )


def _config(session_logging: SessionLoggingConfig | None = None):
    return build_test_vibe_config(
        enabled_tools=["task"],
        tools={"task": {"permission": ToolPermission.ALWAYS.value}},
        session_logging=session_logging or SessionLoggingConfig(enabled=False),
    )


class BlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopped = asyncio.Event()

    async def complete(self, **_kwargs):
        self.started.set()
        try:
            await self.release.wait()
            return mock_llm_chunk(content="")
        finally:
            self.stopped.set()

    async def complete_streaming(self, **kwargs):
        yield await self.complete(**kwargs)


async def _consume(events) -> None:
    _ = [event async for event in events]


async def _read_child(
    session: AppServerSession, child_session_id: str
) -> PublicSessionState:
    client = session._connection.current
    assert client is not None
    response = validate_wire(
        SessionReadResponse,
        await client.request(
            "session/read", SessionReadParams(session_id=child_session_id)
        ),
    )
    return response.state


async def _persist_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SessionLoggingConfig, str, str, Path]:
    logging = SessionLoggingConfig(
        enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
    )
    parent_backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[_task_call()])],
        [mock_llm_chunk(content="Parent completed")],
    ])
    monkeypatch.setattr(
        "vibe.core.agent_loop._loop.create_backend",
        lambda **_: FakeBackend([mock_llm_chunk(content="Child completed")]),
    )
    parent = build_test_agent_loop(
        config=_config(logging), backend=parent_backend, enable_streaming=True
    )
    session = await create_test_app_server_session(parent)
    try:
        _ = [event async for event in session.act("Delegate this")]
        metadata = parent.session_logger.session_metadata
        parent_dir = parent.session_logger.session_dir
        assert metadata is not None
        assert parent_dir is not None
        link = metadata.child_sessions[0]
        assert link.relative_path is not None
        return (
            logging,
            session.session_id,
            link.session_id,
            parent_dir / link.relative_path,
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_root_handoff_updates_live_child_parent_routing() -> None:
    parent = build_test_agent_loop(config=_config())
    registry = SessionRuntimeRegistry(AsyncMock(), AsyncMock(), lambda _: 0)
    child = await AgentRuntimeFactory().create_child(parent, "explore")
    assert child.enable_streaming is False
    runtime = registry._build_child_runtime(child)
    registry._children[child.session_id] = runtime
    old_parent_id = parent.session_id
    new_parent_id = f"{old_parent_id}-compacted"

    registry.handoff_root(old_parent_id, new_parent_id)

    assert registry.child_belongs_to(child.session_id, new_parent_id)
    assert not registry.child_belongs_to(child.session_id, old_parent_id)
    await registry.close()
    await parent.aclose()


@pytest.mark.asyncio
async def test_resumed_child_restores_cumulative_stats(tmp_path: Path) -> None:
    logging = SessionLoggingConfig(
        enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
    )
    parent = build_test_agent_loop(config=_config(logging))
    await parent.persist_empty_session()
    factory = AgentRuntimeFactory()
    child = await factory.create_child(parent, "explore")
    await child.wait_until_ready()
    child.stats.session_prompt_tokens = 11
    child.stats.session_completion_tokens = 7
    child.stats.context_tokens = 18
    await child.persist_empty_session()
    child_session_id = child.session_id
    child_session_dir = child.session_logger.session_dir
    assert child_session_dir is not None
    await child.aclose()

    resumed = await factory.resume_child(
        parent, "explore", child_session_id, child_session_dir
    )
    try:
        assert resumed.stats.session_prompt_tokens == 11
        assert resumed.stats.session_completion_tokens == 7
        assert resumed.stats.context_tokens == 18
    finally:
        await resumed.aclose()
        await parent.aclose()


@pytest.mark.asyncio
async def test_task_creates_independently_readable_child_session(monkeypatch) -> None:
    parent_backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[_task_call()])],
        [mock_llm_chunk(content="Parent completed")],
    ])
    child_backend = FakeBackend([mock_llm_chunk(content="Child completed")])
    monkeypatch.setattr(
        "vibe.core.agent_loop._loop.create_backend", lambda **_: child_backend
    )
    parent = build_test_agent_loop(
        config=_config(), backend=parent_backend, enable_streaming=True
    )
    session = await create_test_app_server_session(parent)

    try:
        _ = [event async for event in session.act("Delegate this")]
        effect = next(
            entry
            for entry in session.history
            if isinstance(entry, PublicEffectEntry)
            and entry.detail.kind is ToolEffectKind.SUBAGENT
        )
        assert isinstance(effect.detail, SubagentEffectDetail)
        child_session_id = effect.detail.child_session_id
        assert child_session_id is not None

        child = await _read_child(session, child_session_id)
        assert child.session.id == child_session_id
        assert child.session.parent_session_id == session.session_id
        assert child.session.root_session_id == session.session_id
        assert child.event_id > 0
        messages = [
            entry.text
            for entry in child.history or []
            if isinstance(entry, PublicMessageEntry)
        ]
        assert messages[0].endswith("Inspect the project")
        assert messages[1] == "Child completed"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_child_auto_compaction_keeps_live_and_persisted_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logging = SessionLoggingConfig(
        enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
    )
    parent_backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[_task_call()])],
        [mock_llm_chunk(content="Parent completed")],
    ])
    child_backend = FakeBackend([
        [mock_llm_chunk(content="<summary>Child summary</summary>")],
        [mock_llm_chunk(content="Child completed")],
    ])
    monkeypatch.setattr(
        "vibe.core.agent_loop._loop.create_backend", lambda **_: child_backend
    )
    original_child_ids: list[str] = []
    create_child = AgentRuntimeFactory.create_child

    async def create_compacting_child(
        factory: AgentRuntimeFactory, loop: AgentLoop, agent_name: str, **kwargs
    ) -> AgentLoop:
        child = await create_child(factory, loop, agent_name, **kwargs)
        original_child_ids.append(child.session_id)
        set_agent_config(
            child,
            child.config.model_copy(
                update={
                    "models": {
                        model.alias: model
                        for model in make_test_models(auto_compact_threshold=1)
                    }
                }
            ),
        )
        child.stats.context_tokens = 2
        return child

    monkeypatch.setattr(AgentRuntimeFactory, "create_child", create_compacting_child)
    parent = build_test_agent_loop(
        config=_config(logging), backend=parent_backend, enable_streaming=True
    )
    client = start_test_app_server(parent)
    session = await attach_test_app_server_session(client)

    try:
        _ = [event async for event in session.act("Delegate this")]
        assert len(original_child_ids) == 1
        original_child_id = original_child_ids[0]
        effect = next(
            entry
            for entry in session.history
            if isinstance(entry, PublicEffectEntry)
            and entry.detail.kind is ToolEffectKind.SUBAGENT
        )
        assert isinstance(effect.detail, SubagentEffectDetail)
        child_session_id = effect.detail.child_session_id
        assert child_session_id is not None
        assert child_session_id == original_child_id
        assert isinstance(effect.state, CompletedEffectState)

        child_state = await _read_child(session, child_session_id)
        assert child_state.session.parent_session_id == session.session_id
        assert child_state.session.root_session_id == session.session_id
        assert child_state.history is not None
        assert any(
            isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
            for entry in child_state.history
        )
        assert any(
            isinstance(entry, PublicMessageEntry) and entry.text == "Child completed"
            for entry in child_state.history
        )
        metadata = parent.session_logger.session_metadata
        assert metadata is not None
        assert len(metadata.child_sessions) == 1
        assert metadata.child_sessions[0].session_id == child_session_id
        parent_session_id = session.session_id
    finally:
        await session.close()

    resumed_loop = build_test_agent_loop(
        config=_config(logging), backend=FakeBackend(), enable_streaming=True
    )
    resumed = await attach_test_app_server_session(
        start_test_app_server(resumed_loop), resume_session_id=parent_session_id
    )
    try:
        resumed_effect = next(
            entry
            for entry in resumed.history
            if isinstance(entry, PublicEffectEntry)
            and entry.detail.kind is ToolEffectKind.SUBAGENT
        )
        assert isinstance(resumed_effect.detail, SubagentEffectDetail)
        assert resumed_effect.detail.child_session_id == child_session_id
        child_state = await _read_child(resumed, child_session_id)
        assert child_state.session.parent_session_id == parent_session_id
        assert child_state.session.root_session_id == parent_session_id
        assert child_state.history is not None
        assert any(
            isinstance(entry, PublicCheckpointEntry) and entry.kind == "compaction"
            for entry in child_state.history
        )
    finally:
        await resumed.close()


@pytest.mark.asyncio
async def test_child_registration_rolls_back_when_projection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logging = SessionLoggingConfig(
        enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
    )
    parent_backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[_task_call()])],
        [mock_llm_chunk(content="Parent completed")],
    ])
    monkeypatch.setattr(
        "vibe.core.agent_loop._loop.create_backend",
        lambda **_: FakeBackend([mock_llm_chunk(content="Child completed")]),
    )

    async def fail_link(
        _turns: TurnController, _tool_call_id: str, _child_session_id: str
    ) -> None:
        raise RuntimeError("projection failed")

    monkeypatch.setattr(TurnController, "link_subagent", fail_link)
    parent = build_test_agent_loop(
        config=_config(logging), backend=parent_backend, enable_streaming=True
    )
    client = start_test_app_server(parent)
    run_peer = client._run_peer
    assert run_peer is not None
    server = cast(AppServer, cast(Any, run_peer).__self__)
    session = await attach_test_app_server_session(client)

    try:
        _ = [event async for event in session.act("Delegate this")]
        metadata = parent.session_logger.session_metadata
        parent_dir = parent.session_logger.session_dir
        assert metadata is not None
        assert parent_dir is not None
        assert metadata.child_sessions == []
        assert legacy_backend(server).children._children == {}
        agents_dir = parent_dir / "agents"
        assert not agents_dir.exists() or not any(agents_dir.iterdir())
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_child_callbacks_round_trip_using_child_session_id(
    tmp_path, monkeypatch
) -> None:
    (tmp_path / "interactive.toml").write_text(
        "\n".join([
            'agent_type = "subagent"',
            'enabled_tools = ["todo", "ask_user_question"]',
        ]),
        encoding="utf-8",
    )
    todo_call = ToolCall(
        id="todo-1",
        index=0,
        function=FunctionCall(name="todo", arguments='{"action":"read"}'),
    )
    question_call = ToolCall(
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
    child_backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[todo_call])],
        [mock_llm_chunk(content="", tool_calls=[question_call])],
        [mock_llm_chunk(content="Child completed")],
    ])
    monkeypatch.setattr(
        "vibe.core.agent_loop._loop.create_backend", lambda **_: child_backend
    )
    parent_backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[_task_call("interactive")])],
        [mock_llm_chunk(content="Parent completed")],
    ])
    config = build_test_vibe_config(
        agent_paths=[tmp_path],
        enabled_tools=["task", "todo", "ask_user_question"],
        tools={
            "task": {"permission": ToolPermission.ALWAYS.value},
            "todo": {"permission": ToolPermission.ASK.value},
        },
    )
    parent = build_test_agent_loop(
        config=config, backend=parent_backend, enable_streaming=True
    )
    session = await create_test_app_server_session(parent)
    callbacks = []

    try:
        async for event in session.act("Delegate this"):
            if not isinstance(event, CallbackRequested):
                continue
            callbacks.append(event.callback)
            match event.callback.detail.kind:
                case "approval":
                    output = ApprovalCallbackOutput(
                        decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
                    )
                case "user_input":
                    output = UserInputCallbackOutput(
                        result=UserQuestionResult(
                            answers=[UserAnswer(question="Ship it?", answer="Yes")]
                        )
                    )
            await session.respond_to_callback(event.callback.callback_id, output)

        assert callbacks, session.history
        assert {callback.detail.kind for callback in callbacks} == {
            "approval",
            "user_input",
        }
        child_session_ids = {callback.session_id for callback in callbacks}
        assert len(child_session_ids) == 1
        child_session_id = child_session_ids.pop()
        assert child_session_id != session.session_id

        child = await _read_child(session, child_session_id)
        child_effects = [
            entry
            for entry in child.history or []
            if isinstance(entry, PublicEffectEntry)
        ]
        assert {effect.detail.tool_name for effect in child_effects} == {
            "todo",
            "ask_user_question",
        }
        assert all(
            isinstance(effect.state, CompletedEffectState) for effect in child_effects
        )
        question = next(
            effect
            for effect in child_effects
            if effect.detail.tool_name == "ask_user_question"
        )
        assert isinstance(question.state, CompletedEffectState)
        assert isinstance(question.state.output, dict)
        assert question.state.output["answers"] == [
            {"question": "Ship it?", "answer": "Yes", "isOther": False}
        ]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_interrupting_task_leaves_child_idle_and_closes_runtime(
    monkeypatch,
) -> None:
    child_backend = BlockingBackend()
    monkeypatch.setattr(
        "vibe.core.agent_loop._loop.create_backend", lambda **_: child_backend
    )
    parent_backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[_task_call()])]
    ])
    parent = build_test_agent_loop(
        config=_config(), backend=parent_backend, enable_streaming=True
    )
    client = start_test_app_server(parent)
    run_peer = client._run_peer
    assert run_peer is not None
    server = cast(AppServer, cast(Any, run_peer).__self__)
    session = await attach_test_app_server_session(client)
    turn = asyncio.create_task(_consume(session.act("Delegate this")))

    try:
        await child_backend.started.wait()
        await session.interrupt()
        await asyncio.wait_for(turn, timeout=1)
        await asyncio.wait_for(child_backend.stopped.wait(), timeout=1)

        children = legacy_backend(server).children
        child_runtimes = children._children
        assert len(child_runtimes) == 1
        child_session_id, runtime = next(iter(child_runtimes.items()))
        assert runtime.turns.active_turn is None
        assert runtime.execution.active is None
        assert runtime.turns._active_task is not None
        assert runtime.turns._active_task.done()
        assert not [
            task
            for task in asyncio.all_tasks()
            if not task.done() and task.get_name().startswith("vibe-subagent-")
        ]

        child = await _read_child(session, child_session_id)
        assert child.session.status.type == "idle"
    finally:
        turn.cancel()
        await session.close()

    assert children._children == {}


@pytest.mark.asyncio
async def test_resuming_parent_restores_child_link_and_public_history(
    tmp_path, monkeypatch
) -> None:
    logging, parent_session_id, child_session_id, _ = await _persist_child(
        tmp_path, monkeypatch
    )

    resumed_loop = build_test_agent_loop(
        config=_config(logging), backend=FakeBackend(), enable_streaming=True
    )
    resumed = await attach_test_app_server_session(
        start_test_app_server(resumed_loop), resume_session_id=parent_session_id
    )
    try:
        resumed_effect = next(
            entry
            for entry in resumed.history
            if isinstance(entry, PublicEffectEntry)
            and entry.detail.kind is ToolEffectKind.SUBAGENT
        )
        assert isinstance(resumed_effect.detail, SubagentEffectDetail)
        assert resumed_effect.detail.child_session_id == child_session_id
        child = await _read_child(resumed, child_session_id)
        assert child.session.parent_session_id == parent_session_id
        assert any(
            isinstance(entry, PublicMessageEntry) and entry.text == "Child completed"
            for entry in child.history or []
        )
    finally:
        await resumed.close()


@pytest.mark.asyncio
async def test_corrupt_child_log_does_not_fail_parent_resume(
    tmp_path, monkeypatch
) -> None:
    logging, parent_session_id, child_session_id, child_dir = await _persist_child(
        tmp_path, monkeypatch
    )

    (child_dir / MESSAGES_FILENAME).write_text("{not-json", encoding="utf-8")
    resumed_loop = build_test_agent_loop(
        config=_config(logging), backend=FakeBackend(), enable_streaming=True
    )
    created_children = []
    create_child = AgentRuntimeFactory.create_child

    async def track_child(
        factory: AgentRuntimeFactory, loop: AgentLoop, agent_name: str, **kwargs
    ) -> AgentLoop:
        child = await create_child(factory, loop, agent_name, **kwargs)
        created_children.append(child)
        return child

    monkeypatch.setattr(AgentRuntimeFactory, "create_child", track_child)
    resumed = await attach_test_app_server_session(
        start_test_app_server(resumed_loop), resume_session_id=parent_session_id
    )
    try:
        assert created_children == []
        effect = next(
            entry
            for entry in resumed.history
            if isinstance(entry, PublicEffectEntry)
            and entry.detail.kind is ToolEffectKind.SUBAGENT
        )
        assert isinstance(effect.detail, SubagentEffectDetail)
        assert effect.detail.child_session_id == child_session_id
        with pytest.raises(AppServerResponseError) as exc_info:
            await _read_child(resumed, child_session_id)
        assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
    finally:
        await resumed.close()


@pytest.mark.asyncio
async def test_repeated_reads_of_broken_child_stay_not_found(
    tmp_path, monkeypatch
) -> None:
    logging, parent_session_id, child_session_id, child_dir = await _persist_child(
        tmp_path, monkeypatch
    )

    (child_dir / MESSAGES_FILENAME).write_text("{not-json", encoding="utf-8")
    resumed_loop = build_test_agent_loop(
        config=_config(logging), backend=FakeBackend(), enable_streaming=True
    )
    created_children: list[AgentLoop] = []
    create_child = AgentRuntimeFactory.create_child

    async def track_child(
        factory: AgentRuntimeFactory, loop: AgentLoop, agent_name: str, **kwargs
    ) -> AgentLoop:
        child = await create_child(factory, loop, agent_name, **kwargs)
        created_children.append(child)
        return child

    monkeypatch.setattr(AgentRuntimeFactory, "create_child", track_child)
    resumed = await attach_test_app_server_session(
        start_test_app_server(resumed_loop), resume_session_id=parent_session_id
    )
    try:
        for _ in range(3):
            with pytest.raises(AppServerResponseError) as exc_info:
                await _read_child(resumed, child_session_id)
            assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
        assert created_children == []
    finally:
        await resumed.close()


@pytest.mark.asyncio
async def test_unprojectable_child_tool_call_is_tolerated_on_resume(
    tmp_path, monkeypatch
) -> None:
    logging, parent_session_id, child_session_id, child_dir = await _persist_child(
        tmp_path, monkeypatch
    )

    # A stored tool call whose arguments no longer match its presentation model.
    # Projection must degrade gracefully rather than discard the whole child.
    invalid_message = LLMMessage(
        role=Role.assistant,
        tool_calls=[
            ToolCall(
                id="invalid-task",
                index=0,
                function=FunctionCall(name="task", arguments="{}"),
                presentation=ToolCallPresentation(
                    kind=ToolEffectKind.FILE_READ,
                    display=EffectCallDisplay(summary="invalid", status_text="invalid"),
                ),
            )
        ],
    )
    with (child_dir / MESSAGES_FILENAME).open("a", encoding="utf-8") as messages:
        messages.write(json.dumps(invalid_message.model_dump(mode="json")) + "\n")

    resumed_loop = build_test_agent_loop(
        config=_config(logging), backend=FakeBackend(), enable_streaming=True
    )
    close_calls: list[tuple[AsyncMock, AsyncMock]] = []
    create_child = AgentRuntimeFactory.create_child

    async def track_child(
        factory: AgentRuntimeFactory, loop: AgentLoop, agent_name: str, **kwargs
    ) -> AgentLoop:
        child = await create_child(factory, loop, agent_name, **kwargs)
        close = AsyncMock(wraps=child.aclose)
        telemetry_close = AsyncMock(wraps=child.telemetry_client.aclose)
        monkeypatch.setattr(child, "aclose", close)
        monkeypatch.setattr(child.telemetry_client, "aclose", telemetry_close)
        close_calls.append((close, telemetry_close))
        return child

    monkeypatch.setattr(AgentRuntimeFactory, "create_child", track_child)
    resumed = await attach_test_app_server_session(
        start_test_app_server(resumed_loop), resume_session_id=parent_session_id
    )
    try:
        # The child is created lazily on first read, then kept (not discarded)
        # because projection degrades gracefully, and stays readable.
        with suppress(Exception):
            await _read_child(resumed, child_session_id)
        assert len(close_calls) == 1
        close, _telemetry_close = close_calls[0]
        close.assert_not_awaited()
        child = await _read_child(resumed, child_session_id)
        assert child.session.parent_session_id == parent_session_id
        assert any(
            isinstance(entry, PublicEffectEntry)
            and entry.detail.kind is ToolEffectKind.TOOL
            for entry in child.history or []
        )
    finally:
        await resumed.close()


@pytest.mark.asyncio
async def test_closing_children_attempts_every_runtime_before_raising() -> None:
    registry = SessionRuntimeRegistry(AsyncMock(), AsyncMock(), lambda _session_id: 0)
    failure = RuntimeError("first child failed to close")
    closed = []
    first = MagicMock()
    second = MagicMock()
    third = MagicMock()

    def close_runtime(runtime, error: Exception | None = None) -> None:
        closed.append(runtime)
        if error is not None:
            raise error

    first.close = AsyncMock(side_effect=lambda: close_runtime(first, failure))
    second.close = AsyncMock(side_effect=lambda: close_runtime(second))
    third.close = AsyncMock(side_effect=lambda: close_runtime(third))
    registry._children.update({
        "first": cast(Any, first),
        "second": cast(Any, second),
        "third": cast(Any, third),
    })

    with pytest.raises(RuntimeError) as exc_info:
        await registry.close()

    assert exc_info.value is failure
    assert closed == [first, second, third]
    assert registry._children == {}
