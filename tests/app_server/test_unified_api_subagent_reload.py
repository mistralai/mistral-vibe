"""A subagent profile written mid-session can be spawned after ``config/reload``."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
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
from vibe.app_server.models import TextContentBlock
from vibe.app_server.protocol import (
    ConfigReloadParams,
    PageRequest,
    SessionOptions,
    SessionReadParams,
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
    """Each root Turn spawns subagent "researcher" of type "reviewer" once.

    The subagent answers at once, and the root ends its Turn on the spawn's
    result, which ``spawned`` keeps.
    """

    def __init__(self) -> None:
        self.spawned: list[str] = []
        # Set to hold the first root completion until the test releases it, so
        # a turn opened mid-reload is still running when the reload rebinds.
        self.hold_first_completion: asyncio.Event | None = None

    async def __call__(
        self, action: Any, messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        hold = self.hold_first_completion
        if (
            hold is not None
            and not hold.is_set()
            and not str(getattr(action, "turn_id", "") or "").startswith("child-")
            and messages[-1].role != "tool"
        ):
            await hold.wait()
        if action.turn_id is not None and action.turn_id.startswith("child-"):
            return _answer(action, [RustTextContentBlock(text="reviewed")])
        last = messages[-1]
        if last.role == "tool":
            self.spawned.append(json.dumps(last.model_dump(mode="json")))
            return _answer(action, [RustTextContentBlock(text="done")])
        # A named agent type brings its own instructions, so the spawn gives none.
        typed = spawn_only(SUBAGENT, "Look").replace(
            'instructions: "Follow the message.", message:',
            f"config_name: {json.dumps(AGENT_TYPE)}, message:",
            1,
        )
        spawn = RustCompletionResultToolCallPart(
            id=f"program-{len(self.spawned)}",
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


@pytest.fixture(autouse=True)
def _no_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reload refetches the connector catalog over the network: keep it off."""
    monkeypatch.setenv("VIBE_ENABLE_CONNECTORS", "false")


def _options(tmp_path: Path) -> SessionOptions:
    return SessionOptions(cwd=str(tmp_path), trust_workspace=True)


async def _host(
    tmp_path: Path, process_services: ProcessServices
) -> UnifiedHarnessBackendHostAdapter:
    """``api_host``, loading project files, with no agent file written yet."""
    (tmp_path / ".vibe" / "agents").mkdir(parents=True, exist_ok=True)
    return await api_host(
        tmp_path,
        _options(tmp_path),
        process_services=process_services,
        sources=("project",),
    )


async def _run_turn(adapter: UnifiedHarnessBackendAdapter) -> None:
    subscription = await adapter.subscribe(
        SessionReadParams(session_id=adapter.session_id, history=PageRequest(limit=100))
    )
    events = cast(AsyncIterator[Any], subscription.events)
    try:
        started = await adapter.start_turn(
            TurnStartParams(
                session_id=adapter.session_id,
                message=[TextContentBlock(text="Delegate")],
            )
        )
        turn_id = started.response.turn.id
        async with asyncio.timeout(15):
            async for backend_event in events:
                event = backend_event.event
                if isinstance(event, TurnCompleted) and event.turn.id == turn_id:
                    return
    finally:
        await cast(Any, events).aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("promoted", [False, True], ids=["fresh", "after-a-turn"])
