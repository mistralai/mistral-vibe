"""A client that opts in at ``initialize`` has the app server resume the turns
its owner left mid-flight, with no ``session/resume`` of its own.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests.app_server.test_interrupted_session_listing import (
    DESKTOP,
    OTHER_USER,
    _answer_at_once,
    _left_mid_turn_by,
    _Multiplexed,
)
from vibe.app_server._connection_protocol import InitializeParams
from vibe.app_server._left_turns import list_on_own_channel, resume_on_own_channel
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server.multiplex import ChannelOpen, serve_multiplexed
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair

_local_actions = pytest.importorskip("mistralai_vibe_local_harness.vibe._local_actions")


class _Resuming(_Multiplexed):
    """A multiplexed app server whose channels resume their owner's left
    turns, as the production entry point builds them.
    """

    def __init__(
        self,
        process_services: ProcessServices,
        gate: asyncio.Event | None = None,
        *,
        stuck: bool = False,
    ) -> None:
        self.client, transport = memory_transport_pair()
        self.resuming = asyncio.Event()

        async def factory(
            channel: JsonRpcTransport,
            _options: ChannelOpen | None,
            left_turn_session: str | None = None,
        ) -> Any:
            return await create_harness_server(
                channel,
                transport_kind="stdio",
                process=HarnessProcess(services=process_services),
                open_sessions=process_services.open_sessions,
                left_turns=process_services.left_turns,
                open_left_turn_channel=open_channel,
                list_on_own_channel=list_left,
                left_turn_session=left_turn_session,
            )

        async def serve_own(channel: JsonRpcTransport, session_id: str) -> None:
            server = await factory(channel, None, session_id)
            await server.serve()

        async def serve_listing(channel: JsonRpcTransport) -> None:
            server = await factory(channel, None)
            await server.serve()

        async def list_left(params: InitializeParams) -> list[str]:
            return await list_on_own_channel(serve_listing, params)

        async def open_channel(
            params: InitializeParams, session_id: str, interrupt: bool
        ) -> None:
            self.resuming.set()
            while gate is not None and not gate.is_set():
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    # A stuck resume does not even end at its deadline.
                    if not stuck:
                        raise
            await resume_on_own_channel(serve_own, params, session_id, interrupt)

        self._serving = asyncio.create_task(serve_multiplexed(transport, factory))
        self._incoming = self.client.messages()
        self._request_id = 0


async def _open_resuming(
    server: _Multiplexed,
    channel: str,
    owner: dict[str, Any],
    credentials: str | None = None,
) -> None:
    capabilities: dict[str, Any] = {"resumeLeftTurns": True}
    if credentials is not None:
        capabilities["platformCredentials"] = {
            "version": 1,
            "kind": "oauth_access_token",
            "lifetime": credentials,
        }
    await server.rpc(
        channel,
        "initialize",
        {
            "clientInfo": {
                "name": "restart-test",
                "version": "1",
                "entrypoint": owner["entrypoint"],
            },
            "account": owner["account"],
            "capabilities": capabilities,
        },
    )
    await server.client.send({
        "channelId": channel,
        "message": {"jsonrpc": "2.0", "method": "initialized", "params": {}},
    })


def second_lender(process_services: ProcessServices) -> Any:
    """The one channel lending in the process, so a test can withdraw it."""
    lenders = process_services.platform_credential_lenders
    (entries,) = lenders._lenders.values()
    (entry,) = entries
    return entry.provider


async def _until_completed(server: _Multiplexed, channel: str, session_id: str) -> Any:
    for _ in range(500):
        read = await server.rpc(channel, "session/read", {"sessionId": session_id})
        turns = read["state"].get("turns") or []
        if turns and turns[-1]["status"] == "completed":
            return read
        await asyncio.sleep(0.02)
    raise AssertionError("the left turn was never carried on")


@pytest.mark.asyncio
async def test_the_server_carries_on_its_owner_s_left_turn_without_a_resume(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # Prepare: a turn alice's app server left waiting on its model call.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    monkeypatch.setattr(_local_actions, "execute_completion", _answer_at_once)

    # Do: her Desktop connects again and opts in; it resumes nothing itself.
    second = _Resuming(process_services)
    await _open_resuming(second, "1", DESKTOP)
    read = await _until_completed(second, "1", session_id)
    await second.close()

    # Assert
    assert [turn["status"] for turn in read["state"]["turns"]] == ["completed"]


@pytest.mark.asyncio
async def test_the_server_leaves_another_owner_s_turn_alone(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # Prepare: a turn alice left.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    sent: list[str] = []

    async def answer(action: Any, *args: Any, **kwargs: Any) -> Any:
        sent.append(action.action_id)
        return await _answer_at_once(action, *args, **kwargs)

    monkeypatch.setattr(_local_actions, "execute_completion", answer)

    # Do: bob connects and opts in.
    second = _Resuming(process_services)
    await _open_resuming(second, "1", OTHER_USER)
    await asyncio.sleep(0.5)

    # Assert: nothing ran; alice's own resume still finds it.
    assert sent == []
    await second.open("2", **DESKTOP)
    listed = await second.rpc("2", "session/list", {"interrupted": True})
    await second.close()
    assert [item["id"] for item in listed["items"]] == [session_id]


@pytest.mark.asyncio
async def test_without_the_capability_the_server_resumes_nothing(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
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
    await asyncio.sleep(0.5)
    listed = await second.rpc("1", "session/list", {"interrupted": True})
    await second.close()

    assert sent == []
    assert [item["id"] for item in listed["items"]] == [session_id]


async def _until_running_with_no_client(server: _Multiplexed, channel: str) -> Any:
    for _ in range(500):
        read = await server.rpc(channel, "session/leftTurns/read", {})
        if [turn["state"] for turn in read["turns"]] == ["running"]:
            return read
        await asyncio.sleep(0.02)
    raise AssertionError("the left turn never ran with no client")


@pytest.mark.asyncio
async def test_the_owner_reads_and_stops_the_left_turns_the_server_carries_on(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # Prepare: alice's left turn, carried on by the server, waits on its model.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    second = _Resuming(process_services)
    await _open_resuming(second, "1", DESKTOP)
    read = await _until_running_with_no_client(second, "1")

    # Do: her Desktop stops the left turns.
    stopped = await second.rpc("1", "session/leftTurns/interrupt", {})

    # Assert: it was running, and is now interrupted; reopening shows it so.
    assert read == {
        "status": "listed",
        "turns": [{"sessionId": session_id, "state": "running"}],
    }
    assert stopped == {
        "status": "listed",
        "turns": [{"sessionId": session_id, "outcome": "interrupted"}],
    }
    await second.open("2", **DESKTOP)
    resumed = await second.rpc("2", "session/resume", {"sessionId": session_id})
    await second.close()
    assert resumed["state"]["turns"][-1]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_another_owner_neither_reads_nor_stops_them(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    _calls, _session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    second = _Resuming(process_services)
    await _open_resuming(second, "1", DESKTOP)
    await _until_running_with_no_client(second, "1")
    await _open_resuming(second, "2", OTHER_USER)

    read = await second.rpc("2", "session/leftTurns/read", {})
    stopped = await second.rpc("2", "session/leftTurns/interrupt", {})

    assert read["turns"] == []
    assert stopped["turns"] == []
    assert [
        turn["state"]
        for turn in (await second.rpc("1", "session/leftTurns/read", {}))["turns"]
    ] == ["running"]
    await second.close()


@pytest.mark.asyncio
async def test_a_chat_opened_on_a_turn_the_server_carries_on_attaches_to_it(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # Prepare: alice's left turn runs with no client, its model call held.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    second = _Resuming(process_services)
    await _open_resuming(second, "1", DESKTOP)
    await _until_running_with_no_client(second, "1")

    # Do: she opens the chat, as Desktop does, on the same process.
    await _open_resuming(second, "2", DESKTOP)
    resumed = await second.rpc("2", "session/resume", {"sessionId": session_id})

    # Assert: the same turn, still running, now with a client.
    read = await second.rpc("1", "session/leftTurns/read", {})
    await second.close()
    assert resumed["state"]["turns"][-1]["status"] == "in_progress"
    assert read["turns"] == []


@pytest.mark.asyncio
async def test_a_chat_opened_while_the_server_resumes_its_turn_attaches_to_it(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # Prepare: the server is about to resume alice's left turn.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    gate = asyncio.Event()
    second = _Resuming(process_services, gate)
    await _open_resuming(second, "1", DESKTOP)
    await asyncio.wait_for(second.resuming.wait(), 5)

    # Do: she opens the chat before that resume is answered.
    await _open_resuming(second, "2", DESKTOP)
    opening = asyncio.create_task(
        second.rpc("2", "session/resume", {"sessionId": session_id})
    )
    await asyncio.sleep(0.2)
    gate.set()
    resumed = await asyncio.wait_for(opening, 10)

    # Assert: her chat holds the turn the server carried on.
    read = await second.rpc("1", "session/leftTurns/read", {})
    await second.close()
    assert resumed["state"]["turns"][-1]["status"] == "in_progress"
    assert read["turns"] == []


@pytest.mark.asyncio
async def test_a_chat_opened_while_the_server_stops_its_turn_waits_for_the_stop(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from vibe.app_server import _left_turns

    # Prepare: alice's left turn runs with no client; once the server's own
    # channel has interrupted it, that channel is slow to close.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    second = _Resuming(process_services)
    await _open_resuming(second, "1", DESKTOP)
    await _until_running_with_no_client(second, "1")
    closing = asyncio.Event()
    close = _left_turns.AppServerClient.close

    async def slow_close(client: Any) -> None:
        closing.set()
        await asyncio.sleep(0.3)
        await close(client)

    monkeypatch.setattr(_left_turns.AppServerClient, "close", slow_close)
    stopping = asyncio.create_task(second.rpc("1", "session/leftTurns/interrupt", {}))
    await asyncio.wait_for(closing.wait(), 10)

    # Do: she opens the chat while the server's own channel interrupts it.
    chat = _Resuming(process_services)
    await _open_resuming(chat, "1", DESKTOP)
    opened = await asyncio.wait_for(
        chat.rpc("1", "session/resume", {"sessionId": session_id}), 10
    )

    # Assert: the chat opened only once the stop was done.
    stopped_first = stopping.done()
    stopped = await asyncio.wait_for(stopping, 10)
    await chat.close()
    await second.close()
    assert stopped_first
    assert [turn["outcome"] for turn in stopped["turns"]] == ["interrupted"]
    assert opened["state"]["turns"][-1]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_a_left_turn_waits_for_a_process_lifetime_lender(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:

    # Prepare: alice's process-lifetime lender is gone (e.g. refused).
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    second = _Resuming(process_services)
    await _open_resuming(second, "1", DESKTOP, credentials="process")
    process_services.platform_credential_lenders.unregister(
        second_lender(process_services)
    )

    # Do
    for _ in range(250):
        read = await second.rpc("1", "session/leftTurns/read", {})
        if read["status"] != "listing":
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)
    read = await second.rpc("1", "session/leftTurns/read", {})
    await second.close()

    # Assert: it does not run with no sign-in to borrow.
    assert read["turns"] == [{"sessionId": session_id, "state": "waiting_for_lender"}]


@pytest.mark.asyncio
async def test_a_client_that_lends_only_per_chat_is_told_to_declare_a_process_lender(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    sent: list[str] = []

    async def answer(action: Any, *args: Any, **kwargs: Any) -> Any:
        sent.append(action.action_id)
        return await _answer_at_once(action, *args, **kwargs)

    _calls, _session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    monkeypatch.setattr(_local_actions, "execute_completion", answer)
    second = _Resuming(process_services)

    await _open_resuming(second, "1", DESKTOP, credentials="connection")
    await asyncio.sleep(0.3)
    read = await second.rpc("1", "session/leftTurns/read", {})
    await second.close()

    assert read == {"status": "needs_process_lender", "turns": []}
    assert sent == []


@pytest.mark.asyncio
async def test_a_chat_waits_for_the_server_s_resume_then_attaches(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from vibe.app_server import _left_turns

    # Prepare: the server's own resume of alice's left turn is slow. Its
    # deadline scaled down: the old 30 s cap on a chat's wait was a quarter
    # of it.
    monkeypatch.setattr(_left_turns, "RESUME_TIMEOUT_S", 4.0)
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    gate = asyncio.Event()
    second = _Resuming(process_services, gate)
    await _open_resuming(second, "1", DESKTOP)
    await asyncio.wait_for(second.resuming.wait(), 5)
    chat = _Resuming(process_services)
    await _open_resuming(chat, "1", DESKTOP)

    # Do: she opens the chat, and the server's resume ends past that cap.
    opening = asyncio.create_task(
        chat.request("1", "session/resume", {"sessionId": session_id})
    )
    await asyncio.sleep(1.5)
    gate.set()
    answer = await asyncio.wait_for(opening, 10)
    read = await second.rpc("1", "session/leftTurns/read", {})
    await chat.close()
    await second.close()

    # Assert: her chat holds the turn the server carried on.
    assert answer["result"]["state"]["turns"][-1]["status"] == "in_progress", answer
    assert read["turns"] == []


@pytest.mark.asyncio
async def test_a_chat_behind_a_server_resume_stuck_past_its_deadline_is_told_busy(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from vibe.app_server import _left_turns

    # Prepare: the server's own resume of alice's left turn hangs, even past
    # its own deadline.
    monkeypatch.setattr(_left_turns, "RESUME_TIMEOUT_S", 0.5)
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    gate = asyncio.Event()
    second = _Resuming(process_services, gate, stuck=True)
    await _open_resuming(second, "1", DESKTOP)
    await asyncio.wait_for(second.resuming.wait(), 5)
    chat = _Resuming(process_services)
    await _open_resuming(chat, "1", DESKTOP)

    # Do: she opens the chat.
    try:
        answer = await asyncio.wait_for(
            chat.request("1", "session/resume", {"sessionId": session_id}), 10
        )
    finally:
        gate.set()
        await chat.close()
        await second.close()

    # Assert: the session-busy conflict any other holder of the session gets.
    assert answer["error"]["code"] == "conflict"
    assert answer["error"]["data"] == {
        "harnessCode": "session_busy",
        "details": {"session_id": session_id},
    }


@pytest.mark.asyncio
async def test_a_chat_inside_its_checks_when_the_server_stops_its_turn_does_not_take_it(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from vibe.app_server.server import AppServer

    # Prepare: alice's left turn runs with no client; her chat's check is held.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    second = _Resuming(process_services)
    await _open_resuming(second, "1", DESKTOP)
    await _until_running_with_no_client(second, "1")
    checking = asyncio.Event()
    release = asyncio.Event()
    check = AppServer._validate_open_callback_capabilities

    async def held_for_the_chat(self: Any, method: str, root: Any = None) -> None:
        if self._left_turn_session is None and not checking.is_set():
            checking.set()
            await release.wait()
        await check(self, method, root)

    monkeypatch.setattr(
        AppServer, "_validate_open_callback_capabilities", held_for_the_chat
    )
    chat = _Resuming(process_services)
    await _open_resuming(chat, "1", DESKTOP)
    opening = asyncio.create_task(
        chat.request("1", "session/resume", {"sessionId": session_id})
    )
    await asyncio.wait_for(checking.wait(), 10)

    # Do: the server stops the turn while the chat is inside its check.
    stopping = asyncio.create_task(second.rpc("1", "session/leftTurns/interrupt", {}))
    async with asyncio.timeout(10):
        while not process_services.open_sessions.resuming(session_id):
            await asyncio.sleep(0.01)
    release.set()
    stopped = await asyncio.wait_for(stopping, 20)
    opened = await asyncio.wait_for(opening, 20)
    await chat.close()
    await second.close()

    # Assert: the stop came first; the chat then holds the turn as it left it.
    assert stopped["turns"][0]["outcome"] == "interrupted", stopped
    assert opened["result"]["state"]["turns"][-1]["status"] == "interrupted"


@pytest.mark.asyncio
async def test_a_server_that_resumes_nothing_tells_an_account_so(
    process_services: ProcessServices,
) -> None:
    from vibe.app_server._runtime import create_harness_server
    from vibe.app_server.client import AppServerClient

    client_side, server_side = memory_transport_pair()
    server = await create_harness_server(
        server_side,
        transport_kind="stdio",
        process=HarnessProcess(services=process_services),
    )
    client = AppServerClient(client_side, run_peer=server.serve)
    try:
        await client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "t",
                    "version": "1",
                    "entrypoint": DESKTOP["entrypoint"],
                },
                "account": DESKTOP["account"],
                "capabilities": {"resumeLeftTurns": True},
            },
        )
        await client.notify("initialized", {})
        read = await client.request("session/leftTurns/read", {})
    finally:
        await client.close()

    assert read == {"status": "not_resuming", "turns": []}


class _Process:
    """One app-server process: its services, and a multiplexed server on them."""

    def __init__(self, services: ProcessServices) -> None:
        self.services = services
        self.server = _Resuming(services)

    async def close(self) -> None:
        from vibe.app_server._left_turns import close_left_turns_first

        await self.server.close()
        await close_left_turns_first(
            self.services.left_turns, self.services.open_sessions
        )


def _counting_model_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []
    held = _local_actions.execute_completion

    async def counting(action: Any, *args: Any, **kwargs: Any) -> Any:
        sent.append(action.action_id)
        return await held(action, *args, **kwargs)

    monkeypatch.setattr(_local_actions, "execute_completion", counting)
    return sent


async def _until_read(
    processes: list[_Process], channel: str, expected: list[list[str]]
) -> list[Any]:
    reads: list[Any] = []
    for _ in range(150):
        reads = [
            await process.server.rpc(channel, "session/leftTurns/read", {})
            for process in processes
        ]
        if sorted([turn["state"] for turn in read["turns"]] for read in reads) == (
            sorted(expected)
        ):
            return reads
        await asyncio.sleep(0.02)
    raise AssertionError(f"the left turn never read as {expected}: {reads}")


@pytest.mark.asyncio
async def test_two_processes_opting_in_for_one_owner_resume_its_left_turn_once(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from tests.stubs.process_services import fake_process_services

    # Prepare: alice's left turn; two app servers on one store, as Desktop
    # runs several.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    sent = _counting_model_calls(monkeypatch)
    processes = [_Process(process_services), _Process(fake_process_services())]

    try:
        # Do: both opt in for her at once.
        await asyncio.gather(
            *(_open_resuming(process.server, "1", DESKTOP) for process in processes)
        )
        reads = await _until_read(processes, "1", [["running"], []])
        await asyncio.sleep(0.3)
        resumed_once = list(sent)

        # Assert: one resume; the process that runs it alone reports it.
        holder, other = processes if reads[0]["turns"] else processes[::-1]
        assert resumed_once == sent[:1] and len(resumed_once) == 1
        assert (await other.server.rpc("1", "session/leftTurns/read", {}))[
            "turns"
        ] == []

        # Do: the holder exits; the other one's owner opts in again.
        await holder.close()
        await _open_resuming(other.server, "2", DESKTOP)
        await _until_read([other], "2", [["running"]])

        # Assert: it took the left turn over, and resumed it.
        assert len(sent) == 2
        assert (await other.server.rpc("2", "session/leftTurns/read", {}))[
            "status"
        ] == "listed"
    finally:
        for process in processes:
            await process.close()
        await processes[1].services.close()


@pytest.mark.asyncio
async def test_a_process_that_does_not_resume_the_owner_s_turns_stops_none(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from tests.stubs.process_services import fake_process_services

    # Prepare: one process carries alice's left turn on; another opts in after.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    holder = _Process(process_services)
    other = _Process(fake_process_services())
    try:
        await _open_resuming(holder.server, "1", DESKTOP)
        await _until_running_with_no_client(holder.server, "1")
        await _open_resuming(other.server, "1", DESKTOP)
        await _until_read([other], "1", [[]])

        # Do: Desktop stops her left turns on each process.
        elsewhere = await other.server.rpc("1", "session/leftTurns/interrupt", {})
        still = await holder.server.rpc("1", "session/leftTurns/read", {})
        stopped = await holder.server.rpc("1", "session/leftTurns/interrupt", {})

        # Assert: only the process that runs it stops it.
        assert elsewhere["turns"] == []
        assert [turn["state"] for turn in still["turns"]] == ["running"]
        assert stopped["turns"] == [{"sessionId": session_id, "outcome": "interrupted"}]
    finally:
        await holder.close()
        await other.close()
        await other.services.close()


@pytest.mark.asyncio
async def test_a_left_turn_another_process_held_is_resumed_once_it_lets_it_go(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from tests.stubs.process_services import fake_process_services
    from vibe.app_server import _left_turns_claim
    from vibe.app_server._left_turns import close_left_turns_first

    # Prepare: the server is about to resume alice's left turn when a chat in
    # another process opens it, its turn running there.
    monkeypatch.setattr(_left_turns_claim, "_LEASE_POLL_S", 0.05)
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    sent = _counting_model_calls(monkeypatch)
    gate = asyncio.Event()
    holder = _Resuming(process_services, gate)
    chat_services = fake_process_services()
    chat = _Multiplexed(chat_services)
    try:
        await _open_resuming(holder, "1", DESKTOP)
        await asyncio.wait_for(holder.resuming.wait(), 5)
        await chat.open("1", **DESKTOP)
        await chat.rpc("1", "session/resume", {"sessionId": session_id})
        async with asyncio.timeout(5):
            while len(sent) < 1:
                await asyncio.sleep(0.02)
        gate.set()
        await _until_read_as(holder, ["running_elsewhere"])

        # Do: that process exits, its turn still running.
        await chat.close()
        await close_left_turns_first(
            chat_services.left_turns, chat_services.open_sessions
        )
        await _until_read_as(holder, ["running"])

        # Assert: the server resumed it, with one model call of its own.
        assert len(sent) == 2
    finally:
        gate.set()
        await holder.close()
        await chat_services.close()


async def _until_read_as(server: _Multiplexed, states: list[str]) -> None:
    read: Any = None
    for _ in range(150):
        read = await server.rpc("1", "session/leftTurns/read", {})
        if [turn["state"] for turn in read["turns"]] == states:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"the left turn never read as {states}: {read}")


@pytest.mark.asyncio
async def test_a_turn_left_after_the_holder_listed_is_resumed_by_another_process(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from tests.app_server.test_interrupted_session_listing import _answer_then_hold
    from tests.stubs.process_services import fake_process_services
    from vibe.app_server import _left_turns

    # Prepare: alice's first left turn, resumed by one process that keeps it.
    monkeypatch.setattr(_left_turns, "_CLAIM_RETRY_S", 0.05)
    _calls, first_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    holder = _Process(process_services)
    crashed_services = fake_process_services()
    crashed = _Multiplexed(crashed_services)
    later = _Process(fake_process_services())
    try:
        await _open_resuming(holder.server, "1", DESKTOP)
        await _until_running_with_no_client(holder.server, "1")

        # Do: another of her processes dies mid-turn, after that listing; a
        # new process opts in for her.
        calls = _answer_then_hold(monkeypatch)
        calls[0].set()
        await crashed.open("1", **DESKTOP)
        second_id = await crashed.start("1", tmp_path)
        await crashed.turn("1", second_id, "work on it")
        await asyncio.wait_for(calls[1].wait(), timeout=10)
        await crashed.close()
        await crashed_services.close()
        sent = _counting_model_calls(monkeypatch)
        await _open_resuming(later.server, "1", DESKTOP)
        await _until_read([later], "1", [["running"]])
        later_read = await later.server.rpc("1", "session/leftTurns/read", {})
        holder_read = await holder.server.rpc("1", "session/leftTurns/read", {})

        # Assert: the new process resumes the later turn, the first keeps its own.
        assert later_read["turns"] == [{"sessionId": second_id, "state": "running"}]
        assert holder_read["turns"] == [{"sessionId": first_id, "state": "running"}]
        assert len(sent) == 1
    finally:
        await holder.close()
        await later.close()
        await later.services.close()


@pytest.mark.asyncio
async def test_a_left_turn_that_ended_is_not_read_as_running_elsewhere_once_reopened(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from tests.stubs.process_services import fake_process_services

    # Prepare: alice's left turn resumed by one process, to its end; a second
    # process opts in for her too.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    monkeypatch.setattr(_local_actions, "execute_completion", _answer_at_once)
    holder = _Process(process_services)
    other = _Process(fake_process_services())
    chat_services = fake_process_services()
    chat = _Multiplexed(chat_services)
    try:
        await _open_resuming(holder.server, "1", DESKTOP)
        await _open_resuming(other.server, "1", DESKTOP)
        await _until_completed(holder.server, "1", session_id)

        # Do: once it stopped there, she just opens that chat in a third one.
        await chat.open("1", **DESKTOP)
        opened: Any = None
        for _ in range(250):
            opened = await chat.request(
                "1", "session/resume", {"sessionId": session_id}
            )
            if "error" not in opened:
                break
            await asyncio.sleep(0.02)
        assert opened is not None and "error" not in opened, opened
        read = await other.server.rpc("1", "session/leftTurns/read", {})

        # Assert: open is not running: no left turn reads as running elsewhere.
        assert read["turns"] == []
    finally:
        await chat.close()
        await chat_services.close()
        await holder.close()
        await other.close()
        await other.services.close()


@pytest.mark.asyncio
async def test_the_server_lists_its_owner_s_left_turns_after_the_opting_in_channel_closed(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    from vibe.app_server import _left_turns

    # Prepare: alice's left turn; her opt-in waits behind another process's claim.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    monkeypatch.setattr(_local_actions, "execute_completion", _answer_at_once)
    monkeypatch.setattr(_left_turns, "_CLAIM_RETRY_S", 0.05)
    held = asyncio.Event()

    class _HeldOnce:
        def __init__(self, claim: Any) -> None:
            self._claim = claim

        def __getattr__(self, name: str) -> Any:
            return getattr(self._claim, name)

        async def acquire(self) -> bool:
            if not held.is_set():
                held.set()
                return False
            return await self._claim.acquire()

    from vibe.app_server import server as server_module

    claim_type = server_module.LeftTurnsClaim
    monkeypatch.setattr(
        server_module,
        "LeftTurnsClaim",
        lambda *args, **kwargs: _HeldOnce(claim_type(*args, **kwargs)),
    )
    second = _Resuming(process_services)
    await _open_resuming(second, "1", DESKTOP)
    await asyncio.wait_for(held.wait(), 5)

    # Do: her channel closes before the claim frees up, and no other opts in.
    await second.client.send({"channelId": "1", "message": None})
    await asyncio.wait_for(second.resuming.wait(), 5)
    chat = _Multiplexed(process_services)
    await chat.open("2", **DESKTOP)
    read = await _until_completed(chat, "2", session_id)
    await chat.close()
    await second.close()

    # Assert: the process listed and carried the turn on all the same.
    assert read["state"]["turns"][-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_a_left_turn_reads_as_resuming_while_the_server_resumes_it(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # Prepare: the server's own resume of alice's left turn is open, not done.
    _calls, session_id = await _left_mid_turn_by(
        DESKTOP, config_dir, tmp_path, monkeypatch, process_services
    )
    opened = asyncio.Event()
    held = asyncio.Event()

    async def hold(action: Any, *args: Any, **kwargs: Any) -> Any:
        opened.set()
        await held.wait()
        return await _answer_at_once(action, *args, **kwargs)

    monkeypatch.setattr(_local_actions, "execute_completion", hold)
    second = _Resuming(process_services)
    try:
        await _open_resuming(second, "1", DESKTOP)
        await asyncio.wait_for(opened.wait(), 10)

        # Do
        read = await second.rpc("1", "session/leftTurns/read", {})

        # Assert: it is the server's own, so it reads as carried on.
        assert [turn["sessionId"] for turn in read["turns"]] == [session_id]
        assert read["turns"][0]["state"] in {"resuming", "running"}
    finally:
        held.set()
        await second.close()
