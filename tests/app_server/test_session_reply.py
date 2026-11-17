"""A session another one started replies to it when a turn that session
started ends.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import respx

from mistralai_vibe_local_harness.protocol import RustEnabledModelWait
import mistralai_vibe_local_harness.session_protocol as harness_session_protocol
from tests.app_server.test_unified_harness_backend_adapter import _harness_backend_host
from tests.conftest import build_test_vibe_config
from tests.constants import (
    CHAT_COMPLETIONS_PATH,
    CONNECTORS_BOOTSTRAP_PATH,
    MISTRAL_BASE_URL,
)
from tests.stubs.mistral_responses import mistral_completion
from vibe.app_server import _session_reply
from vibe.app_server._connection_protocol import ClientCapabilities
from vibe.app_server._session_peers import SessionPeerError
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedHarnessBackendAdapter,
)
from vibe.app_server.models import TextContentBlock
from vibe.app_server.protocol import (
    PeerSessionSummary,
    SessionArchiveParams,
    SessionListParams,
    SessionMessageSender,
    SessionPeerListParams,
    SessionPeerListResponse,
    SessionPeerSendParams,
    SessionPeerSendResponse,
    SessionReadParams,
    SessionResumeParams,
    SessionStartParams,
    SessionStartTurn,
    SessionTextContentBlock,
    TurnEnqueueParams,
    TurnStartParams,
    TurnUserInputEntry,
)
from vibe.core.config import SessionLoggingConfig

_PARENT = "parent-1"
_WAIT_SECONDS = 10.0


@pytest.fixture(autouse=True, params=["direct", "session_api"])
def _runtime_path(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Desktop sessions run on the Session API; the CLI may not."""
    if request.param == "session_api":
        monkeypatch.setenv("VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API", "true")


class _Peers:
    """Records the replies; answers nothing else."""

    def __init__(self) -> None:
        self.sends: list[SessionPeerSendParams] = []
        self.sent = asyncio.Event()

    def client_capabilities(self) -> ClientCapabilities:
        return ClientCapabilities(session_peers=True)

    async def list_peer_sessions(
        self, params: SessionPeerListParams
    ) -> SessionPeerListResponse:
        return SessionPeerListResponse(
            sessions=[
                PeerSessionSummary(session_id=session_id, status="idle")
                for session_id in params.session_ids or []
            ]
        )

    async def send_to_peer_session(
        self, params: SessionPeerSendParams
    ) -> SessionPeerSendResponse:
        self.sends.append(params)
        self.sent.set()
        return SessionPeerSendResponse(
            session_id=params.target_session_id, queued=False
        )


def _answers(respx_mock: respx.MockRouter, *texts: str) -> None:
    """The model answers each request with the next text, the last one after."""
    remaining = list(texts)

    def answer(_request: httpx.Request) -> httpx.Response:
        text = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        chunk = mistral_completion(text)
        chunk["object"] = "chat.completion.chunk"
        [choice] = chunk["choices"]
        choice["delta"] = {"role": "assistant", "content": text}
        del choice["message"]
        return httpx.Response(
            200,
            content=b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]",
            headers={"Content-Type": "text/event-stream"},
        )

    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json={"connectors": []})
    )
    respx_mock.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}").mock(
        side_effect=answer
    )


def _waits_then_answers(respx_mock: respx.MockRouter) -> list[str]:
    """The model calls `wait` for an hour, then answers; the requests it got."""
    requests: list[str] = []
    calls = iter([("wait", {"timeout_ms": 3_600_000}), ("text", "Thanks.")])

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request.content.decode())
        kind, value = next(calls, ("text", "Thanks."))
        if kind == "wait":
            delta: dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "wait-1",
                        "index": 0,
                        "type": "function",
                        "function": {"name": "wait", "arguments": json.dumps(value)},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": value}
            finish = "stop"
        chunk = mistral_completion("")
        chunk["object"] = "chat.completion.chunk"
        [choice] = chunk["choices"]
        choice["delta"] = delta
        choice["finish_reason"] = finish
        del choice["message"]
        return httpx.Response(
            200,
            content=b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]",
            headers={"Content-Type": "text/event-stream"},
        )

    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json={"connectors": []})
    )
    respx_mock.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}").mock(
        side_effect=answer
    )
    return requests


