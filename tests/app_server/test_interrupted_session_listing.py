from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
import shutil
import tomllib
from typing import Any, cast

import pytest
import tomli_w

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionSucceededEvent,
    RustTextContentBlock,
)
from mistralai_vibe_local_harness.vibe._host import UnifiedHarnessSessionBackendHost
from tests.app_server._session_api_harness import USAGE
from tests.app_server.test_unified_harness_backend_adapter import _harness_backend_host
from tests.conftest import build_test_vibe_config
from vibe.app_server._goal_engine import TurnOrigin
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import (
    HarnessProcess,
    HarnessServer,
    create_harness_server,
)
from vibe.app_server.models import GoalStatus, TextContentBlock
from vibe.app_server.multiplex import ChannelOpen, serve_multiplexed
from vibe.app_server.protocol import (
    SessionReadParams,
    SessionResumeParams,
    SessionStartParams,
    TurnStartParams,
)
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair
from vibe.core.config import SessionLoggingConfig

_local_actions = pytest.importorskip("mistralai_vibe_local_harness.vibe._local_actions")


def _answer_then_hold(monkeypatch: pytest.MonkeyPatch) -> list[asyncio.Event]:
    """The first model call answers; every later one holds. Each call sets the
    next event of the returned list.
    """
    calls = [asyncio.Event(), asyncio.Event()]

    async def execute_completion(action: Any, *_args: Any, **_kwargs: Any) -> Any:
        answered = calls[0].is_set()
        (calls[1] if answered else calls[0]).set()
        if answered:
            await asyncio.Event().wait()
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=[RustTextContentBlock(text="done")],
                finish_reason="stop",
                usage=USAGE,
            ),
        )

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)
    return calls


async def _until_idle(backend: Any) -> None:
    for _ in range(500):
        read = await backend.read(SessionReadParams(session_id=backend.session_id))
        if read.state.session.status.type == "idle":
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the turn never ended")


async def _start_turn(backend: Any, text: str) -> None:
    started = await backend.start_turn(
        TurnStartParams(
            session_id=backend.session_id, message=[TextContentBlock(text=text)]
        )
    )
    if started.after_response is not None:
        started.after_response()


