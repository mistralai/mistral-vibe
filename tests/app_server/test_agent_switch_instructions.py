"""What the model is told when a session switches agent in place.

The next turn's system prompt is rebuilt from the new agent's instructions:
one uncached request per switch, and nothing else to carry them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import tomllib
from typing import Any

import pytest
import tomli_w

from mistralai_vibe_local_harness.protocol import (
    RustCompletionSucceededEvent,
    RustLLMCallAction,
    RustTextContentBlock,
)
from tests.stubs.local_sandbox import EXECUTE_COMPLETION
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.transport import memory_transport_pair

_ALPHA = "Answer as ALPHA_RULE."
_BETA = "Answer as BETA_RULE."


class _Request:
    def __init__(self, purpose: str, messages: list[Any]) -> None:
        self.purpose = purpose
        self.roles = [getattr(message, "role", None) for message in messages]
        self.texts = [_text(message) for message in messages]

    @property
    def system(self) -> str:
        return self.texts[0] if self.roles[:1] == ["system"] else ""

    @property
    def conversation(self) -> list[str]:
        return self.texts[1:] if self.roles[:1] == ["system"] else self.texts


def _text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return "\n".join(
        part.text
        for part in content or ()
        if isinstance(getattr(part, "text", None), str)
    )


class _RecordingModel:
    """Answers every request and keeps what each agent request carried.

    ``hold`` makes the next agent request wait until it is released, so a test
    can act while a turn runs.
    """

    def __init__(self) -> None:
        self.requests: list[_Request] = []
        self.reply = "summary"
        self.hold: asyncio.Event | None = None
        self.held = asyncio.Event()

    @property
    def agent_requests(self) -> list[_Request]:
        return [request for request in self.requests if request.purpose == "agent"]

    async def __call__(
        self,
        action: RustLLMCallAction,
        messages: list[Any],
        _tools: object,
        _config: object,
        **_sinks: object,
    ) -> RustCompletionSucceededEvent:
        self.requests.append(_Request(action.purpose, list(messages)))
        if (hold := self.hold) is not None and action.purpose == "agent":
            self.hold = None
            self.held.set()
            await hold.wait()
        return RustCompletionSucceededEvent.model_validate({
            "action_id": action.action_id,
            "result": {
                "parts": [
                    RustTextContentBlock(text=self.reply).model_dump(mode="json")
                ],
                "finish_reason": "stop",
                "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
            },
        })


def _write_agents(config_dir: Path) -> None:
    agents = config_dir / "agents"
    agents.mkdir(exist_ok=True)
    for name, rule in (("alpha", _ALPHA), ("beta", _BETA)):
        (agents / f"{name}.toml").write_text(
            f'display_name = "{name.title()}"\ninstructions = "{rule}"\n',
            encoding="utf-8",
        )


@asynccontextmanager
async def _client(process_services: ProcessServices) -> AsyncIterator[AppServerClient]:
    from vibe.app_server._connection_protocol import ClientCapabilities, ClientInfo

    process = HarnessProcess(services=process_services)
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    try:
        await client.initialize(
            ClientInfo(name="test", version="1"), ClientCapabilities()
        )
        await client.notify("initialized")
        yield client
    finally:
        await client.close()


async def _run_turn(client: AppServerClient, session_id: str, text: str) -> None:
    incoming = client.incoming()
    try:
        await client.request(
            "turn/start",
            {"sessionId": session_id, "message": [{"type": "text", "text": text}]},
        )
        async for message in incoming:
            if getattr(message, "method", None) == "turn/completed":
                return
    finally:
        await incoming.aclose()


async def _start(
    client: AppServerClient, cwd: Path, agent: str, *, headless: bool = True
) -> str:
    started: dict[str, Any] = await client.request(
        "session/start",
        {"agentConfig": {"cwd": str(cwd), "headless": headless, "agent": agent}},
    )
    return started["state"]["session"]["id"]


async def _switch(client: AppServerClient, session_id: str, agent: str) -> None:
    await client.request(
        "session/agent/update", {"sessionId": session_id, "agentName": agent}
    )


@pytest.fixture
def model(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> _RecordingModel:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_agents(config_dir)
    recording = _RecordingModel()
    monkeypatch.setattr(EXECUTE_COMPLETION, recording)
    return recording


@pytest.mark.asyncio
async def test_a_switch_rebuilds_the_system_prompt_for_the_next_turn(
    tmp_path: Path, model: _RecordingModel, process_services: ProcessServices
) -> None:
    """*Prepare*: A session that ran a turn as `alpha`.
    *Do*: Switch to `beta` and run a turn.
    *Assert*: That turn's system prompt carries beta's instructions and none of
    alpha's, and no message in the conversation repeats them.
    """
    async with _client(process_services) as client:
        session_id = await _start(client, tmp_path, "alpha")
        await _run_turn(client, session_id, "first")
        await _switch(client, session_id, "beta")
        await _run_turn(client, session_id, "second")

    before, after = model.agent_requests
    assert _ALPHA in before.system
    assert _BETA in after.system
    assert _ALPHA not in after.system
    assert not any(_BETA in text for text in after.conversation)


@pytest.mark.asyncio
async def test_switching_back_restores_the_first_agents_prompt(
    tmp_path: Path, model: _RecordingModel, process_services: ProcessServices
) -> None:
    async with _client(process_services) as client:
        session_id = await _start(client, tmp_path, "alpha")
        await _run_turn(client, session_id, "first")
        await _switch(client, session_id, "beta")
        await _run_turn(client, session_id, "second")
        await _switch(client, session_id, "alpha")
        await _run_turn(client, session_id, "third")

    first, _second, third = model.agent_requests
    assert third.system == first.system


@pytest.mark.asyncio
async def test_a_queued_turn_after_a_switch_runs_on_the_new_prompt(
    tmp_path: Path, model: _RecordingModel, process_services: ProcessServices
) -> None:
    """*Prepare*: A session that ran a turn as `alpha`, switched to `beta`.
    *Do*: Queue a turn, which the Session opens without `turn/start`.
    *Assert*: It runs on beta's prompt.
    """
    async with _client(process_services) as client:
        session_id = await _start(client, tmp_path, "alpha")
        await _run_turn(client, session_id, "first")
        await _switch(client, session_id, "beta")
        incoming = client.incoming()
        try:
            await client.request(
                "session/turn/enqueue",
                {
                    "sessionId": session_id,
                    "entries": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": "queued"}],
                        }
                    ],
                },
            )
            async for message in incoming:
                if getattr(message, "method", None) == "turn/completed":
                    break
        finally:
            await incoming.aclose()

    after = model.agent_requests[-1]
    assert "queued" in after.conversation
    assert _BETA in after.system
    assert _ALPHA not in after.system


@pytest.mark.asyncio
async def test_a_goal_continuation_after_a_switch_runs_on_the_new_prompt(
    tmp_path: Path, model: _RecordingModel, process_services: ProcessServices
) -> None:
    """*Prepare*: A session that ran a turn as `alpha`, switched to `beta`.
    *Do*: Set a goal, whose continuation the session starts on its own.
    *Assert*: The continuation runs on beta's prompt.
    """
    async with _client(process_services) as client:
        session_id = await _start(client, tmp_path, "alpha", headless=False)
        await _run_turn(client, session_id, "first")
        await _switch(client, session_id, "beta")
        incoming = client.incoming()
        try:
            await client.request(
                "goal/set", {"sessionId": session_id, "objective": "Keep going"}
            )
            async for message in incoming:
                if getattr(message, "method", None) == "turn/completed":
                    break
        finally:
            await incoming.aclose()

    after = model.agent_requests[-1]
    assert any("Keep going" in text for text in after.conversation)
    assert _BETA in after.system
    assert _ALPHA not in after.system


@pytest.mark.asyncio
async def test_a_switch_during_a_turn_reaches_the_goal_continuation_after_it(
    tmp_path: Path, model: _RecordingModel, process_services: ProcessServices
) -> None:
    """*Prepare*: A session running a turn as `alpha`, with a goal set.
    *Do*: Switch to `beta` while that turn runs, then let it end.
    *Assert*: The goal continuation that follows runs on beta's prompt.
    """
    async with _client(process_services) as client:
        session_id = await _start(client, tmp_path, "alpha", headless=False)
        await _run_turn(client, session_id, "first")
        release = asyncio.Event()
        model.hold = release
        incoming = client.incoming()
        try:
            await client.request(
                "goal/set", {"sessionId": session_id, "objective": "Keep going"}
            )
            await asyncio.wait_for(model.held.wait(), 10)
            await _switch(client, session_id, "beta")
            release.set()
            completed = 0
            async for message in incoming:
                if getattr(message, "method", None) == "turn/completed":
                    completed += 1
                    if completed == 2:
                        break
        finally:
            await incoming.aclose()

    during, after = model.agent_requests[-2:]
    assert _ALPHA in during.system
    assert _BETA in after.system
    assert _ALPHA not in after.system


@pytest.mark.asyncio
async def test_the_switch_shows_only_the_agent_change_marker(
    tmp_path: Path, model: _RecordingModel, process_services: ProcessServices
) -> None:
    """*Prepare*: A session switched from `alpha` to `beta` after a turn.
    *Do*: Run a turn and read the history.
    *Assert*: The transcript shows the agent change marker and the user's
    text, and no message carries the agent's instructions.
    """
    async with _client(process_services) as client:
        session_id = await _start(client, tmp_path, "alpha")
        await _run_turn(client, session_id, "first")
        await _switch(client, session_id, "beta")
        await _run_turn(client, session_id, "second")
        read: dict[str, Any] = await client.request(
            "session/read", {"sessionId": session_id, "history": {"limit": 50}}
        )

    history = read["state"]["history"]
    texts = [
        block.get("text", "")
        for entry in history
        if entry.get("type") == "message"
        for block in entry.get("content") or ()
    ]
    assert not any(_BETA in text for text in texts)
    assert "second" in texts
    assert any(
        entry.get("type") == "checkpoint" and entry.get("kind") == "agent_change"
        for entry in history
    )


@pytest.mark.asyncio
async def test_a_compaction_after_a_switch_keeps_the_new_agent(
    tmp_path: Path, model: _RecordingModel, process_services: ProcessServices
) -> None:
    async with _client(process_services) as client:
        session_id = await _start(client, tmp_path, "alpha")
        await _run_turn(client, session_id, "first")
        await _switch(client, session_id, "beta")
        await _run_turn(client, session_id, "second")
        await client.request("session/compact", {"sessionId": session_id})
        await _run_turn(client, session_id, "third")

    after = model.agent_requests[-1]
    assert _BETA in after.system
    assert _ALPHA not in after.system


@pytest.mark.asyncio
async def test_an_automatic_compaction_right_after_a_switch_keeps_the_new_agent(
    tmp_path: Path,
    config_dir: Path,
    model: _RecordingModel,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session run as `alpha` whose context is over the model's
    compaction threshold.
    *Do*: Switch to `beta` and run a turn, which compacts before its request.
    *Assert*: The request after the compaction has beta's instructions in its
    system prompt and none of alpha's.
    """
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["models"][0]["auto_compact_threshold"] = 30_000
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    async with _client(process_services) as client:
        session_id = await _start(client, tmp_path, "alpha")
        model.reply = "filler " * 40_000
        await _run_turn(client, session_id, "first")
        model.reply = "summary"
        await _switch(client, session_id, "beta")
        await _run_turn(client, session_id, "second")

    purposes = [request.purpose for request in model.requests]
    assert purposes == ["agent", "compaction", "agent"]
    after = model.agent_requests[-1]
    assert _BETA in after.system
    assert _ALPHA not in after.system