async def _waiting_turn(host: Any) -> Any:
    """A session the user wrote to, its model blocked in `wait`."""
    session = (await host.start(SessionStartParams())).backend
    turn = await session.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="Start a session and wait for it")],
        )
    )
    if turn.after_response is not None:
        turn.after_response()
    await _until(lambda: "self.wait" in str(_open_actions(session)))
    return session


def _host(tmp_path: Path, peers: _Peers, *, waits: bool = False) -> Any:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions")
        )
    )
    return cast(
        Any,
        _harness_backend_host(
            config, session_peers=peers, core_config=_offering_wait if waits else None
        ),
    )


def _offering_wait(config: Any) -> Any:
    tools = config.settings.tools.model_copy(
        update={"model_wait": RustEnabledModelWait(external_input=True)}
    )
    return config.model_copy(
        update={"settings": config.settings.model_copy(update={"tools": tools})}
    )


async def _start_child(host: Any, text: str = "Find a funny article") -> Any:
    started = await host.start(
        SessionStartParams(
            started_by_session_id=_PARENT,
            first_turn=SessionStartTurn(message=[TextContentBlock(text=text)]),
            hops=2,
        )
    )
    backend = started.backend
    turn = await backend.start_turn(
        TurnStartParams(
            session_id=backend.session_id, message=[TextContentBlock(text=text)]
        )
    )
    if turn.after_response is not None:
        turn.after_response()
    return backend


async def _send_user_turn(backend: Any, text: str) -> None:
    turn = await backend.start_turn(
        TurnStartParams(
            session_id=backend.session_id, message=[TextContentBlock(text=text)]
        )
    )
    if turn.after_response is not None:
        turn.after_response()
    await _settled(backend)


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(_WAIT_SECONDS):
        while not condition():
            await asyncio.sleep(0.02)


async def _settled(backend: Any) -> None:
    await backend._session._wait_for_pending_turns()
    await _replies_sent(backend)


async def _replies_sent(backend: Any) -> None:
    while backend._replies_in_flight:
        await asyncio.gather(
            *backend._replies_in_flight.values(), return_exceptions=True
        )


async def _send(backend: Any, params: TurnEnqueueParams) -> None:
    result = await backend.enqueue_turn(
        params.model_copy(update={"session_id": backend.session_id})
    )
    if result.after_response is not None:
        result.after_response()
    await _settled(backend)


def _open_actions(backend: Any) -> list[Any]:
    runtime = backend._session._session._runtime
    return [] if runtime is None else list(runtime.inspection.pending_actions)


def _from(sender: str) -> SessionMessageSender:
    return SessionMessageSender(session_id=sender, title="Parent")


def _message(text: str, **kwargs: Any) -> TurnEnqueueParams:
    return TurnEnqueueParams(
        session_id="unused",
        idempotency_key=f"key-{text}",
        entries=[
            TurnUserInputEntry(
                entry_id=f"key-{text}", content=[SessionTextContentBlock(text=text)]
            )
        ],
        **kwargs,
    )


