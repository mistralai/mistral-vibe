"""A root resumed on a new Host lists a subagent it left running, with the status of its own Session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, cast

import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionFinishReason,
    RustCompletionResult,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustTextContentBlock,
    RustTokenUsage,
)
from mistralai_vibe_local_harness.vibe import _local_actions
from tests.app_server._session_api_harness import api_host
from tests.stubs.subagent_programs import spawn_only
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedHarnessBackendAdapter,
    UnifiedHarnessBackendHostAdapter,
)
from vibe.app_server.events import TurnCompleted
from vibe.app_server.models import PublicChildSession, TextContentBlock
from vibe.app_server.protocol import (
    PageRequest,
    SessionOptions,
    SessionReadParams,
    SessionResumeParams,
    SessionStartParams,
    TurnStartParams,
)

USAGE = RustTokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
SUBAGENT = "researcher"
AGENT_TYPE = "reviewer"
REVIEWER = """\
description = "Reviews a diff and reports what it found"
agent_type = "subagent"
enabled_tools = ["read_file"]
"""


class _Model:
    """The root spawns subagent "researcher" of type "reviewer", whose Turn
    never ends.
    """

    def __init__(self) -> None:
        self.root_completions = 0
        self.never = asyncio.Event()

    async def __call__(
        self, action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        if action.turn_id is not None and action.turn_id.startswith("child-"):
            await self.never.wait()
        self.root_completions += 1
        if self.root_completions > 1:
            return _answer(action, [RustTextContentBlock(text="done")])
        # The program helper has no agent type argument, and a named agent type
        # brings its own instructions, so the spawn gives none.
        typed = spawn_only(SUBAGENT, "Look").replace(
            'instructions: "Follow the message.", message:',
            f"config_name: {json.dumps(AGENT_TYPE)}, message:",
            1,
        )
        spawn = RustCompletionResultToolCallPart(
            id="program-1",
            name="run_typescript",
            arguments_json=json.dumps({"code": typed}),
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


def _options(tmp_path: Path) -> SessionOptions:
    return SessionOptions(cwd=str(tmp_path), auto_approve=True, trust_workspace=True)


async def _host(
    tmp_path: Path, process_services: ProcessServices
) -> UnifiedHarnessBackendHostAdapter:
    """``api_host``, loading project files, so the "reviewer" type binds."""
    agents = tmp_path / ".vibe" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / f"{AGENT_TYPE}.toml").write_text(REVIEWER, encoding="utf-8")
    return await api_host(
        tmp_path,
        _options(tmp_path),
        process_services=process_services,
        sources=("project",),
    )


@dataclass
class _Stored:
    root_id: str
    child_id: str
    child_state: str


async def _store_root_with_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> _Stored:
    """Run a root Turn that spawns the subagent until the root's Turn ends, then
    shut the Host down.
    """
    monkeypatch.setattr(_local_actions, "execute_completion", _Model())
    host = await _host(tmp_path, process_services)
    try:
        started = await host.start(SessionStartParams(agent_config=_options(tmp_path)))
        adapter = cast(UnifiedHarnessBackendAdapter, started.backend)
        root_id = adapter.session_id
        subscription = await adapter.subscribe(
            SessionReadParams(session_id=root_id, history=PageRequest(limit=100))
        )
        await adapter.start_turn(
            TurnStartParams(
                session_id=root_id, message=[TextContentBlock(text="Delegate")]
            )
        )
        events = cast(AsyncIterator[Any], subscription.events)
        async with asyncio.timeout(15):
            async for backend_event in events:
                if isinstance(backend_event.event, TurnCompleted):
                    break
        await cast(Any, events).aclose()
        [record] = host._host.child_session_records(root_id)
        return _Stored(root_id, record.child_session_id, record.state.type)
    finally:
        await host.shutdown()


@asynccontextmanager
async def _resumed(
    tmp_path: Path, root_id: str, process_services: ProcessServices
) -> AsyncIterator[UnifiedHarnessBackendAdapter]:
    """Resume the root on a new Host over the same storage."""
    host = await _host(tmp_path, process_services)
    try:
        resumed = await host.resume(
            SessionResumeParams(session_id=root_id, agent_config=_options(tmp_path))
        )
        yield cast(UnifiedHarnessBackendAdapter, resumed.backend)
    finally:
        await host.shutdown()


def _root_read(adapter: UnifiedHarnessBackendAdapter) -> SessionReadParams:
    return SessionReadParams(
        session_id=adapter.session_id, history=PageRequest(limit=100)
    )


async def _listed(
    adapter: UnifiedHarnessBackendAdapter, *, settled_status: str | None = None
) -> tuple[list[PublicChildSession], list[PublicChildSession]]:
    """The subagents a root read lists, then those a subscription lists.

    With ``settled_status``, list again until every listed subagent has that
    status. A resumed subagent recovers in the background, and a read shows
    only the state it has announced so far.
    """
    async with asyncio.timeout(5):
        while True:
            read = await adapter.read(_root_read(adapter))
            subscription = await adapter.subscribe(_root_read(adapter))
            await cast(Any, subscription.events).aclose()
            listed = (
                read.state.child_sessions,
                subscription.snapshot.state.child_sessions,
            )
            if settled_status is None or all(
                child.status.type == settled_status
                for children in listed
                for child in children
            ):
                return listed
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_resumed_root_lists_a_subagent_it_left_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A stored root whose subagent "researcher" of type "reviewer" was
        still running when the first Host shut down, which cancelled its Turn.
    *Do*: Resume the root on a new Host, read it, then subscribe to it.
    *Assert*: The stored record still says running, but the read and the
        subscription list the subagent with its name, its type and the failed
        status of its own Session, and the root references it.
    """
    # Prepare
    stored = await _store_root_with_subagent(tmp_path, monkeypatch, process_services)
    assert stored.child_state == "running"

    async with _resumed(tmp_path, stored.root_id, process_services) as adapter:
        # Do
        read, subscribed = await _listed(adapter, settled_status="failed")

        # Assert
        for listed in (read, subscribed):
            assert [
                (child.id, child.name, child.agent_type, child.status.type)
                for child in listed
            ] == [(stored.child_id, SUBAGENT, AGENT_TYPE, "failed")]
        assert adapter.references_child(stored.child_id)