DESKTOP: dict[str, Any] = {
    "entrypoint": "desktop",
    "account": {"organizationId": "org-1", "workspaceId": "ws-1", "userId": "u-1"},
}
OTHER_USER: dict[str, Any] = {
    "entrypoint": "desktop",
    "account": {"organizationId": "org-1", "workspaceId": "ws-1", "userId": "u-2"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lister", "listed"),
    [
        (DESKTOP, True),
        (OTHER_USER, False),
        (
            {
                "entrypoint": "desktop",
                "account": {
                    "organizationId": "org-1",
                    "workspaceId": "ws-2",
                    "userId": "u-1",
                },
            },
            False,
        ),
        ({"entrypoint": "cli", "account": DESKTOP["account"]}, False),
        ({"entrypoint": "desktop", "account": None}, False),
    ],
    ids=["same-owner", "other-user", "other-workspace", "cli", "no-account"],
)
async def test_interrupted_sessions_are_listed_to_the_owner_that_left_them(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    lister: dict[str, Any],
    listed: bool,
) -> None:
    """*Prepare*: A Desktop connection, signed in to an account, whose session
        waits on its model call when its app server dies, and an idle session.
    *Do*: List interrupted sessions from a new app server, as some client.
    *Assert*: Only the exact same entrypoint and account is offered the session
        left mid-turn; the idle one is never offered.
    """
    # Prepare
    answered, held = _answer_then_hold(monkeypatch)
    _persist_sessions(config_dir, tmp_path)
    first = _Multiplexed(process_services)
    await first.open("1", **DESKTOP)
    idle = await first.start("1", tmp_path)
    await first.turn("1", idle, "say hi")
    await asyncio.wait_for(answered.wait(), timeout=10)
    await _read_once_idle(first, idle)
    await first.open("2", **DESKTOP)
    busy = await first.start("2", tmp_path)
    await first.turn("2", busy, "work on it")
    await asyncio.wait_for(held.wait(), timeout=10)
    # A copy holds no lease: the store a killed process leaves behind.
    shutil.copytree(tmp_path / "logs", tmp_path / "dead")
    await first.close()
    _persist_sessions(config_dir, tmp_path, "dead")
    second = _Multiplexed(process_services)
    await second.open("1", **lister)

    # Do
    interrupted = await second.rpc("1", "session/list", {"interrupted": True})
    await second.close()

    # Assert
    assert [item["id"] for item in interrupted["items"]] == ([busy] if listed else [])


@pytest.mark.asyncio
async def test_the_owner_of_a_session_is_the_latest_client_to_open_it(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session one account started, then another account resumed,
        whose turn waits on its model call when the app server dies.
    *Do*: List interrupted sessions from a new app server as each account.
    *Assert*: Only the account that opened it last is offered it.
    """
    # Prepare
    answered, held = _answer_then_hold(monkeypatch)
    _persist_sessions(config_dir, tmp_path)
    first = _Multiplexed(process_services)
    await first.open("1", **DESKTOP)
    session_id = await first.start("1", tmp_path)
    await first.turn("1", session_id, "say hi")
    await asyncio.wait_for(answered.wait(), timeout=10)
    await _read_once_idle(first, session_id)
    await first.rpc("1", "session/stop", {"sessionId": session_id})
    await first.closed("1")
    await first.open("2", **OTHER_USER)
    await first.rpc("2", "session/resume", {"sessionId": session_id})
    await first.turn("2", session_id, "work on it")
    await asyncio.wait_for(held.wait(), timeout=10)
    shutil.copytree(tmp_path / "logs", tmp_path / "dead")
    await first.close()
    _persist_sessions(config_dir, tmp_path, "dead")
    second = _Multiplexed(process_services)
    await second.open("1", **DESKTOP)
    await second.open("2", **OTHER_USER)

    # Do
    as_starter = await second.rpc("1", "session/list", {"interrupted": True})
    as_latest = await second.rpc("2", "session/list", {"interrupted": True})
    await second.close()

    # Assert
    assert as_starter["items"] == []
    assert [item["id"] for item in as_latest["items"]] == [session_id]


@pytest.mark.asyncio
async def test_a_turn_started_by_a_client_with_no_account_is_offered_to_no_one(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session Desktop started, then continued from a client that
        reports no account (the CLI), whose turn waits on its model call when
        the app server dies.
    *Do*: List interrupted sessions from a new app server as Desktop.
    *Assert*: Nothing is listed: only the client that started a turn resumes it.
    """
    # Prepare
    answered, held = _answer_then_hold(monkeypatch)
    _persist_sessions(config_dir, tmp_path)
    first = _Multiplexed(process_services)
    await first.open("1", **DESKTOP)
    session_id = await first.start("1", tmp_path)
    await first.turn("1", session_id, "say hi")
    await asyncio.wait_for(answered.wait(), timeout=10)
    await _read_once_idle(first, session_id)
    await first.rpc("1", "session/stop", {"sessionId": session_id})
    await first.closed("1")
    await first.open("2", entrypoint="cli")
    await first.rpc("2", "session/resume", {"sessionId": session_id})
    await first.turn("2", session_id, "work on it")
    await asyncio.wait_for(held.wait(), timeout=10)
    shutil.copytree(tmp_path / "logs", tmp_path / "dead")
    await first.close()
    _persist_sessions(config_dir, tmp_path, "dead")
    second = _Multiplexed(process_services)
    await second.open("1", **DESKTOP)

    # Do
    listed = await second.rpc("1", "session/list", {"interrupted": True})
    await second.close()

    # Assert
    assert listed["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["session_stop", "channel_close", "client_lost"])
async def test_a_stop_mid_model_call_leaves_the_turn_to_resume(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    stop: str,
) -> None:
    """*Prepare*: A multiplexed app server whose session waits on its model call.
    *Do*: End it with ``session/stop``, by closing the channel, or by losing the
        client; then, from a new app server, list interrupted sessions and
        resume the session while the model answers.
    *Assert*: The session is listed, and the turn the stop cut completes with
        the model's answer.
    """
    # Prepare
    answered, held = _answer_then_hold(monkeypatch)
    answered.set()
    _persist_sessions(config_dir, tmp_path)
    first = _Multiplexed(process_services)
    await first.open("1", **DESKTOP)
    started = await first.rpc(
        "1", "session/start", {"agentConfig": {"cwd": str(tmp_path)}}
    )
    session_id = started["state"]["session"]["id"]
    await first.rpc(
        "1",
        "turn/start",
        {"sessionId": session_id, "message": [{"type": "text", "text": "work on it"}]},
    )
    await asyncio.wait_for(held.wait(), timeout=10)

    # Do
    if stop == "session_stop":
        await first.rpc("1", "session/stop", {"sessionId": session_id})
        await first.closed("1")
        await first.close()
    elif stop == "channel_close":
        await first.client.send({"channelId": "1", "message": None})
        await first.closed("1")
        await first.close()
    else:
        await first.close()
    monkeypatch.setattr(_local_actions, "execute_completion", _answer_at_once)
    second = _Multiplexed(process_services)
    await second.open("1", **DESKTOP)
    listed = await second.rpc("1", "session/list", {"interrupted": True})
    await second.rpc("1", "session/resume", {"sessionId": session_id})
    read = await _read_once_idle(second, session_id)
    await second.close()

    # Assert
    assert [item["id"] for item in listed["items"]] == [session_id]
    assert [turn["status"] for turn in read["state"]["turns"]] == ["completed"]
    assert read["state"]["history"][-1]["content"] == [{"type": "text", "text": "done"}]


async def _left_mid_turn_by(
    owner: dict[str, Any],
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> tuple[list[asyncio.Event], str]:
    """A session ``owner`` started whose turn waits on its model call when its
    app server dies, its store then read by the next app server.
    """
    calls = _answer_then_hold(monkeypatch)
    calls[0].set()
    _persist_sessions(config_dir, tmp_path)
    first = _Multiplexed(process_services)
    await first.open("1", **owner)
    session_id = await first.start("1", tmp_path)
    await first.turn("1", session_id, "work on it")
    await asyncio.wait_for(calls[1].wait(), timeout=10)
    shutil.copytree(tmp_path / "logs", tmp_path / "dead")
    await first.close()
    _persist_sessions(config_dir, tmp_path, "dead")
    return calls, session_id


@pytest.mark.asyncio
async def test_another_account_opening_a_session_leaves_its_turn_to_its_owner(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session one account left mid-turn.
    *Do*: From a new app server, open it as another account, try a turn
        there, and close it; then list and resume it as its owner.
    *Assert*: The other account's open sends no model call and refuses the
        turn; the session stays its owner's, offered to it and carried on by
        its resume.
    """
    # Prepare
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    sent: list[str] = []

    async def answer(action: Any, *args: Any, **kwargs: Any) -> Any:
        sent.append(action.action_id)
        return await _answer_at_once(action, *args, **kwargs)

    monkeypatch.setattr(_local_actions, "execute_completion", answer)
    second = _Multiplexed(process_services)
    await second.open("1", **DESKTOP)
    await second.open("2", **OTHER_USER)

    # Do
    await second.rpc("2", "session/resume", {"sessionId": session_id})
    await asyncio.sleep(0.2)
    sent_while_held = list(sent)
    refused = await second.request(
        "2",
        "turn/start",
        {"sessionId": session_id, "message": [{"type": "text", "text": "mine"}]},
    )
    await second.rpc("2", "session/stop", {"sessionId": session_id})
    await second.closed("2")
    listed = await second.rpc("1", "session/list", {"interrupted": True})
    await second.rpc("1", "session/resume", {"sessionId": session_id})
    read = await _read_once_idle(second, session_id)
    await second.close()

    # Assert
    assert sent_while_held == []
    assert "error" in refused
    assert [item["id"] for item in listed["items"]] == [session_id]
    assert len(sent) == 1
    assert [turn["status"] for turn in read["state"]["turns"]] == ["completed"]


NO_ACCOUNT: dict[str, Any] = {"entrypoint": "desktop", "account": None}


@pytest.mark.asyncio
async def test_a_connection_with_no_account_owns_nothing(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session a Desktop connection with no account left mid-turn,
        and one an account left mid-turn.
    *Do*: From a new app server, list interrupted sessions with no account,
        then open the account's session with no account.
    *Assert*: The session is not stamped, nothing is listed, and opening the
        account's session holds its turn for that account.
    """
    # Prepare
    _calls, unowned = await _left_mid_turn_by(
        NO_ACCOUNT, config_dir, tmp_path, monkeypatch, process_services
    )
    stamp = tmp_path / "dead" / "unified" / unowned / "owner.json"
    sent: list[str] = []

    async def answer(action: Any, *args: Any, **kwargs: Any) -> Any:
        sent.append(action.action_id)
        return await _answer_at_once(action, *args, **kwargs)

    monkeypatch.setattr(_local_actions, "execute_completion", answer)
    second = _Multiplexed(process_services)
    await second.open("1", **NO_ACCOUNT)

    # Do
    listed = await second.rpc("1", "session/list", {"interrupted": True})
    await second.close()

    # Assert
    assert not stamp.exists()
    assert listed["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("opener", [DESKTOP, OTHER_USER], ids=["owner", "other"])
async def test_a_resume_that_interrupts_the_left_turn_runs_nothing(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    opener: dict[str, Any],
) -> None:
    """*Prepare*: A session one account left mid-turn.
    *Do*: From a new app server, resume it with ``interruptLeftTurn`` as its
        owner or another account, then list interrupted sessions as the owner.
    *Assert*: No model call is sent, the turn ends interrupted, and it is no
        longer offered to its owner.
    """
    # Prepare
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    sent: list[str] = []

    async def answer(action: Any, *args: Any, **kwargs: Any) -> Any:
        sent.append(action.action_id)
        return await _answer_at_once(action, *args, **kwargs)

    monkeypatch.setattr(_local_actions, "execute_completion", answer)
    second = _Multiplexed(process_services)
    await second.open("1", **DESKTOP)
    await second.open("2", **opener)

    # Do
    await second.rpc(
        "2", "session/resume", {"sessionId": session_id, "interruptLeftTurn": True}
    )
    read = await _read_once_idle(second, session_id, channel="2")
    await second.rpc("2", "session/stop", {"sessionId": session_id})
    await second.closed("2")
    listed = await second.rpc("1", "session/list", {"interrupted": True})
    await second.close()

    # Assert
    assert sent == []
    assert [turn["status"] for turn in read["state"]["turns"]] == ["interrupted"]
    assert listed["items"] == []


@pytest.mark.asyncio
async def test_another_account_can_end_a_held_turn_and_take_the_session_over(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session one account left mid-turn, opened from a new app
        server by another account.
    *Do*: Interrupt the held turn there, then start a turn.
    *Assert*: The interrupt sends nothing and ends the turn; the new turn runs,
        and the session is no longer offered to its first owner.
    """
    # Prepare
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    sent: list[str] = []

    async def answer(action: Any, *args: Any, **kwargs: Any) -> Any:
        sent.append(action.action_id)
        return await _answer_at_once(action, *args, **kwargs)

    monkeypatch.setattr(_local_actions, "execute_completion", answer)
    second = _Multiplexed(process_services)
    await second.open("1", **DESKTOP)
    await second.open("2", **OTHER_USER)
    opened = await second.rpc("2", "session/resume", {"sessionId": session_id})
    held_turn_id = opened["state"]["turns"][-1]["id"]

    # Do
    await second.rpc(
        "2", "turn/interrupt", {"sessionId": session_id, "expectedTurnId": held_turn_id}
    )
    interrupted = await _read_once_idle(second, session_id, channel="2")
    sent_after_interrupt = list(sent)
    await second.turn("2", session_id, "mine now")
    read = await _read_once_idle(second, session_id, channel="2")
    listed = await second.rpc("1", "session/list", {"interrupted": True})
    await second.close()

    # Assert
    assert sent_after_interrupt == []
    assert interrupted["state"]["turns"][-1]["status"] == "interrupted"
    assert [turn["status"] for turn in read["state"]["turns"]][-1] == "completed"
    assert len(sent) == 1
    assert listed["items"] == []


@pytest.mark.asyncio
async def test_opening_a_session_does_not_take_it_over_but_a_turn_does(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A session one account started and finished a turn in.
    *Do*: Open it as another account, then start a turn there that the app
        server dies in; list interrupted sessions from a new one as each.
    *Assert*: Only the account that ran the turn is offered it.
    """
    # Prepare
    answered, held = _answer_then_hold(monkeypatch)
    _persist_sessions(config_dir, tmp_path)
    first = _Multiplexed(process_services)
    await first.open("1", **DESKTOP)
    session_id = await first.start("1", tmp_path)
    await first.turn("1", session_id, "say hi")
    await asyncio.wait_for(answered.wait(), timeout=10)
    await _read_once_idle(first, session_id)
    await first.rpc("1", "session/stop", {"sessionId": session_id})
    await first.closed("1")
    await first.open("2", **OTHER_USER)
    await first.rpc("2", "session/resume", {"sessionId": session_id})
    opened_owner = (
        tmp_path / "logs" / "unified" / session_id / "owner.json"
    ).read_text()

    # Do
    await first.turn("2", session_id, "work on it")
    await asyncio.wait_for(held.wait(), timeout=10)
    shutil.copytree(tmp_path / "logs", tmp_path / "dead")
    await first.close()
    _persist_sessions(config_dir, tmp_path, "dead")
    second = _Multiplexed(process_services)
    await second.open("1", **DESKTOP)
    await second.open("2", **OTHER_USER)
    as_starter = await second.rpc("1", "session/list", {"interrupted": True})
    as_runner = await second.rpc("2", "session/list", {"interrupted": True})
    await second.close()

    # Assert
    assert '"u-1"' in opened_owner
    assert as_starter["items"] == []
    assert [item["id"] for item in as_runner["items"]] == [session_id]


@pytest.mark.asyncio
async def test_sessions_owing_a_reply_are_listed_as_the_listing_connection(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: A Desktop connection signed in to an account.
    *Do*: List the sessions owing a reply, then all sessions.
    *Assert*: The first listing asks the Host for that owner's sessions only,
        so another account's are left out; the second for every one.
    """
    # Prepare
    owners: list[Any] = []
    listing = UnifiedHarnessSessionBackendHost.list

    async def list_as(self: Any, **kwargs: Any) -> Any:
        owners.append(kwargs.get("owner"))
        return await listing(self, **kwargs)

    monkeypatch.setattr(UnifiedHarnessSessionBackendHost, "list", list_as)
    _persist_sessions(config_dir, tmp_path)
    server = _Multiplexed(process_services)
    await server.open("1", **DESKTOP)

    # Do
    await server.rpc("1", "session/list", {"owesReply": True})
    owing = list(owners)
    owners.clear()
    await server.rpc("1", "session/list", {})
    await server.close()

    # Assert
    assert owing and all(owner == DESKTOP for owner in owing)
    assert owners and all(owner is None for owner in owners)


async def _read_once_idle(
    server: _Multiplexed, session_id: str, *, channel: str = "1"
) -> Any:
    for _ in range(500):
        read = await server.rpc(channel, "session/read", {"sessionId": session_id})
        if read["state"]["session"]["status"]["type"] == "idle":
            return read
        await asyncio.sleep(0.02)
    raise AssertionError("the resumed turn never ended")


async def _answer_at_once(action: Any, *_args: Any, **_kwargs: Any) -> Any:
    return RustCompletionSucceededEvent(
        action_id=action.action_id,
        result=RustCompletionResult(
            parts=[RustTextContentBlock(text="done")], finish_reason="stop", usage=USAGE
        ),
    )


def _persist_sessions(config_dir: Path, tmp_path: Path, folder: str = "logs") -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {"enabled": True, "save_dir": str(tmp_path / folder)}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


class _Multiplexed:
    """A multiplexed app server, as Desktop runs it, driven by raw frames."""

    def __init__(self, process_services: ProcessServices) -> None:
        self.client, transport = memory_transport_pair()

        async def factory(
            channel: JsonRpcTransport, _options: ChannelOpen | None
        ) -> HarnessServer:
            return await create_harness_server(
                channel,
                transport_kind="stdio",
                process=HarnessProcess(services=process_services),
            )

        self._serving = asyncio.create_task(serve_multiplexed(transport, factory))
        self._incoming = self.client.messages()
        self._request_id = 0

    async def open(
        self,
        channel: str,
        *,
        entrypoint: str = "desktop",
        account: dict[str, str] | None = None,
    ) -> None:
        params: dict[str, Any] = {
            "clientInfo": {
                "name": "restart-test",
                "version": "1",
                "entrypoint": entrypoint,
            }
        }
        if account is not None:
            params["account"] = account
        await self.rpc(channel, "initialize", params)
        await self.client.send({
            "channelId": channel,
            "message": {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        })

    async def rpc(self, channel: str, method: str, params: dict[str, Any]) -> Any:
        message = await self.request(channel, method, params)
        assert "error" not in message, message
        return message["result"]

    async def request(self, channel: str, method: str, params: dict[str, Any]) -> Any:
        self._request_id += 1
        request_id = self._request_id
        await self.client.send({
            "channelId": channel,
            "message": {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        })
        return await self._next(
            channel,
            lambda message: message.get("id") == request_id and "method" not in message,
        )

    async def start(self, channel: str, cwd: Path) -> str:
        started = await self.rpc(
            channel, "session/start", {"agentConfig": {"cwd": str(cwd)}}
        )
        return started["state"]["session"]["id"]

    async def turn(self, channel: str, session_id: str, text: str) -> None:
        await self.rpc(
            channel,
            "turn/start",
            {"sessionId": session_id, "message": [{"type": "text", "text": text}]},
        )

    async def closed(self, channel: str) -> None:
        async with asyncio.timeout(10):
            async for frame in self._incoming:
                if frame["channelId"] == channel and frame["message"] is None:
                    return
        raise AssertionError("the channel never closed")

    async def close(self) -> None:
        await self.client.close()
        await asyncio.wait_for(self._serving, timeout=10)

    async def _next(self, channel: str, matches: Callable[[Any], bool]) -> Any:
        async with asyncio.timeout(10):
            async for frame in self._incoming:
                message = frame["message"]
                if frame["channelId"] != channel or message is None:
                    continue
                if message.get("method") == "callback/call":
                    raise AssertionError(f"unexpected callback: {message}")
                if matches(message):
                    return message
        raise AssertionError("the connection closed first")


@pytest.mark.asyncio
async def test_a_resumed_goal_turn_stays_bound_to_its_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session with a goal whose turn waits on its model call, and
    whose app server lost its client.
    *Do*: Resume the session on a new app server.
    *Assert*: The goal comes back paused, with the carried-on turn still bound
        to it, so the turn's end charges it.
    """
    # Prepare
    _answered, held = _answer_then_hold(monkeypatch)
    _answered.set()
    logging_config = SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    host = cast(
        Any,
        _harness_backend_host(build_test_vibe_config(session_logging=logging_config)),
    )
    started = await host.start(SessionStartParams())
    backend = cast(Any, started.backend)
    session_id = backend.session_id
    await backend._goal.controller.dispatch(
        "goal/set", {"sessionId": session_id, "objective": "Keep going"}
    )
    await _start_turn(backend, "work on it")
    await asyncio.wait_for(held.wait(), timeout=10)
    turn_id = backend._session.active_turn_id
    await backend._goal.turn_started(turn_id, TurnOrigin.USER)
    await host.shutdown()

    # Do
    second = _harness_backend_host(
        build_test_vibe_config(session_logging=logging_config)
    )
    resumed = await second.resume(SessionResumeParams(session_id=session_id))
    goal = cast(Any, resumed.backend)._goal

    # Assert
    assert goal.goal is not None
    assert goal.goal.status is GoalStatus.PAUSED
    assert goal.bound(turn_id)
    await second.shutdown()