@pytest.mark.asyncio
async def test_a_handed_task_replies_once_with_its_final_message(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A session another one started with a task.
    *Do*: Its task's turn ends with an answer.
    *Assert*: It sends that answer back to its starter once, as a reply that
        starts a turn there, one hop further, once nothing runs here.
    """
    # Prepare
    _answers(respx_mock, "Found one about a cat.")
    peers = _Peers()
    host = _host(tmp_path, peers)

    # Do
    child = await _start_child(host)
    await _settled(child)

    # Assert
    [reply] = peers.sends
    assert reply.target_session_id == _PARENT
    assert reply.text == "Found one about a cat."
    assert reply.reply is True
    assert reply.delivery == "start_turn"
    assert reply.hops == 3
    assert reply.sender.session_id == child.session_id
    assert not child._session.has_turn_work
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_handed_task_that_fails_at_once_still_replies(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A model that refuses every request.
    *Do*: Another session starts a session with a task.
    *Assert*: The task's turn fails at once, and its starter hears so once.
    """
    # Prepare
    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json={"connectors": []})
    )
    respx_mock.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}").mock(
        return_value=httpx.Response(400, json={"message": "Bad request"})
    )
    peers = _Peers()
    host = _host(tmp_path, peers)

    # Do
    child = await _start_child(host)
    await _settled(child)

    # Assert
    [reply] = peers.sends
    assert reply.text.startswith("Its turn failed")
    assert child._owed_replies() == []
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_turn_another_session_started_replies_to_that_session(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A started session whose task already replied.
    *Do*: Its starter sends a follow-up that starts a turn, then another
        session leaves a message for its next turn.
    *Assert*: Only the follow-up's turn replies, to its sender.
    """
    # Prepare
    _answers(respx_mock, "Done.")
    peers = _Peers()
    host = _host(tmp_path, peers)
    child = await _start_child(host)
    await _settled(child)

    # Do
    await _send(child, _message("Find another", sender=_from("other-1"), hops=1))
    await _send(child, _message("Thanks", sender=_from(_PARENT), delivery="queue"))

    # Assert
    assert [send.target_session_id for send in peers.sends] == [_PARENT, "other-1"]
    assert peers.sends[1].hops == 2
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_message_joining_a_turn_owed_to_its_sender_adds_no_reply(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A started session whose task's turn owes its starter a
        reply.
    *Do*: The starter's follow-up joins that turn, then the turn ends.
    *Assert*: One reply, one hop past the longer chain.
    """
    # Prepare
    _answers(respx_mock, "Done.")
    peers = _Peers()
    host = _host(tmp_path, peers)
    child = await _start_child(host)
    [owed] = child._owed_replies()

    # Do
    await child._owe_reply(_PARENT, owed.turn_id, 5)
    await _settled(child)

    # Assert
    [reply] = peers.sends
    assert reply.hops == 6
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_turn_the_user_or_a_reply_started_does_not_reply(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A started session whose task already replied.
    *Do*: The user writes to it; then a reply from a session it messaged
        starts a turn on it.
    *Assert*: Neither turn replies, so two sessions cannot keep replying to
        each other.
    """
    # Prepare
    _answers(respx_mock, "Done.")
    peers = _Peers()
    host = _host(tmp_path, peers)
    child = await _start_child(host)
    await _settled(child)

    # Do
    turn = await child.start_turn(
        TurnStartParams(
            session_id=child.session_id, message=[TextContentBlock(text="Hi")]
        )
    )
    if turn.after_response is not None:
        turn.after_response()
    await _settled(child)
    await _send(
        child, _message("Finished its turn.", sender=_from("other-1"), reply=True)
    )

    # Assert
    assert [send.target_session_id for send in peers.sends] == [_PARENT]
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_message_joining_a_turn_the_user_started_owes_no_reply(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A session waiting in a turn the user started.
    *Do*: A session it started asks it something mid-task, which joins that
        turn and wakes it, and the turn ends.
    *Assert*: Nothing goes back to the asking session: the user's turn
        answers the user, and only a turn a session started replies to it.
    """
    # Prepare
    requests = _waits_then_answers(respx_mock)
    peers = _Peers()
    host = _host(tmp_path, peers, waits=True)
    parent = await _waiting_turn(host)

    # Do
    await _send(parent, _message("Which language?", sender=_from("child-1")))

    # Assert
    assert "Which language?" in requests[-1]
    assert parent._owed_replies() == []
    assert peers.sends == []
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_reply_owed_when_the_app_stops_is_sent_once_the_turn_ends(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A started session that owes its starter a reply when the
        app stops: the host goes down with the reply still unsent.
    *Do*: A new host lists the sessions owing a reply, then resumes it.
    *Assert*: Asked for the sessions owing a reply, the listing names it, so
        a client knows to resume it; once resumed it sends the reply, once,
        and no longer owes it.
    """
    # Prepare
    _answers(respx_mock, "Found one about a cat.")
    stopped = _Peers()
    host = _host(tmp_path, stopped)
    child = await _start_child(host)
    child._reply_when_turn_ends = lambda _turn: None
    await child._session._wait_for_pending_turns()
    session_id = child.session_id
    await host.shutdown()
    assert stopped.sends == []

    # Do
    peers = _Peers()
    host = _host(tmp_path, peers)
    listed = await host.list(SessionListParams(owes_reply=True))
    resumed = (await host.resume(SessionResumeParams(session_id=session_id))).backend
    await _until(lambda: len(peers.sends) == 1)
    await _replies_sent(resumed)
    relisted = await host.list(SessionListParams(owes_reply=True))

    # Assert
    assert [session.id for session in listed.items] == [session_id]
    [reply] = peers.sends
    assert reply.target_session_id == _PARENT
    assert reply.text == "Found one about a cat."
    assert relisted.items == []
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_reply_the_client_could_not_take_is_sent_again(
    tmp_path: Path, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A client that refuses the first reply, as when it is busy.
    *Do*: A started session's task ends.
    *Assert*: It sends the reply again shortly, and owes nothing once taken.
    """
    # Prepare
    monkeypatch.setattr(_session_reply, "SEND_RETRY_SECONDS", 0.01)
    _answers(respx_mock, "Found one about a cat.")
    peers = _Peers()
    send = peers.send_to_peer_session
    refused: list[str] = []

    async def busy_once(params: SessionPeerSendParams) -> SessionPeerSendResponse:
        if not refused:
            refused.append(params.text)
            raise SessionPeerError("The session is busy. Try again.")
        return await send(params)

    peers.send_to_peer_session = busy_once  # type: ignore[method-assign]
    host = _host(tmp_path, peers)

    # Do
    child = await _start_child(host)
    await _settled(child)

    # Assert
    assert refused == ["Found one about a cat."]
    assert [reply.text for reply in peers.sends] == ["Found one about a cat."]
    assert child._owed_replies() == []
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_reply_still_owed_after_a_later_turn_reports_its_own_turn(
    tmp_path: Path, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A started session whose task's turn failed, its reply
        refused by the client every time, after which the user ran another
        turn in it.
    *Do*: The app restarts, and the client resumes the session.
    *Assert*: The reply reports how the task's turn ended, not the user's
        later one.
    """
    # Prepare
    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json={"connectors": []})
    )
    model = respx_mock.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}").mock(
        return_value=httpx.Response(400, json={"message": "Bad request"})
    )

    class _Refusing(_Peers):
        async def send_to_peer_session(
            self, params: SessionPeerSendParams
        ) -> SessionPeerSendResponse:
            raise SessionPeerError("The session is busy. Try again.")

    host = _host(tmp_path, _Refusing())
    child = await _start_child(host)
    await child._session._wait_for_pending_turns()
    await _until(lambda: all(r.text for r in child._owed_replies()))
    for reply in tuple(child._replies_in_flight.values()):
        reply.cancel()
    await _replies_sent(child)
    _answers(respx_mock, "Sure, here is a dog.")
    model.side_effect = None
    await _send_user_turn(child, "And a dog?")
    session_id = child.session_id
    await host.shutdown()

    # Do
    peers = _Peers()
    host = _host(tmp_path, peers)
    resumed = (await host.resume(SessionResumeParams(session_id=session_id))).backend
    await _until(lambda: len(peers.sends) == 1)
    await _replies_sent(resumed)

    # Assert
    [reply] = peers.sends
    assert reply.text.startswith("Its turn failed")
    assert "dog" not in reply.text
    assert resumed._owed_replies() == []
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_reply_saves_how_its_turn_ended_before_waiting_for_it_to_settle(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A started session still finishing its task's work after the
        turn ended.
    *Do*: The task's turn ends.
    *Assert*: The reply's text is saved with the session at once, so a
        restart during the wait still reports that turn.
    """
    # Prepare
    _answers(respx_mock, "Found one about a cat.")
    peers = _Peers()
    host = _host(tmp_path, peers)

    # Do
    child = await _start_child(host)
    child._finishing_turn = lambda: True
    await child._session._wait_for_pending_turns()
    await _until(lambda: all(r.text for r in child._owed_replies()))

    # Assert
    assert [r.text for r in child._owed_replies()] == ["Found one about a cat."]
    assert peers.sends == []
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_message_joining_an_owed_turn_keeps_its_saved_reply_text(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A started session whose task's turn ended, its reply text
        saved and its send held back by a slow client.
    *Do*: Its starter's message joins that turn again, with a longer chain.
    *Assert*: The owed reply keeps its saved text and takes the longer chain.
    """
    # Prepare
    _answers(respx_mock, "Found one about a cat.")
    peers = _Peers()
    release = asyncio.Event()
    send = peers.send_to_peer_session

    async def slow_send(params: SessionPeerSendParams) -> SessionPeerSendResponse:
        await release.wait()
        return await send(params)

    peers.send_to_peer_session = slow_send  # type: ignore[method-assign]
    host = _host(tmp_path, peers)
    child = await _start_child(host)
    await child._session._wait_for_pending_turns()
    await _until(lambda: all(r.text for r in child._owed_replies()))
    [owed] = child._owed_replies()

    # Do
    await child._owe_reply(_PARENT, owed.turn_id, owed.hops + 3)

    # Assert
    [kept] = child._owed_replies()
    assert kept.text == "Found one about a cat."
    assert kept.hops == owed.hops + 3
    release.set()
    await _settled(child)
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_reply_whose_text_could_not_be_read_is_tried_again(
    tmp_path: Path, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A started session whose first read of its ended turn fails.
    *Do*: Its task's turn ends.
    *Assert*: It reads the turn again shortly and sends the reply once.
    """
    # Prepare
    monkeypatch.setattr(_session_reply, "SEND_RETRY_SECONDS", 0.01)
    _answers(respx_mock, "Found one about a cat.")
    peers = _Peers()
    host = _host(tmp_path, peers)
    started = await host.start(
        SessionStartParams(
            started_by_session_id=_PARENT,
            first_turn=SessionStartTurn(
                message=[TextContentBlock(text="Find a funny article")]
            ),
            hops=2,
        )
    )
    child = started.backend
    read = child._final_message
    failed: list[str] = []

    async def fails_once(turn_id: str) -> str | None:
        if not failed:
            failed.append(turn_id)
            raise RuntimeError("history read failed")
        return await read(turn_id)

    child._final_message = fails_once

    # Do
    turn = await child.start_turn(
        TurnStartParams(
            session_id=child.session_id,
            message=[TextContentBlock(text="Find a funny article")],
        )
    )
    if turn.after_response is not None:
        turn.after_response()
    await child._session._wait_for_pending_turns()
    await _until(lambda: len(peers.sends) == 1)
    await _settled(child)

    # Assert
    assert len(failed) == 1
    assert [reply.text for reply in peers.sends] == ["Found one about a cat."]
    assert child._owed_replies() == []
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_reply_owed_by_a_turn_still_running_at_open_waits_for_it_to_end(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A started session whose task's turn reads as still running
        when the session opens, though no turn is active yet.
    *Do*: The session delivers the replies it owes on open.
    *Assert*: It sends nothing for that turn and still owes its reply.
    """
    # Prepare
    _answers(respx_mock, "Found one about a cat.")
    peers = _Peers()
    host = _host(tmp_path, peers)
    child = await _start_child(host)
    child._reply_when_turn_ends = lambda _turn: None
    await child._session._wait_for_pending_turns()
    [owed] = child._owed_replies()
    read = child._session.read

    async def still_running(params: Any) -> Any:
        result = await read(params)
        latest = result.snapshot.state.latest_turn
        running = harness_session_protocol.InProgressPublicTurn(
            id=latest.id, session_id=latest.session_id, started_at=latest.started_at
        )
        state = result.snapshot.state.model_copy(update={"latest_turn": running})
        snapshot = result.snapshot.model_copy(update={"state": state})
        return result.model_copy(update={"snapshot": snapshot})

    child._session.read = still_running

    # Do
    await child._deliver_replies_owed_on_open()
    await _replies_sent(child)

    # Assert
    assert peers.sends == []
    assert [r.turn_id for r in child._owed_replies()] == [owed.turn_id]
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_message_whose_response_is_never_sent_owes_no_reply(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: An idle started session.
    *Do*: Another session's message would start a turn, but its response is
        never sent, as when the app stops first.
    *Assert*: No turn runs, and the session owes no reply for it.
    """
    # Prepare
    _answers(respx_mock, "Done.")
    peers = _Peers()
    host = _host(tmp_path, peers)
    child = await _start_child(host)
    await _settled(child)

    # Do
    result = await child.enqueue_turn(
        _message("Find another", sender=_from("other-1"), hops=1).model_copy(
            update={"session_id": child.session_id}
        )
    )
    assert result.on_response_abandoned is not None
    result.on_response_abandoned()
    await _until(lambda: not child._owed_replies())
    await _settled(child)

    # Assert
    assert child._session.active_turn_id is None
    assert [send.target_session_id for send in peers.sends] == [_PARENT]
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_turn_already_running_keeps_its_reply_when_the_response_is_dropped(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: An idle started session whose runtime runs a turn before
        answering, as one behind the Session API does.
    *Do*: Another session's message starts a turn there, and the response is
        never sent.
    *Assert*: The turn still replies to that session once it ends.
    """
    # Prepare
    _answers(respx_mock, "Done.")
    peers = _Peers()
    host = _host(tmp_path, peers)
    child = await _start_child(host)
    await _settled(child)
    start = child._commands.start_turn

    async def runs_before_answering(params: Any) -> Any:
        result = await start(params)
        if result.after_response is not None:
            result.after_response()
        return replace(result, after_response=None, on_response_abandoned=None)

    child._commands.start_turn = runs_before_answering

    # Do
    result = await child.enqueue_turn(
        _message("Find another", sender=_from("other-1"), hops=1).model_copy(
            update={"session_id": child.session_id}
        )
    )
    if result.on_response_abandoned is not None:
        result.on_response_abandoned()
    await _until(lambda: len(peers.sends) == 2 and not child._owed_replies())

    # Assert
    assert [send.target_session_id for send in peers.sends] == [_PARENT, "other-1"]
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_session_whose_owed_replies_cannot_be_read_at_open_still_sends_them(
    tmp_path: Path, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A started session that owes a reply when the app stops.
    *Do*: A new host resumes it while the first read of its turns fails.
    *Assert*: The resume succeeds, and the reply still goes out once.
    """
    # Prepare
    monkeypatch.setattr(_session_reply, "SEND_RETRY_SECONDS", 0.01)
    _answers(respx_mock, "Found one about a cat.")
    host = _host(tmp_path, _Peers())
    child = await _start_child(host)
    child._reply_when_turn_ends = lambda _turn: None
    await child._session._wait_for_pending_turns()
    session_id = child.session_id
    await host.shutdown()
    deliver = UnifiedHarnessBackendAdapter._deliver_replies_owed_on_open
    failed: list[str] = []

    async def fails_once(self: Any) -> None:
        if not failed:
            failed.append(self.session_id)
            raise RuntimeError("history read failed")
        await deliver(self)

    monkeypatch.setattr(
        UnifiedHarnessBackendAdapter, "_deliver_replies_owed_on_open", fails_once
    )
    peers = _Peers()

    # Do
    host = _host(tmp_path, peers)
    resumed = (await host.resume(SessionResumeParams(session_id=session_id))).backend
    await _until(lambda: not resumed._owed_replies())

    # Assert
    assert failed == [session_id]
    assert [reply.text for reply in peers.sends] == ["Found one about a cat."]
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_reply_sent_again_after_a_crash_starts_no_second_turn(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A parent that took a reply, then a restart of its app.
    *Do*: The same reply arrives again, as when the child crashed after
        sending it and before saving that it had.
    *Assert*: The parent's model sees it once and runs no second turn.
    """
    # Prepare
    requests = _waits_then_answers(respx_mock)
    host = _host(tmp_path, _Peers(), waits=True)
    parent = await _waiting_turn(host)
    reply = _message("Found one about a cat.", sender=_from("child-1"), reply=True)
    await _send(parent, reply)
    seen = len(requests)
    session_id = parent.session_id
    await host.shutdown()

    # Do
    host = _host(tmp_path, _Peers(), waits=True)
    resumed = (await host.resume(SessionResumeParams(session_id=session_id))).backend
    await _send(resumed, reply)

    # Assert
    assert len(requests) == seen
    assert sum("Found one about a cat." in r for r in requests) == 1
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_reply_is_sent_once_even_when_its_turn_ends_twice(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A started session sending the reply its task's turn owes,
        the client slow to take it.
    *Do*: The finished turn is reported again meanwhile, as a later snapshot
        does when any of its fields changes, and again once it was sent.
    *Assert*: One reply.
    """
    # Prepare
    _answers(respx_mock, "Done.")
    peers = _Peers()
    release = asyncio.Event()
    send = peers.send_to_peer_session

    async def slow_send(params: SessionPeerSendParams) -> SessionPeerSendResponse:
        await release.wait()
        return await send(params)

    peers.send_to_peer_session = slow_send  # type: ignore[method-assign]
    host = _host(tmp_path, peers)
    child = await _start_child(host)
    await child._session._wait_for_pending_turns()
    await _until(lambda: bool(child._replies_in_flight))
    read = await child.read(SessionReadParams(session_id=child.session_id))
    turn = read.state.turns[-1]

    # Do
    child._reply_when_turn_ends(turn)
    release.set()
    await _settled(child)
    child._reply_when_turn_ends(turn)
    await _settled(child)

    # Assert
    assert len(peers.sends) == 1
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_started_session_archives_at_once_after_its_reply(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A started session whose reply just reached its starter.
    *Do*: The starter archives it right away, requiring it idle.
    *Assert*: It is archived without waiting: the reply only goes out once
        the session is idle.
    """
    # Prepare
    _answers(respx_mock, "Done.")
    peers = _Peers()
    host = _host(tmp_path, peers)
    child = await _start_child(host)
    await peers.sent.wait()

    # Do
    loop = asyncio.get_running_loop()
    began = loop.time()
    archived = await host.archive(
        SessionArchiveParams(
            session_id=child.session_id, archived=True, require_idle=True
        )
    )

    # Assert
    assert archived.archived_at is not None
    assert loop.time() - began < 1.0
    await host.shutdown()


@pytest.mark.asyncio
async def test_a_parent_waiting_through_a_restart_reads_the_reply_without_an_error(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    """*Prepare*: A session blocked in `wait` for a session it started.
    *Do*: The app stops, the session resumes, and the reply it waited for
        arrives.
    *Assert*: The model reads its interrupted wait as a timeout, never as an
        error, then the reply.
    """
    # Prepare
    requests = _waits_then_answers(respx_mock)
    host = _host(tmp_path, _Peers(), waits=True)
    parent = await _waiting_turn(host)
    session_id = parent.session_id

    # Do
    await host.shutdown()
    host = _host(tmp_path, _Peers(), waits=True)
    resumed = (await host.resume(SessionResumeParams(session_id=session_id))).backend
    await resumed._session.wait_for_recovered_turn()
    reply = await resumed.enqueue_turn(
        _message(
            "Found one about a cat.", sender=_from("child-1"), reply=True
        ).model_copy(update={"session_id": resumed.session_id})
    )
    if reply.after_response is not None:
        reply.after_response()
    await _until(lambda: len(requests) == 3)

    # Assert
    seen = "".join(requests)
    assert "effect_recovery_failed" not in seen
    assert "timeout" in requests[1]
    assert "Found one about a cat." in requests[2]
    await host.shutdown()