async def test_a_subagent_written_mid_session_spawns_after_a_config_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    promoted: bool,
) -> None:
    """*Prepare*: A session started before any agent file exists, fresh or past
        a Turn whose spawn of type "reviewer" failed as unknown, then the
        "reviewer" subagent file written.
    *Do*: Reload the config, then run a Turn that spawns a "reviewer".
    *Assert*: The subagent is admitted with type "reviewer", without a restart.
    """
    # Prepare
    model = _Model()
    monkeypatch.setattr(_local_actions, "execute_completion", model)
    host = await _host(tmp_path, process_services)
    try:
        started = await host.start(SessionStartParams(agent_config=_options(tmp_path)))
        adapter = cast(UnifiedHarnessBackendAdapter, started.backend)
        if promoted:
            await _run_turn(adapter)
            assert "subagent_type_not_found" in model.spawned[-1]
        (tmp_path / ".vibe" / "agents" / f"{AGENT_TYPE}.toml").write_text(
            REVIEWER, encoding="utf-8"
        )

        # Do
        await adapter.reload_config(ConfigReloadParams(session_id=adapter.session_id))
        await _run_turn(adapter)

        # Assert
        assert "subagent_type_not_found" not in model.spawned[-1]
        [record] = host._host.child_session_records(adapter.session_id)
        assert (record.agent_name, record.agent_type) == (SUBAGENT, AGENT_TYPE)
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_a_subagent_removed_mid_session_is_unknown_after_a_config_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A session started with the "reviewer" subagent file, which is
        then removed.
    *Do*: Reload the config, then run a Turn that spawns a "reviewer".
    *Assert*: The spawn fails as an unknown type: the reload dropped it.
    """
    # Prepare
    model = _Model()
    monkeypatch.setattr(_local_actions, "execute_completion", model)
    agent_file = tmp_path / ".vibe" / "agents" / f"{AGENT_TYPE}.toml"
    agent_file.parent.mkdir(parents=True)
    agent_file.write_text(REVIEWER, encoding="utf-8")
    host = await _host(tmp_path, process_services)
    try:
        started = await host.start(SessionStartParams(agent_config=_options(tmp_path)))
        adapter = cast(UnifiedHarnessBackendAdapter, started.backend)
        agent_file.unlink()

        # Do
        await adapter.reload_config(ConfigReloadParams(session_id=adapter.session_id))
        await _run_turn(adapter)

        # Assert
        assert "subagent_type_not_found" in model.spawned[-1]
        assert host._host.child_session_records(adapter.session_id) == ()
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_a_first_turn_promoted_during_reload_spawns_the_new_subagent_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A session nobody has used, and the "reviewer" file written.
        config/reload converges, then the first Turn promotes the session
        before the reload rebinds agent types, and stays running.
    *Do*: Let that Turn finish, then run another that spawns a "reviewer".
    *Assert*: The reload parked the rebind, and the next Turn admits the type.
    """
    # Prepare
    model = _Model()
    model.hold_first_completion = asyncio.Event()
    monkeypatch.setattr(_local_actions, "execute_completion", model)
    host = await _host(tmp_path, process_services)
    opened: list[asyncio.Task[None]] = []
    try:
        started = await host.start(SessionStartParams(agent_config=_options(tmp_path)))
        adapter = cast(UnifiedHarnessBackendAdapter, started.backend)
        (tmp_path / ".vibe" / "agents" / f"{AGENT_TYPE}.toml").write_text(
            REVIEWER, encoding="utf-8"
        )
        converge = adapter._converge_integrations

        async def converge_then_open_the_first_turn(*, strict: bool = False) -> None:
            if opened:
                await converge(strict=strict)
                return
            await converge(strict=strict)
            opened.append(asyncio.create_task(_run_turn(adapter)))
            async with asyncio.timeout(15):
                while (
                    adapter._session.ephemeral
                    or adapter._session.active_turn_id is None
                ):
                    await asyncio.sleep(0)

        monkeypatch.setattr(
            adapter, "_converge_integrations", converge_then_open_the_first_turn
        )

        # Do
        await adapter.reload_config(ConfigReloadParams(session_id=adapter.session_id))
        assert adapter._plugin_reload_parked is True
        assert model.hold_first_completion is not None
        model.hold_first_completion.set()
        [first_turn] = opened
        await first_turn
        await _run_turn(adapter)

        # Assert
        assert "subagent_type_not_found" not in model.spawned[-1]
        [record] = host._host.child_session_records(adapter.session_id)
        assert (record.agent_name, record.agent_type) == (SUBAGENT, AGENT_TYPE)
    finally:
        if model.hold_first_completion is not None:
            model.hold_first_completion.set()
        for task in opened:
            if not task.done():
                task.cancel()
        await host.shutdown()
