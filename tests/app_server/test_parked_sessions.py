"""A session whose channel closes mid-turn keeps running, and a later resume
on any channel of the process attaches to it.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
import fcntl
import json
from pathlib import Path
import tomllib
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
import respx
import tomli_w

from tests.constants import (
    CHAT_COMPLETIONS_PATH,
    CONNECTORS_BOOTSTRAP_PATH,
    MISTRAL_BASE_URL,
)
from tests.stubs.mistral_responses import mistral_sse_payload
from vibe.app_server.models import RunningSessionStatus
from vibe.app_server.stdio import serve_stdio
from vibe.app_server.transport import (
    JsonRpcTransport,
    StdioJsonRpcTransport,
    memory_transport_pair,
)

_WAIT_S = 5

_QUESTION_CALL = {
    "id": "question-1",
    "index": 0,
    "function": {
        "name": "ask_user_question",
        "arguments": (
            '{"questions":[{"question":"Ship it?",'
            '"options":[{"label":"Yes"},{"label":"No"}]}]}'
        ),
    },
}

type Frame = dict[str, Any] | None


def _sse(
    content: str, tool_calls: list[dict[str, Any]] | None = None
) -> httpx.Response:
    return httpx.Response(
        200,
        stream=httpx.ByteStream(stream=mistral_sse_payload(content, tool_calls)),
        headers={"Content-Type": "text/event-stream"},
    )


_ORG_A = "5dd1788a-dd1b-43f5-b95c-aeb48676a276"
_ORG_B = "9a1f3c52-0b7e-4d6a-8f21-3c4d5e6f7a8b"
_WORKSPACE = "f8539dce-729c-4200-a52c-88208cebc654"


def _id_token(subject: str) -> str:
    def part(value: Mapping[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    claims = {"iss": "https://auth.mistral.ai", "aud": "desktop", "sub": subject}
    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


class _Multiplex:
    """Plays Desktop main over one multiplexed stream: requests per channel,
    and every frame the server sends, kept by channel.
    """

    def __init__(self, transport: JsonRpcTransport) -> None:
        self._transport = transport
        self._next_id = 0
        self._answers: dict[tuple[str, int], asyncio.Future[dict[str, Any]]] = {}
        self.received: dict[str, list[Frame]] = {}
        self._arrived = asyncio.Condition()
        # The token each channel lends; None answers ``transient``.
        self.lends: dict[str, str | None] = {}
        # Channels that lend an ID token, by the organization they name.
        self.id_token_orgs: dict[str, str] = {}
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for frame in self._transport.messages():
            channel = frame["channelId"]
            message = frame["message"]
            async with self._arrived:
                self.received.setdefault(channel, []).append(message)
                self._arrived.notify_all()
            if (
                message is not None
                and message.get("method") == "platformCredentials/resolve"
            ):
                await self._lend(channel, message)
                continue
            if message is None or "method" in message:
                continue
            answer = self._answers.pop((channel, message.get("id")), None)
            if answer is not None and not answer.done():
                answer.set_result(message)

    async def call(
        self, channel: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        self._next_id += 1
        answer = asyncio.get_running_loop().create_future()
        self._answers[channel, self._next_id] = answer
        await self._transport.send({
            "channelId": channel,
            "message": {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": method,
                "params": params,
            },
        })
        try:
            return await asyncio.wait_for(answer, _WAIT_S)
        except TimeoutError:
            raise AssertionError(
                f"No answer to {method} on channel {channel}"
            ) from None

    async def request(
        self, channel: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        message = await self.call(channel, method, params)
        assert "error" not in message, message
        return message["result"]

    async def _lend(self, channel: str, request: dict[str, Any]) -> None:
        token = self.lends.get(channel)
        result: dict[str, Any] = (
            {"status": "unavailable", "reason": "transient"}
            if token is None
            else {
                "status": "ready",
                "kind": "oauth_access_token",
                "token": token,
                "audience": request["params"]["audience"],
                "expiresAt": 2_000_000_000_000,
                "revision": f"{token}-revision",
            }
        )
        if token is not None and channel in self.id_token_orgs:
            result |= {
                "kind": "oidc_id_token",
                "token": _id_token(token),
                "organizationId": self.id_token_orgs[channel],
                "workspaceId": _WORKSPACE,
            }
        with suppress(Exception):
            await self._transport.send({
                "channelId": channel,
                "message": {"jsonrpc": "2.0", "id": request["id"], "result": result},
            })

    async def initialize(
        self,
        channel: str,
        callback_kinds: tuple[str, ...] = ("user_input",),
        *,
        account: str | None = None,
        lends: str | None = None,
        id_token_org: str | None = None,
        client_tools: dict[str, Any] | None = None,
    ) -> None:
        capabilities: dict[str, Any] = {
            "callbackKinds": list(callback_kinds),
            **(client_tools or {}),
        }
        params: dict[str, Any] = {
            "clientInfo": {"name": "attach-test", "version": "1"},
            "capabilities": capabilities,
        }
        if account is not None:
            self.lends[channel] = lends
            capabilities["platformCredentials"] = {
                "version": 1,
                "kind": "oauth_access_token"
                if id_token_org is None
                else "oidc_id_token",
            }
            params["clientInfo"]["entrypoint"] = "desktop"
            params["account"] = {
                "organizationId": "org-1" if id_token_org is None else _ORG_A,
                "workspaceId": "ws-1" if id_token_org is None else _WORKSPACE,
                "userId": account,
            }
            if id_token_org is not None:
                self.id_token_orgs[channel] = id_token_org
        await self.request(channel, "initialize", params)
        await self._transport.send({
            "channelId": channel,
            "message": {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        })

    async def accept(self, channel: str, call: dict[str, Any]) -> None:
        await self._transport.send({
            "channelId": channel,
            "message": {
                "jsonrpc": "2.0",
                "id": call["id"],
                "result": {"callbackId": call["params"]["callback"]["callbackId"]},
            },
        })

    async def close_channel(self, channel: str) -> None:
        await self._transport.send({"channelId": channel, "message": None})

    async def wait_for(
        self, channel: str, what: str, predicate: Callable[[Frame], bool]
    ) -> Frame:
        async def find() -> Frame:
            async with self._arrived:
                while True:
                    for message in self.received.get(channel, []):
                        if predicate(message):
                            return message
                    await self._arrived.wait()

        try:
            return await asyncio.wait_for(find(), _WAIT_S)
        except TimeoutError:
            raise AssertionError(f"No {what} on channel {channel}") from None

    async def wait_for_method(self, channel: str, method: str) -> dict[str, Any]:
        message = await self.wait_for(
            channel,
            method,
            lambda frame: frame is not None and frame.get("method") == method,
        )
        assert message is not None
        return message

    def methods(self, channel: str) -> list[str]:
        return [
            message["method"]
            for message in self.received.get(channel, [])
            if message is not None and "method" in message
        ]

    async def aclose(self) -> None:
        await self._transport.close()
        self._reader.cancel()
        await asyncio.gather(self._reader, return_exceptions=True)


@pytest.fixture
def persistent_sessions(config_dir: Path, tmp_path: Path) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {
        "enabled": True,
        "save_dir": str(tmp_path / "sessions"),
    }
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


@pytest.fixture
def model(respx_mock: respx.MockRouter) -> respx.Route:
    respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}").mock(
        return_value=httpx.Response(200, json={"connectors": []})
    )
    respx_mock.get(f"{MISTRAL_BASE_URL}/v1/users/me").mock(
        return_value=httpx.Response(401)
    )
    respx_mock.get("https://console.mistral.ai/api/vibe/whoami").mock(
        return_value=httpx.Response(401)
    )
    route = respx_mock.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}")
    route.mock(side_effect=[_sse("", [_QUESTION_CALL]), _sse("Shipped")])
    return route


@pytest_asyncio.fixture
async def multiplex(
    persistent_sessions: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_Multiplex]:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    peer, transport = memory_transport_pair()
    monkeypatch.setattr(
        StdioJsonRpcTransport, "from_standard_streams", lambda: transport
    )
    serving = asyncio.create_task(serve_stdio(multiplex=True))
    client = _Multiplex(peer)
    try:
        yield client
    finally:
        await client.aclose()
        await serving


async def _start_waiting_on_a_question(
    multiplex: _Multiplex, channel: str, client_tools: dict[str, Any] | None = None
) -> str:
    await multiplex.initialize(channel, client_tools=client_tools)
    started = await multiplex.request(
        channel,
        "session/start",
        {"agentConfig": {"enabledTools": ["ask_user_question"]}},
    )
    session_id = started["state"]["session"]["id"]
    await multiplex.request(
        channel,
        "turn/start",
        {"sessionId": session_id, "message": [{"type": "text", "text": "ask me"}]},
    )
    await multiplex.accept(
        channel, await multiplex.wait_for_method(channel, "callback/call")
    )
    return session_id


async def _close(multiplex: _Multiplex, channel: str) -> None:
    await multiplex.close_channel(channel)
    await multiplex.wait_for(channel, "close", lambda frame: frame is None)


async def _answer_yes(
    multiplex: _Multiplex, channel: str, session_id: str, call: dict[str, Any]
) -> None:
    await multiplex.accept(channel, call)
    await multiplex.request(
        channel,
        "callback/result",
        {
            "sessionId": session_id,
            "result": {
                "callbackId": call["params"]["callback"]["callbackId"],
                "output": {
                    "type": "user_input",
                    "result": {"answers": [{"question": "Ship it?", "answer": "Yes"}]},
                },
            },
        },
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_a_turn_keeps_running_after_its_channel_closes(
    multiplex: _Multiplex, tmp_path: Path
) -> None:
    # Prepare: channel 1's turn waits on the user.
    session_id = await _start_waiting_on_a_question(multiplex, "1")

    # Do: channel 1 closes, then channel 2 resumes the session.
    await _close(multiplex, "1")
    assert _held(_lease(tmp_path, session_id))
    await multiplex.initialize("2")
    resumed = await multiplex.request("2", "session/resume", {"sessionId": session_id})

    # Assert: the same turn is still running, its question still open.
    assert resumed["state"]["session"]["id"] == session_id
    assert resumed["state"]["turns"][-1]["status"] == "in_progress"
    await multiplex.wait_for_method("2", "turn/started")
    call = await multiplex.wait_for_method("2", "callback/call")
    await _answer_yes(multiplex, "2", session_id, call)
    await multiplex.wait_for_method("2", "turn/completed")


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_an_idle_session_still_stops_when_its_channel_closes(
    multiplex: _Multiplex, tmp_path: Path
) -> None:
    # Prepare: a session with no turn.
    await multiplex.initialize("1")
    started = await multiplex.request("1", "session/start", {"agentConfig": {}})
    session_id = started["state"]["session"]["id"]

    # Do
    await _close(multiplex, "1")

    # Assert: its lease is released, as before.
    assert not _held(_lease(tmp_path, session_id))


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_a_parked_session_stops_once_its_turn_ends(
    multiplex: _Multiplex, tmp_path: Path
) -> None:
    # Prepare: channel 1's turn is parked, then channel 2 finishes it.
    session_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")
    await multiplex.initialize("2")
    await multiplex.request("2", "session/resume", {"sessionId": session_id})
    call = await multiplex.wait_for_method("2", "callback/call")
    await _answer_yes(multiplex, "2", session_id, call)
    await multiplex.wait_for_method("2", "turn/completed")

    # Do
    await _close(multiplex, "2")

    # Assert: its lease is released, and a new channel opens it from the store.
    await _released(_lease(tmp_path, session_id))
    await multiplex.initialize("3")
    resumed = await multiplex.request("3", "session/resume", {"sessionId": session_id})
    assert resumed["state"]["turns"][-1]["status"] == "completed"


def _lease(tmp_path: Path, session_id: str) -> Path:
    return tmp_path / "sessions" / "active" / f"{session_id}.lock"


async def _released(lock: Path, timeout_s: float = 5) -> None:
    for _ in range(int(timeout_s * 10)):
        if not _held(lock):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{lock.name} is still held")


def _held(lock: Path) -> bool:
    if not lock.exists():
        return False
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


@pytest.fixture
def short_credential_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    from mistralai_vibe_local_harness.vibe import _completion, _credential_wait

    wait = _credential_wait.resolve_credential

    async def resolve(credentials: Any, *, wait_s: float, notices: Any) -> Any:
        del wait_s
        return await wait(credentials, wait_s=4.0, notices=notices)

    monkeypatch.setattr(_credential_wait, "_CHECK_S", 0.5)
    monkeypatch.setattr(_completion, "resolve_credential", resolve)


@pytest.fixture
def no_tenant_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)


@pytest.fixture
def signed_in_model(respx_mock: respx.MockRouter) -> respx.Route:
    respx_mock.get(url__regex=r".*").mock(return_value=httpx.Response(401))
    route = respx_mock.post(f"{MISTRAL_BASE_URL}{CHAT_COMPLETIONS_PATH}")
    route.mock(side_effect=lambda _request: _sse("Shipped"))
    return route


async def _start_waiting_on_its_sign_in(
    multiplex: _Multiplex, channel: str, account: str, *, initialized: bool = False
) -> str:
    """A turn whose model call waits: its own channel cannot lend yet."""
    if not initialized:
        await multiplex.initialize(channel, account=account, lends=None)
    started = await multiplex.request(channel, "session/start", {"agentConfig": {}})
    session_id = started["state"]["session"]["id"]
    await multiplex.request(
        channel,
        "turn/start",
        {"sessionId": session_id, "message": [{"type": "text", "text": "ship"}]},
    )
    await multiplex.wait_for_method(channel, "turn/started")
    return session_id


def _bearers(route: respx.Route) -> list[str]:
    return [call.request.headers.get("Authorization", "") for call in route.calls]


async def _turn_status(multiplex: _Multiplex, channel: str, session_id: str) -> str:
    await multiplex.initialize(channel)
    resumed = await multiplex.request(
        channel, "session/resume", {"sessionId": session_id}
    )
    return resumed["state"]["turns"][-1]["status"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_credential_wait")
async def test_a_parked_turn_borrows_its_account_s_sign_in_from_another_channel(
    multiplex: _Multiplex, signed_in_model: respx.Route, tmp_path: Path
) -> None:
    # Prepare: the catalogue channel of alice lends; her chat's turn waits.
    await multiplex.initialize("1", account="alice", lends="alice-token")
    session_id = await _start_waiting_on_its_sign_in(multiplex, "2", "alice")

    # Do: her chat closes.
    await _close(multiplex, "2")
    await _released(_lease(tmp_path, session_id), timeout_s=15)

    # Assert: the turn called the model with the catalogue's sign-in and ended.
    assert set(_bearers(signed_in_model)) == {"Bearer alice-token"}
    assert await _turn_status(multiplex, "3", session_id) == "completed"


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.usefixtures("short_credential_wait")
async def test_a_parked_turn_with_no_channel_of_its_account_waits_then_fails(
    multiplex: _Multiplex, signed_in_model: respx.Route, tmp_path: Path
) -> None:
    # Prepare: only bob's channel lends; alice's chat's turn waits.
    await multiplex.initialize("1", account="bob", lends="bob-token")
    session_id = await _start_waiting_on_its_sign_in(multiplex, "2", "alice")

    # Do: her chat closes, and nothing of hers comes back within the wait.
    await _close(multiplex, "2")
    await _released(_lease(tmp_path, session_id), timeout_s=15)

    # Assert: bob never lent to her; the turn failed without calling the model.
    assert signed_in_model.call_count == 0
    assert await _turn_status(multiplex, "3", session_id) == "failed"


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.usefixtures("short_credential_wait", "no_tenant_override")
@pytest.mark.parametrize(("org", "called"), [(_ORG_A, True), (_ORG_B, False)])
async def test_a_parked_turn_borrows_an_id_token_only_for_its_organization(
    multiplex: _Multiplex,
    signed_in_model: respx.Route,
    tmp_path: Path,
    org: str,
    called: bool,
) -> None:
    # Prepare: a same-account channel lends an ID token naming ``org``.
    await multiplex.initialize("1", account="carol", lends="carol", id_token_org=org)
    await multiplex.initialize("2", account="carol", id_token_org=_ORG_A)
    session_id = await _start_waiting_on_its_sign_in(
        multiplex, "2", "carol", initialized=True
    )

    # Do: her chat closes.
    await _close(multiplex, "2")
    await _released(_lease(tmp_path, session_id), timeout_s=15)

    # Assert: only a token of the account's organization reached the model.
    assert (signed_in_model.call_count > 0) is called


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_credential_wait")
async def test_another_account_s_channel_cannot_attach_a_parked_session(
    multiplex: _Multiplex,
) -> None:
    # Prepare: alice's chat closes while its turn waits.
    session_id = await _start_waiting_on_its_sign_in(multiplex, "1", "alice")
    await _close(multiplex, "1")

    # Do: bob's channel resumes it.
    await multiplex.initialize("2", account="bob", lends="bob-token")
    refused = await multiplex.call("2", "session/resume", {"sessionId": session_id})

    # Assert: refused; the turn would otherwise run on bob's sign-in.
    assert refused["error"]["code"] == "conflict"


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_credential_wait")
async def test_a_parked_turn_goes_on_when_its_chat_reopens_in_time(
    multiplex: _Multiplex, signed_in_model: respx.Route
) -> None:
    # Prepare: alice's only channel closes while its turn waits.
    session_id = await _start_waiting_on_its_sign_in(multiplex, "1", "alice")
    await _close(multiplex, "1")

    # Do: her chat reopens within the wait, and lends.
    await multiplex.initialize("2", account="alice", lends="alice-token")
    resumed = await multiplex.request("2", "session/resume", {"sessionId": session_id})

    # Assert: the same turn calls the model with the new channel's sign-in.
    assert resumed["state"]["turns"][-1]["status"] == "in_progress"
    await multiplex.wait_for_method("2", "turn/completed")
    assert set(_bearers(signed_in_model)) == {"Bearer alice-token"}


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.usefixtures("short_credential_wait", "signed_in_model")
async def test_a_parked_session_whose_failed_turn_left_a_queue_stops(
    multiplex: _Multiplex, tmp_path: Path
) -> None:
    # Prepare: alice's turn waits for her sign-in, with a follow-up queued.
    session_id = await _start_waiting_on_its_sign_in(multiplex, "1", "alice")
    await multiplex.request(
        "1",
        "session/turn/enqueue",
        {
            "sessionId": session_id,
            "entries": [
                {"role": "user", "content": [{"type": "text", "text": "then this"}]}
            ],
        },
    )

    # Do: her chat closes and no channel of hers lends within the wait.
    await _close(multiplex, "1")

    # Assert: the failed turn pauses the queue; the session stops all the same.
    await _released(_lease(tmp_path, session_id), timeout_s=8)


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_resuming_a_parked_session_to_interrupt_its_turn_ends_it(
    multiplex: _Multiplex,
) -> None:
    # Prepare: channel 1's turn waits on the user, then channel 1 closes.
    session_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")

    # Do: channel 2 resumes it asking to end the turn left running.
    await multiplex.initialize("2")
    resumed = await multiplex.request(
        "2", "session/resume", {"sessionId": session_id, "interruptLeftTurn": True}
    )

    # Assert: the resume shows the turn ended as interrupted.
    assert resumed["state"]["session"]["status"]["type"] == "idle"
    assert resumed["state"]["activeCallbacks"] == []
    assert resumed["state"]["turns"][-1]["status"] == "interrupted"


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_a_channel_with_a_session_cannot_attach_a_parked_one(
    multiplex: _Multiplex,
) -> None:
    # Prepare: a parked session, and channel 2 with a session of its own.
    parked_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")
    await multiplex.initialize("2")
    await multiplex.request("2", "session/start", {"agentConfig": {}})

    # Do
    refused = await multiplex.call("2", "session/resume", {"sessionId": parked_id})

    # Assert: refused, and the session stays parked for a free channel.
    assert refused["error"]["code"] == "conflict"
    await multiplex.initialize("3")
    resumed = await multiplex.request("3", "session/resume", {"sessionId": parked_id})
    assert resumed["state"]["turns"][-1]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_a_turn_that_ended_before_its_interrupt_still_attaches() -> None:
    from vibe.app_server._parked_sessions import interrupt_running_turn
    from vibe.app_server._session_backend_port import SessionBackendError
    from vibe.app_server.protocol import ProtocolErrorCode

    class _EndedTurn:
        session_id = "s"

        async def read(self, params: object) -> object:
            return SimpleNamespace(
                state=SimpleNamespace(
                    session=SimpleNamespace(
                        status=RunningSessionStatus(active_turn_id="turn-1")
                    )
                )
            )

        async def interrupt_turn(self, params: object) -> object:
            raise SessionBackendError(
                ProtocolErrorCode.CONFLICT, "The turn already ended"
            )

    await interrupt_running_turn(cast(Any, _EndedTurn()))


@pytest.mark.asyncio
async def test_an_interrupt_that_fails_for_another_reason_fails_the_attach() -> None:
    from vibe.app_server._parked_sessions import interrupt_running_turn
    from vibe.app_server._session_backend_port import SessionBackendError
    from vibe.app_server.protocol import ProtocolErrorCode

    class _Unreachable:
        session_id = "s"

        async def read(self, params: object) -> object:
            return SimpleNamespace(
                state=SimpleNamespace(
                    session=SimpleNamespace(
                        status=RunningSessionStatus(active_turn_id="turn-1")
                    )
                )
            )

        async def interrupt_turn(self, params: object) -> object:
            raise SessionBackendError(ProtocolErrorCode.INTERNAL_ERROR, "store down")

    with pytest.raises(SessionBackendError):
        await interrupt_running_turn(cast(Any, _Unreachable()))


@pytest.mark.asyncio
async def test_an_interrupt_settles_once_its_turn_ends_not_the_queue_after_it() -> None:
    from vibe.app_server._parked_sessions import interrupt_running_turn

    class _QueuedFollowUp:
        session_id = "s"
        has_turn_work = True

        def __init__(self) -> None:
            self.status: object = RunningSessionStatus(active_turn_id="turn-1")

        async def read(self, params: object) -> object:
            return SimpleNamespace(
                state=SimpleNamespace(session=SimpleNamespace(status=self.status))
            )

        async def interrupt_turn(self, params: object) -> object:
            self.status = RunningSessionStatus(active_turn_id="turn-2")
            return SimpleNamespace(after_response=None)

    session = _QueuedFollowUp()

    await asyncio.wait_for(interrupt_running_turn(cast(Any, session)), 1)


def _browser_tools(tmp_path: Path) -> dict[str, Any]:
    plugin = tmp_path / "browser"
    plugin.mkdir(exist_ok=True)
    (plugin / "plugin.json").write_text(
        json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "browser",
            "version": "1.0.0",
            "extensions": {"ai.mistral.vibe": {"schemaVersion": 1}},
        })
    )
    return {
        "clientTools": ["tools/call"],
        "agentPlugins": [{"name": "browser", "root": str(plugin)}],
        "clientToolDefinitions": [
            {
                "namespace": "browser",
                "name": "echo",
                "description": "Echo a value",
                "inputSchema": {"type": "object"},
                "outputSchema": {"type": "object"},
            }
        ],
    }


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_a_channel_that_cannot_answer_its_open_callback_leaves_it_parked(
    multiplex: _Multiplex,
) -> None:
    # Prepare: a parked session waiting on a question.
    parked_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")

    # Do: a channel that answers no question resumes it.
    await multiplex.initialize("2", callback_kinds=())
    refused = await multiplex.call("2", "session/resume", {"sessionId": parked_id})

    # Assert: refused, and a channel that can answer still attaches it.
    assert refused["error"]["code"] == "invalid_params"
    await multiplex.initialize("3")
    resumed = await multiplex.request("3", "session/resume", {"sessionId": parked_id})
    assert resumed["state"]["turns"][-1]["status"] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_an_interrupting_attach_its_client_cannot_answer_leaves_it_parked(
    multiplex: _Multiplex, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.app_server import _parked_sessions

    # Prepare: a parked session waiting on a question; its interrupt never lands.
    parked_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")

    async def unsettled(root: Any) -> None:
        return None

    monkeypatch.setattr(_parked_sessions, "interrupt_running_turn", unsettled)

    # Do: a channel that answers no question resumes it to interrupt it.
    await multiplex.initialize("2", callback_kinds=())
    refused = await multiplex.call(
        "2", "session/resume", {"sessionId": parked_id, "interruptLeftTurn": True}
    )
    monkeypatch.undo()

    # Assert: refused, and a channel that can answer still attaches it.
    assert refused["error"]["code"] == "invalid_params"
    await multiplex.initialize("3")
    resumed = await multiplex.request("3", "session/resume", {"sessionId": parked_id})
    assert resumed["state"]["turns"][-1]["status"] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_two_attaches_racing_through_their_checks_attach_it_once(
    multiplex: _Multiplex, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.app_server.server import AppServer

    # Prepare: a parked session; the first attach's check is held.
    parked_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")
    checking = asyncio.Event()
    release = asyncio.Event()
    check = AppServer._validate_open_callback_capabilities
    held: list[bool] = []

    async def held_once(self: Any, method: str, root: Any = None) -> None:
        if not held:
            held.append(True)
            checking.set()
            await release.wait()
        await check(self, method, root)

    monkeypatch.setattr(AppServer, "_validate_open_callback_capabilities", held_once)
    await multiplex.initialize("2")
    await multiplex.initialize("3")

    # Do: channel 2 is inside its check while channel 3 attaches.
    first = asyncio.create_task(
        multiplex.call("2", "session/resume", {"sessionId": parked_id})
    )
    await asyncio.wait_for(checking.wait(), _WAIT_S)
    won = await asyncio.wait_for(
        multiplex.call("3", "session/resume", {"sessionId": parked_id}), _WAIT_S
    )
    release.set()
    lost = await first

    # Assert: channel 3 holds the running session; channel 2 finds it taken.
    assert won["result"]["state"]["turns"][-1]["status"] == "in_progress"
    assert lost["error"]["code"] == "conflict", lost


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_a_channel_with_other_client_tools_cannot_attach_a_parked_one(
    multiplex: _Multiplex, tmp_path: Path
) -> None:
    # Prepare: a parked session started with no client tools.
    parked_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")

    # Do: a channel that declared client tools resumes it.
    await multiplex.initialize("2", client_tools=_browser_tools(tmp_path))
    refused = await multiplex.call("2", "session/resume", {"sessionId": parked_id})

    # Assert: refused; the session could not reach tools of its own client.
    assert refused["error"]["code"] == "conflict"


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_a_channel_without_the_session_s_client_requests_cannot_attach_it(
    multiplex: _Multiplex,
) -> None:
    # Prepare: a parked session whose client answered file reads.
    parked_id = await _start_waiting_on_a_question(
        multiplex, "1", client_tools={"clientTools": ["filesystem/read"]}
    )
    await _close(multiplex, "1")

    # Do: a channel that cannot answer them resumes it.
    await multiplex.initialize("2")
    refused = await multiplex.call("2", "session/resume", {"sessionId": parked_id})

    # Assert
    assert refused["error"]["code"] == "conflict"


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_an_attached_session_no_longer_counts_its_client_as_lost(
    multiplex: _Multiplex, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.app_server._open_sessions import OpenSessions

    parked: dict[str, Any] = {}
    park = OpenSessions.park

    def spy(self: OpenSessions, session_id: str, session: Any) -> None:
        parked[session_id] = session
        park(self, session_id, session)

    monkeypatch.setattr(OpenSessions, "park", spy)

    # Prepare: channel 1's client is lost mid-turn.
    session_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")
    host = parked[session_id].server._session_backend_host
    host.mark_client_disconnected()

    # Do: channel 2 attaches it.
    await multiplex.initialize("2")
    await multiplex.request("2", "session/resume", {"sessionId": session_id})

    # Assert: a later stop reports the session closed normally, not as lost.
    assert [adapter._client_disconnected for adapter in host._adapters.values()] == [
        False
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_a_failed_interrupt_on_attach_leaves_the_session_parked(
    multiplex: _Multiplex, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.app_server import _parked_sessions

    # Prepare: a parked session, and an interrupt that fails.
    session_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")

    async def fail(root: Any) -> None:
        raise RuntimeError("read failed")

    monkeypatch.setattr(_parked_sessions, "interrupt_running_turn", fail)

    # Do: a resume asking for the interrupt fails.
    await multiplex.initialize("2")
    failed = await multiplex.call(
        "2", "session/resume", {"sessionId": session_id, "interruptLeftTurn": True}
    )
    assert "error" in failed
    monkeypatch.undo()

    # Assert: the session is still parked; a plain resume attaches it.
    await multiplex.initialize("3")
    resumed = await multiplex.request("3", "session/resume", {"sessionId": session_id})
    assert resumed["state"]["turns"][-1]["status"] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.usefixtures("model")
async def test_a_resume_waiting_on_a_failed_attach_attaches_the_session(
    multiplex: _Multiplex, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.app_server import _parked_sessions

    # Prepare: a parked session, and an interrupt that fails after a while.
    session_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")
    interrupting = asyncio.Event()

    async def fail_later(root: Any) -> None:
        interrupting.set()
        await asyncio.sleep(0.3)
        raise RuntimeError("read failed")

    monkeypatch.setattr(_parked_sessions, "interrupt_running_turn", fail_later)
    await multiplex.initialize("2")
    await multiplex.initialize("3")

    # Do: channel 3 resumes while channel 2's attach is failing.
    failing = asyncio.create_task(
        multiplex.call(
            "2", "session/resume", {"sessionId": session_id, "interruptLeftTurn": True}
        )
    )
    await asyncio.wait_for(interrupting.wait(), _WAIT_S)
    resumed = await multiplex.request("3", "session/resume", {"sessionId": session_id})

    # Assert: channel 3 attached the running session.
    assert "error" in await failing
    assert resumed["state"]["turns"][-1]["status"] == "in_progress"


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.usefixtures("model")
async def test_a_lease_watch_keeps_reporting_after_its_channel_attaches(
    multiplex: _Multiplex,
) -> None:
    # Prepare: channel 1's turn is parked; channel 2 watches leases.
    parked_id = await _start_waiting_on_a_question(multiplex, "1")
    await _close(multiplex, "1")
    await multiplex.initialize("2")
    await multiplex.request("2", "session/lease/subscribe", {})

    # Do: channel 2 attaches the parked session, then another session starts.
    await multiplex.request("2", "session/resume", {"sessionId": parked_id})
    await multiplex.initialize("3")
    started = await multiplex.request("3", "session/start", {"agentConfig": {}})
    other_id = started["state"]["session"]["id"]

    # Assert: channel 2 still hears of the new session's lease.
    await multiplex.wait_for(
        "2",
        "lease update for the new session",
        lambda frame: (
            frame is not None
            and frame.get("method") == "session/lease/updated"
            and frame["params"]["sessionId"] == other_id
        ),
    )
