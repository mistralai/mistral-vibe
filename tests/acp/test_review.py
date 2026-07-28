from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import start_test_app_server
from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent
from vibe.acp.exceptions import InvalidRequestError
from vibe.app_server.local import LocalHarnessOptions
from vibe.app_server.session import AppServerSession
from vibe.core.agent_loop import AgentLoop
from vibe.core.checkpoints import FileSnapshot, FileState


@pytest.mark.asyncio
async def test_review_extensions_delegate_to_app_server(tmp_path: Path) -> None:
    loops: list[AgentLoop] = []

    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        loop = build_test_agent_loop()
        loops.append(loop)
        return await AppServerSession.start(
            start_test_app_server(loop),
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
        )

    agent = VibeAcpAgent(session_starter=start_session)
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    response = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    session_id = response.session_id
    loop = loops[0]
    path = tmp_path / "review.txt"
    path.write_text("before\n", encoding="utf-8")
    turn_id = len(loop.messages)
    loop.checkpoint_recorder.create_checkpoint()
    loop.checkpoint_recorder.add_snapshot(
        FileSnapshot(path=str(path.resolve()), state=FileState(path.read_bytes()))
    )
    path.write_text("after\n", encoding="utf-8")
    loop.checkpoint_recorder.seal_turn()
    owner = {"kind": "agent", "turnId": turn_id}

    try:
        state = await agent.ext_method("review/state", {"sessionId": session_id})
        baseline = await agent.ext_method(
            "review/baseline", {"sessionId": session_id, "path": str(path)}
        )
        turn_diff = await agent.ext_method(
            "review/turnDiff",
            {"sessionId": session_id, "path": str(path), "owner": owner},
        )
        hunks = await agent.ext_method(
            "review/hunks", {"sessionId": session_id, "path": str(path), "owner": owner}
        )
    finally:
        await agent.close()

    assert state["files"][0]["regions"][0]["owner"] == owner
    assert state["scopes"][0]["owner"] == owner
    assert baseline == {"content": "before\n"}
    assert turn_diff == {
        "status": "modified",
        "baseline": "before\n",
        "current": "after\n",
    }
    assert hunks == {
        "hunks": [
            {
                "side": "additions",
                "line": 0,
                "regions": [
                    {
                        "versionIndex": state["files"][0]["regions"][0]["versionIndex"],
                        "ordinal": state["files"][0]["regions"][0]["ordinal"],
                    }
                ],
            }
        ]
    }


@pytest.mark.parametrize(
    ("method", "expected_content"),
    [("review/approve", "after\n"), ("review/revert", "before\n")],
)
@pytest.mark.asyncio
async def test_review_mutation_extensions_delegate_to_app_server(
    tmp_path: Path, method: str, expected_content: str
) -> None:
    loops: list[AgentLoop] = []

    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        loop = build_test_agent_loop()
        loops.append(loop)
        return await AppServerSession.start(
            start_test_app_server(loop),
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
        )

    agent = VibeAcpAgent(session_starter=start_session)
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    response = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])
    path = tmp_path / "review.txt"
    path.write_text("before\n", encoding="utf-8")
    loop = loops[0]
    loop.checkpoint_recorder.create_checkpoint()
    loop.checkpoint_recorder.add_snapshot(
        FileSnapshot(path=str(path.resolve()), state=FileState(path.read_bytes()))
    )
    path.write_text("after\n", encoding="utf-8")
    loop.checkpoint_recorder.seal_turn()

    try:
        result = await agent.ext_method(
            method, {"sessionId": response.session_id, "target": {"kind": "all"}}
        )
    finally:
        await agent.close()

    assert result == {}
    assert path.read_text(encoding="utf-8") == expected_content


@pytest.mark.asyncio
async def test_review_mutation_rejects_invalid_target(tmp_path: Path) -> None:
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    response = await agent.new_session(cwd=str(tmp_path), mcp_servers=[])

    try:
        with pytest.raises(InvalidRequestError):
            await agent.ext_method(
                "review/approve",
                {"sessionId": response.session_id, "target": {"kind": "bogus"}},
            )
    finally:
        await agent.close()
