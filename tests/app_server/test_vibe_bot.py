from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import threading
from typing import Any, cast

import httpx2
import pytest

from vibe.app_server._connection_protocol import ClientCapabilities
from vibe.app_server._session_peers import SessionPeerError
from vibe.app_server._session_reply import ReplyRoute, send_reply
from vibe.app_server._vibe_bot import (
    VibeBotCallRefused,
    VibeBotClient,
    VibeBotController,
    VibeBotLocalSessionGone,
    VibeBotLocalSessions,
    _bot_thread_of_sender,
    bot_sender_id,
)
from vibe.app_server._vibe_bot_tools import LocalTools
from vibe.app_server.protocol import (
    ProtocolErrorCode,
    SessionMessageSender,
    SessionPeerSendParams,
    SessionPeerSendResponse,
)
from vibe.app_server.server import _ClientRequestFailed, _VibeBotClientRequests
from vibe.core.platform_auth import ApiKeyPlatformCredentialProvider
from vibe.core.types import OwedReply

ORCH = "01a1e9c9-97cb-734e-944f-fdbc80cb80d2"
BOT = "vibe-bot--u1--default"
BASE = "https://api.mistral.ai/v2/agents/sessions"


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def _sse_response(body: bytes | AsyncIterator[bytes]) -> httpx2.Response:
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content=body
    )


def _callbacks_stream(
    read: Callable[[], list[dict[str, Any]]], ended: Callable[[], bool] = lambda: False
) -> httpx2.Response:
    """The Controller's stream: the open callbacks again whenever they change."""

    async def body() -> AsyncIterator[bytes]:
        yield b": ping\n\n"
        sent: list[dict[str, Any]] | None = None
        while not ended():
            current = copy.deepcopy(read())
            if current != sent:
                sent = current
                yield _sse("callbacks", {"callbacks": current})
            await asyncio.sleep(0.005)
        yield _sse("session_updated", {"sessionId": ORCH, "status": "terminated"})

    return _sse_response(body())


class _FakeSessionApi:
    def __init__(self, callbacks: list[dict[str, Any]]) -> None:
        self.callbacks = callbacks
        self.answers: list[dict[str, Any]] = []
        self.reads = 0
        self.stream_opens = 0
        self.ended = False

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if request.method == "GET" and url == f"{BASE}/{ORCH}/callbacks":
            self.reads += 1
            return httpx2.Response(200, json={"callbacks": self.callbacks})
        if request.method == "GET" and url == f"{BASE}/{ORCH}/callbacks/stream":
            self.stream_opens += 1
            return _callbacks_stream(lambda: self.callbacks, lambda: self.ended)
        if request.method == "POST" and url == f"{BASE}/{ORCH}/callback/result":
            result = json.loads(request.content)["result"]
            self.answers.append(result)
            self.callbacks = [
                c for c in self.callbacks if c["callback"]["id"] != result["callbackId"]
            ]
            return httpx2.Response(200, json={})
        return httpx2.Response(404)


def _call(callback_id: str, tool: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "sessionId": ORCH,
        "agentPath": "/root",
        "callback": {
            "id": callback_id,
            "kind": "client_tool",
            "name": f"bot.{tool}",
            "toolCallId": f"tc-{callback_id}",
            "input": tool_input,
        },
    }


class _FakeClient(VibeBotClient):
    """Desktop: starts and messages local sessions, and passes reports to the bot's workflow."""

    def __init__(
        self,
        *,
        duplicate: bool = False,
        claim_refused: str | None = None,
        session_peers: bool = True,
    ) -> None:
        self.session_peers = session_peers
        self.started: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.archived: list[str] = []
        self.reports: list[dict[str, Any]] = []
        self.report_bots: list[str] = []
        self.duplicate = duplicate
        self.claim_refused = claim_refused
        self.failing_starts = 0
        self.failing_sends = 0
        self.failing_reports: list[Exception] = []

    def session_tools(self) -> bool:
        return self.session_peers

    async def start_local(
        self,
        *,
        bot: str,
        idempotency_key: str,
        thread_id: str,
        prompt: str,
        title: str,
        checkout: dict[str, Any] | None,
        model: str | None = None,
        approval_policy: str | None = None,
    ) -> tuple[str, str | None]:
        if self.failing_starts:
            self.failing_starts -= 1
            raise RuntimeError("the model `nope` is not in this computer's Vibe config")
        self.started.append({
            "bot": bot,
            "key": idempotency_key,
            "thread": thread_id,
            "prompt": prompt,
            "title": title,
            "checkout": checkout,
            "model": model,
            "approval_policy": approval_policy,
        })
        return "ls-1", "turn-first"

    async def send_local(
        self,
        *,
        bot: str,
        local_session_id: str,
        thread_id: str,
        text: str,
        idempotency_key: str,
        delivery: str = "start_turn",
    ) -> str | None:
        if self.failing_sends:
            self.failing_sends -= 1
            raise RuntimeError("the local session is busy starting")
        self.sent.append({
            "bot": bot,
            "session": local_session_id,
            "thread": thread_id,
            "text": text,
            "key": idempotency_key,
            "delivery": delivery,
        })
        return f"turn-{idempotency_key}"

    async def stop_local(
        self, *, bot: str, local_session_id: str, idempotency_key: str
    ) -> None:
        self.stopped.append(local_session_id)

    async def archive_local(self, *, bot: str, local_session_id: str) -> None:
        self.archived.append(local_session_id)

    async def report(self, bot: str, event: dict[str, Any]) -> bool:
        if self.failing_reports:
            raise self.failing_reports.pop(0)
        self.reports.append(event)
        self.report_bots.append(bot)
        if event["kind"] == "message" and self.claim_refused is not None:
            raise VibeBotCallRefused(self.claim_refused)
        return self.duplicate and event["kind"] == "accepted"


def _controller(
    api: _FakeSessionApi,
    client: _FakeClient,
    sessions: VibeBotLocalSessions,
    *,
    api_origin: str = "https://api.mistral.ai",
    not_yet_grace_seconds: float = 0.0,
    project_roots: list[str] | None = None,
    reconnect_seconds: float = 0.01,
    max_reconnect_seconds: float = 0.01,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    local_tools: LocalTools | None = None,
    instance: str | None = None,
) -> VibeBotController:
    credentials = ApiKeyPlatformCredentialProvider.from_api_key("test-key")

    async def origin() -> str:
        return api_origin

    async def roots() -> list[Path]:
        return [Path(root) for root in project_roots or []]

    controller = VibeBotController(
        credentials=lambda: credentials,
        client=client,
        sessions=sessions,
        api_origin=origin,
        project_roots=roots,
        not_yet_grace_seconds=not_yet_grace_seconds,
        http=httpx2.AsyncClient(transport=httpx2.MockTransport(api.handler)),
        reconnect_seconds=reconnect_seconds,
        max_reconnect_seconds=max_reconnect_seconds,
        not_yet_retry_seconds=0.01,
        local_tools=local_tools,
        client_instance=lambda: instance,
    )
    controller.sleep = sleep
    return controller


async def _until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout)


@pytest.fixture
def sessions(tmp_path: Path) -> VibeBotLocalSessions:
    return VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")


async def _follow(
    controller: VibeBotController,
    threads: list[dict[str, Any]] | None = None,
    *,
    bot: str = BOT,
    session_ids: list[str] | None = None,
) -> str:
    response = await controller.dispatch(
        "vibebot/follow",
        {
            "botWorkflowId": bot,
            "sessionIds": session_ids if session_ids is not None else [ORCH],
            "localSessions": threads or [],
        },
    )
    return str(response.model_dump(by_alias=True)["clientId"])


@pytest.mark.asyncio
async def test_the_bots_calls_are_read_on_the_api_the_client_signed_in_to(
    sessions: VibeBotLocalSessions,
) -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json={"callbacks": []})

    api = _FakeSessionApi([])
    api.handler = handler  # type: ignore[method-assign]
    controller = _controller(api, _FakeClient(), sessions)
    try:
        await controller.dispatch(
            "vibebot/follow",
            {
                "botWorkflowId": BOT,
                "sessionIds": [ORCH],
                "localSessions": [],
                "apiBaseUrl": "https://api.staging.example/",
            },
        )
        await _until(lambda: bool(seen))

        assert seen[0] == (
            f"https://api.staging.example/v2/agents/sessions/{ORCH}/callbacks"
        )
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_the_bots_calls_are_read_on_the_api_of_the_signed_in_account(
    sessions: VibeBotLocalSessions,
) -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json={"callbacks": []})

    api = _FakeSessionApi([])
    api.handler = handler  # type: ignore[method-assign]
    controller = _controller(
        api, _FakeClient(), sessions, api_origin="https://api.staging.example"
    )
    try:
        await _follow(controller)
        await _until(lambda: bool(seen))

        assert (
            seen[0]
            == f"https://api.staging.example/v2/agents/sessions/{ORCH}/callbacks"
        )
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_reply_reaches_a_thread_whose_id_has_colons(
    sessions: VibeBotLocalSessions,
) -> None:
    """Staging QA of #83140: a `code_local` thread is named after its tool call (`local_typescript:146:x:0:tool:0`)."""
    thread_id = "local_typescript:146:hJrPQ7EqO:0:tool:0"
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(
            controller,
            [{"threadId": thread_id, "handle": "run-tests", "localSessionId": "ls-1"}],
        )

        await sessions.deliver_reply(
            SessionPeerSendParams(
                target_session_id=bot_sender_id(BOT, thread_id),
                text="All green.",
                idempotency_key="k",
                sender=SessionMessageSender(session_id="ls-1"),
                reply=True,
                reply_turn_id="turn-7",
            )
        )

        assert client.report_bots == [BOT]
        assert client.reports[0]["threadId"] == thread_id
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "final"),
    [(VibeBotCallRefused("no such thread"), True), (RuntimeError("offline"), False)],
)
async def test_a_reply_the_bot_refuses_is_final_and_a_lost_one_is_not(
    sessions: VibeBotLocalSessions, failure: Exception, final: bool
) -> None:
    """A refused turn end never lands, so the session stops sending it; one that
    did not reach the bot is sent again (`send_reply` reads which).
    """
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(
            controller,
            [
                {
                    "threadId": "local_cb-1",
                    "handle": "run-tests",
                    "localSessionId": "ls-1",
                }
            ],
        )
        client.failing_reports.append(failure)

        with pytest.raises(Exception) as raised:
            await sessions.deliver_reply(
                SessionPeerSendParams(
                    target_session_id=bot_sender_id(BOT, "local_cb-1"),
                    text="All green.",
                    idempotency_key="k",
                    sender=SessionMessageSender(session_id="ls-1"),
                    reply=True,
                    reply_turn_id="turn-7",
                )
            )

        assert isinstance(raised.value, SessionPeerError) is final
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_the_owed_reply_of_a_local_turn_becomes_the_bots_turn_end_with_its_turn_id(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        client_id = await _follow(
            controller,
            [
                {
                    "threadId": "local_cb-1",
                    "handle": "run-tests",
                    "localSessionId": "ls-1",
                }
            ],
        )

        response = await sessions.deliver_reply(
            SessionPeerSendParams(
                target_session_id=bot_sender_id(BOT, "local_cb-1"),
                text="All green.",
                idempotency_key="k",
                sender=SessionMessageSender(session_id="ls-1"),
                reply=True,
                reply_turn_id="turn-7",
            )
        )

        assert response.session_id == bot_sender_id(BOT, "local_cb-1")
        assert client.report_bots == [BOT]
        assert client.reports == [
            {
                "kind": "turn_ended",
                "clientId": client_id,
                "threadId": "local_cb-1",
                "localSessionId": "ls-1",
                "turnId": "turn-7",
                "result": {"outcome": "completed", "finalText": "All green."},
            }
        ]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


async def _deliver(
    controller: VibeBotController, deliveries: list[dict[str, Any]], bot: str = BOT
) -> None:
    await controller.dispatch(
        "vibebot/deliver", {"botWorkflowId": bot, "deliveries": deliveries}
    )


WAKE = {
    "kind": "send",
    "requestId": "wake-1",
    "threadId": "local_cb-1",
    "localSessionId": "ls-1",
    "text": "rerun them",
}


START = {
    "kind": "start",
    "requestId": "start-call-1",
    "threadId": "local_call-1",
    "handle": "run-tests",
    "title": "Run the tests",
    "brief": "Run the tests.",
}


@pytest.mark.asyncio
async def test_a_start_delivery_starts_one_local_session_under_its_key_and_reports_it(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        client_id = await _follow(controller)

        await _deliver(
            controller,
            [
                {
                    **START,
                    "model": "glm-5-latest-short",
                    "approvalPolicy": "smart-approve",
                }
            ],
        )

        [started] = client.started
        assert (started["bot"], started["key"], started["thread"]) == (
            BOT,
            "start-call-1",
            "local_call-1",
        )
        assert (
            started["title"] == "Run the tests"
            and "Run the tests." in started["prompt"]
        )
        assert (started["model"], started["approval_policy"]) == (
            "glm-5-latest-short",
            "smart-approve",
        )
        # Benjamin's review: a peer's message is a request, and the cloud threads cannot read this computer's files.
        assert 'relation="peer"' in started["prompt"]
        assert "can't read files on this computer" in started["prompt"]
        assert "`send_to_session` with `session_id`" in started["prompt"]
        assert client.reports == [
            {
                "kind": "accepted",
                "clientId": client_id,
                "requestId": "start-call-1",
                "threadId": "local_call-1",
                "localSessionId": "ls-1",
                "turnId": "turn-first",
            }
        ]
        assert sessions.of(BOT).find("run-tests") is not None
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_follow_says_this_server_starts_local_threads(
    sessions: VibeBotLocalSessions,
) -> None:
    controller = _controller(_FakeSessionApi([]), _FakeClient(), sessions)
    try:
        response = await controller.dispatch(
            "vibebot/follow",
            {"botWorkflowId": BOT, "sessionIds": [], "localSessions": []},
        )

        assert response.model_dump(by_alias=True)["startsLocal"] is True
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


def _local(thread: str, session: str, client_id: str) -> dict[str, Any]:
    return {
        "threadId": thread,
        "handle": thread,
        "localSessionId": session,
        "clientId": client_id,
    }


async def _archived(
    controller: VibeBotController, local_sessions: list[dict[str, Any]]
) -> dict[str, Any]:
    response = await controller.dispatch(
        "vibebot/follow",
        {
            "botWorkflowId": BOT,
            "sessionIds": [],
            "localSessions": local_sessions,
            "ended": "archived",
        },
    )
    return response.model_dump(by_alias=True)


@pytest.mark.asyncio
async def test_a_followed_bot_is_still_followed(sessions: VibeBotLocalSessions) -> None:
    controller = _controller(_FakeSessionApi([]), _FakeClient(), sessions)
    try:
        response = await controller.dispatch(
            "vibebot/follow",
            {"botWorkflowId": BOT, "sessionIds": [], "localSessions": []},
        )

        assert response.model_dump(by_alias=True)["following"] is True
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_an_archived_bot_is_no_longer_followed_and_its_sessions_here_stop(
    sessions: VibeBotLocalSessions,
) -> None:
    """VIB2-330: the bot's run ended without this machine taking its stops
    (offline then); the client relays why, and the server decides.
    """
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        here = await _follow(controller)
        live = [_local("local_a", "ls-a", here), _local("local_b", "ls-b", "desk-2")]
        await _follow(controller, live)

        answer = await _archived(controller, live)

        assert answer["following"] is False
        assert client.stopped == ["ls-a"]
        assert not any(key[0] == BOT for key in controller._followers)
        assert sessions.of(BOT).threads == {}
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_an_archived_bot_this_server_never_followed_still_stops_its_sessions(
    sessions: VibeBotLocalSessions,
) -> None:
    """After a restart the server holds none of the bot's sessions: the
    ones the client relays, from the bot's last state, are stopped.
    """
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        here = sessions.client_id(BOT)

        answer = await _archived(controller, [_local("local_a", "ls-a", here)])

        assert answer["following"] is False
        assert client.stopped == ["ls-a"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_an_archived_bot_stops_the_sessions_of_this_desktop_profile(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, instance="/profiles/dev"
    )
    try:
        here = controller.client_id(BOT)
        other = sessions.client_id(BOT, instance="/profiles/dev-port-5182")
        live = [_local("local_a", "ls-a", here), _local("local_b", "ls-b", other)]

        answer = await _archived(controller, live)

        assert answer["clientId"] == here
        assert client.stopped == ["ls-a"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_an_archived_bot_whose_stop_failed_is_followed_until_it_stops(
    sessions: VibeBotLocalSessions,
) -> None:
    """A stop that failed is tried again on the next follow, so the client
    keeps asking.
    """
    client = _FakeClient()
    failing = [RuntimeError("the session is busy")]

    async def stop_local(
        *, bot: str, local_session_id: str, idempotency_key: str
    ) -> None:
        if failing:
            raise failing.pop()
        client.stopped.append(local_session_id)

    client.stop_local = stop_local  # type: ignore[method-assign]
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        local = [_local("local_a", "ls-a", sessions.client_id(BOT))]

        first = await _archived(controller, local)
        second = await _archived(controller, local)

        assert (first["following"], second["following"]) == (True, False)
        assert client.stopped == ["ls-a"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_an_archived_bot_whose_session_is_gone_counts_it_stopped(
    sessions: VibeBotLocalSessions,
) -> None:
    """A session the user deleted has nothing to stop: the client stops
    asking, rather than every 5 s for good.
    """
    client = _FakeClient()

    async def stop_local(
        *, bot: str, local_session_id: str, idempotency_key: str
    ) -> None:
        raise VibeBotLocalSessionGone(local_session_id)

    client.stop_local = stop_local  # type: ignore[method-assign]
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        local = [_local("local_a", "ls-a", sessions.client_id(BOT))]

        answer = await _archived(controller, local)

        assert answer["following"] is False
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_bot_that_stopped_otherwise_is_unfollowed_and_keeps_its_local_threads(
    sessions: VibeBotLocalSessions,
) -> None:
    """Its run ended (not archived): nothing to follow until the user starts
    it again, and nothing is stopped or sent again meanwhile.
    """
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        here = await _follow(controller)
        live = [_local("local_a", "ls-a", here)]
        await _follow(controller, live)

        response = await controller.dispatch(
            "vibebot/follow",
            {
                "botWorkflowId": BOT,
                "sessionIds": [],
                "localSessions": [],
                "ended": "stopped",
            },
        )

        assert response.model_dump(by_alias=True)["following"] is True
        assert not any(key[0] == BOT for key in controller._followers)
        assert set(sessions.of(BOT).threads) == {"local_a"}
        assert client.stopped == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_names_no_session_tool_the_session_will_not_have(
    sessions: VibeBotLocalSessions,
) -> None:
    """Staging QA of #83140: without `sessionPeers`, a local session has no
    `send_to_session`; its task must not tell it to call that tool.
    """
    client = _FakeClient(session_peers=False)
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller)

        await _deliver(controller, [START])

        [started] = client.started
        assert "send_to_session" not in started["prompt"]
        assert "can't read files on this computer" in started["prompt"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_that_fails_here_is_refused_with_its_reason(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    client.failing_starts = 1
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        client_id = await _follow(controller)

        await _deliver(controller, [START])

        assert client.reports == [
            {
                "kind": "refused",
                "clientId": client_id,
                "requestId": "start-call-1",
                "threadId": "local_call-1",
                "error": "the model `nope` is not in this computer's Vibe config",
            }
        ]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_the_bot_no_longer_waits_for_archives_its_session(
    sessions: VibeBotLocalSessions,
) -> None:
    """Stopped, archived, or another machine's meanwhile: no session it does not know of keeps running."""
    client = _FakeClient(duplicate=True)
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller)

        await _deliver(controller, [START])

        assert client.archived == ["ls-1"] and sessions.of(BOT).threads == {}
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "folder",
    ["/Users/ada/dev/app", "/Users/ada/dev/app/packages/web", "/Users/ada/dev/app/"],
)
async def test_a_start_runs_in_a_local_project_or_a_folder_inside_one(
    sessions: VibeBotLocalSessions, folder: str
) -> None:
    client = _FakeClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, project_roots=["/Users/ada/dev/app"]
    )
    try:
        await _follow(controller)

        await _deliver(
            controller, [{**START, "checkout": {"kind": "directory", "path": folder}}]
        )

        assert [s["checkout"]["path"] for s in client.started] == [folder]
        assert [r["kind"] for r in client.reports] == ["accepted"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "checkout",
    [
        {
            "kind": "new_worktree",
            "repository": "/Users/ada",
            "path": "/Users/ada/dev/app",
        },
        {"kind": "directory", "path": "/Users/ada", "repository": "/Users/ada/dev/app"},
        {"kind": "new_worktree", "repository": "", "path": ""},
        {"kind": "elsewhere", "path": "/Users/ada/dev/app"},
    ],
)
async def test_a_start_checks_the_folder_its_checkout_kind_names(
    sessions: VibeBotLocalSessions, checkout: dict[str, str]
) -> None:
    """Benjamin's review: Desktop runs in the field the kind names, so that field is the one checked."""
    client = _FakeClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, project_roots=["/Users/ada/dev/app"]
    )
    try:
        await _follow(controller)

        await _deliver(controller, [{**START, "checkout": checkout}])

        assert client.started == []
        assert [r["kind"] for r in client.reports] == ["refused"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_new_worktree_of_a_local_project_starts(
    sessions: VibeBotLocalSessions,
) -> None:
    checkout = {"kind": "new_worktree", "repository": "/Users/ada/dev/app"}
    client = _FakeClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, project_roots=["/Users/ada/dev/app"]
    )
    try:
        await _follow(controller)

        await _deliver(controller, [{**START, "checkout": checkout}])

        assert [s["checkout"] for s in client.started] == [checkout]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_through_a_symlink_that_leaves_the_project_is_refused(
    sessions: VibeBotLocalSessions, tmp_path: Path
) -> None:
    """Gabriel's review: a link inside a project to a folder outside it does not make that folder a project."""
    project = tmp_path / "app"
    secrets = tmp_path / "secrets"
    project.mkdir()
    secrets.mkdir()
    (project / "escape").symlink_to(secrets)
    client = _FakeClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, project_roots=[str(project)]
    )
    try:
        await _follow(controller)

        await _deliver(
            controller,
            [
                {
                    **START,
                    "checkout": {"kind": "directory", "path": str(project / "escape")},
                }
            ],
        )

        assert client.started == []
        [refused] = client.reports
        assert "not one of the user's local projects" in refused["error"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_in_a_project_reached_through_a_symlinked_root_runs(
    sessions: VibeBotLocalSessions, tmp_path: Path
) -> None:
    real = tmp_path / "real" / "app"
    real.mkdir(parents=True)
    (tmp_path / "linked").symlink_to(tmp_path / "real")
    client = _FakeClient()
    controller = _controller(
        _FakeSessionApi([]),
        client,
        sessions,
        project_roots=[str(tmp_path / "linked" / "app")],
    )
    try:
        await _follow(controller)

        await _deliver(
            controller,
            [{**START, "checkout": {"kind": "directory", "path": str(real / ".")}}],
        )

        assert [r["kind"] for r in client.reports] == ["accepted"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "folder",
    ["/Users/ada", "/Users/ada/dev/application", "/Users/ada/dev/app/../secrets", "~"],
)
async def test_a_start_outside_the_users_local_projects_is_refused_before_it_starts(
    sessions: VibeBotLocalSessions, folder: str
) -> None:
    """A trigger's payload must not pick any folder on the computer (review B1)."""
    client = _FakeClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, project_roots=["/Users/ada/dev/app"]
    )
    try:
        await _follow(controller)

        await _deliver(
            controller, [{**START, "checkout": {"kind": "directory", "path": folder}}]
        )

        assert client.started == []
        [refused] = client.reports
        assert refused["kind"] == "refused"
        assert "not one of the user's local projects" in refused["error"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_handed_back_after_a_lost_report_starts_under_the_same_key(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    client.failing_reports = [RuntimeError("the BFF is down")]
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller)

        await _deliver(controller, [START])
        await _deliver(controller, [START])

        assert [s["key"] for s in client.started] == ["start-call-1", "start-call-1"]
        assert [r["kind"] for r in client.reports] == ["accepted"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_held_delivery_is_sent_under_its_key_then_reported(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        client_id = await _follow(controller)

        await _deliver(controller, [WAKE])

        assert [(s["text"], s["key"]) for s in client.sent] == [
            ("rerun them", "wake-1")
        ]
        assert client.reports == [
            {
                "kind": "accepted",
                "clientId": client_id,
                "requestId": "wake-1",
                "threadId": "local_cb-1",
                "localSessionId": "ls-1",
                "turnId": "turn-wake-1",
            }
        ]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_deferred_delivery_is_queued_not_started(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller)

        await _deliver(controller, [{**WAKE, "delivery": "deferred"}])

        assert [s["delivery"] for s in client.sent] == ["queue"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_delivery_handed_back_after_a_crash_goes_again_under_the_same_key(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    # Delivered, then the report was lost (a crash, the BFF down): the bot
    # hands it back in its next heartbeat.
    client.failing_reports = [RuntimeError("the BFF is down")]
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller)

        await _deliver(controller, [WAKE])
        await _deliver(controller, [WAKE])

        assert [s["key"] for s in client.sent] == ["wake-1", "wake-1"]
        assert [r["requestId"] for r in client.reports] == ["wake-1"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_held_deliveries_go_in_the_order_the_bot_gives_them(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    stop = {
        "kind": "stop",
        "requestId": "stop-1",
        "threadId": "local_cb-2",
        "localSessionId": "ls-2",
    }
    try:
        await _follow(controller)

        await _deliver(controller, [stop, WAKE])

        assert client.stopped == ["ls-2"]
        assert [r["requestId"] for r in client.reports] == ["stop-1", "wake-1"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


def test_each_bot_gets_its_own_client_id_kept_across_restarts(tmp_path: Path) -> None:
    first = VibeBotLocalSessions(client_id_file=tmp_path / "id")
    ids = {
        bot: first.client_id(bot)
        for bot in ("vibe-bot--u1--default", "vibe-bot--u1--work")
    }
    again = VibeBotLocalSessions(client_id_file=tmp_path / "id")

    assert len(set(ids.values())) == 2
    assert {bot: again.client_id(bot) for bot in ids} == ids


def test_two_desktops_on_one_vibe_home_get_their_own_ids_kept_across_restarts(
    tmp_path: Path,
) -> None:
    """Two dev Desktops share the dev Vibe home: one id would make the bot take
    them for one Desktop, each taking the other's starts and tool calls.
    """
    one = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    other = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    first = one.client_id(BOT, instance="/profiles/dev")
    second = other.client_id(BOT, instance="/profiles/dev-port-5182")
    again = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")

    assert first != second
    assert again.client_id(BOT, instance="/profiles/dev") == first
    assert again.client_id(BOT, instance="/profiles/dev-port-5182") == second


def test_the_first_desktop_after_the_upgrade_keeps_the_machines_ids(
    tmp_path: Path,
) -> None:
    """Its open start cards and local tool rules are keyed on the id it had."""
    legacy = tmp_path / "vibe_bot_client_id"
    legacy.write_text(json.dumps({BOT: "d2f98138", "vibe-bot--u1--work": "w-1"}))
    sessions = VibeBotLocalSessions(client_id_file=legacy)

    assert sessions.client_id(BOT, instance="/profiles/installed") == "d2f98138"
    assert sessions.client_id(BOT, instance="/profiles/dev") != "d2f98138"
    reopened = VibeBotLocalSessions(client_id_file=legacy)
    assert reopened.client_id(BOT, instance="/profiles/installed") == "d2f98138"
    assert (
        reopened.client_id("vibe-bot--u1--work", instance="/profiles/installed")
        == "w-1"
    )
    assert reopened.client_id(BOT, instance="/profiles/dev") != "d2f98138"


def test_a_side_by_side_copy_never_takes_the_machines_ids_from_its_desktop(
    tmp_path: Path,
) -> None:
    """Bugbot on #83930: a dev Desktop's Vibe home is inside its profile, and a
    per-port copy of that profile is deleted on quit. The copy asking first must
    not keep the ids the Desktop's open start cards and tool rules are keyed on.
    """
    profile = tmp_path / "le-chat-desktop-dev"
    legacy = profile / "vibe" / "vibe_bot_client_id"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({BOT: "d2f98138"}))
    sessions = VibeBotLocalSessions(client_id_file=legacy)

    copy = sessions.client_id(
        BOT, instance=str(tmp_path / "le-chat-desktop-dev-port-5182")
    )
    reopened = VibeBotLocalSessions(client_id_file=legacy)

    assert copy != "d2f98138"
    assert reopened.client_id(BOT, instance=str(profile)) == "d2f98138"


def test_a_side_by_side_copy_on_a_shared_vibe_home_never_adopts(tmp_path: Path) -> None:
    """Felix on #83930: a dev build run with `VIBE_HOME=~/.vibe` beside the
    installed app; its per-port copy must not keep the installed app's ids.
    """
    legacy = tmp_path / "home" / ".vibe" / "vibe_bot_client_id"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({BOT: "d2f98138"}))
    app_data = tmp_path / "Application Support"
    sessions = VibeBotLocalSessions(client_id_file=legacy)

    copy = sessions.client_id(
        BOT, instance=str(app_data / "le-chat-desktop-dev-port-5182")
    )
    installed = VibeBotLocalSessions(client_id_file=legacy).client_id(
        BOT, instance=str(app_data / "le-chat-desktop")
    )

    assert copy != "d2f98138"
    assert installed == "d2f98138"


def test_app_servers_of_one_desktop_share_its_id_for_a_new_bot(tmp_path: Path) -> None:
    """A multiplexed Desktop runs several app servers: each must report under
    the id the bot knows this Desktop by, whichever minted it first.
    """
    one = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    two = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    one.client_id("vibe-bot--u1--other", instance="/profiles/dev")
    two.client_id("vibe-bot--u1--other", instance="/profiles/dev")

    assert one.client_id(BOT, instance="/profiles/dev") == two.client_id(
        BOT, instance="/profiles/dev"
    )


@pytest.mark.asyncio
async def test_each_desktop_reports_its_deliveries_under_its_own_id(
    tmp_path: Path,
) -> None:
    sessions = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    client = _FakeClient()
    here = _controller(
        _FakeSessionApi([]), client, sessions, instance="/profiles/dev-port-5182"
    )
    try:
        followed = await _follow(here, session_ids=[])
        await _deliver(here, [START])

        assert followed == sessions.client_id(BOT, instance="/profiles/dev-port-5182")
        assert followed != sessions.client_id(BOT, instance="/profiles/dev")
        assert [r["clientId"] for r in client.reports] == [followed]
        assert sessions.of(BOT).find("run-tests").client_id == followed  # type: ignore[union-attr]
    finally:
        await asyncio.gather(*here.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_the_desktops_profile_path_never_leaves_this_computer(
    sessions: VibeBotLocalSessions,
) -> None:
    """It names the OS user: the bot only ever sees the id minted from it."""
    profile = "/Users/someone/Library/Application Support/le-chat-desktop-dev"
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions, instance=profile)
    try:
        followed = await controller.dispatch(
            "vibebot/follow",
            {"botWorkflowId": BOT, "sessionIds": [], "localSessions": []},
        )
        await _deliver(controller, [START, {**WAKE, "localSessionId": "ls-1"}])

        sent = json.dumps([followed.model_dump(by_alias=True), *client.reports])
        assert client.reports
        assert "someone" not in sent and "le-chat-desktop" not in sent
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_local_sessions_message_to_its_bot_carries_its_desktops_id(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, instance="/profiles/dev-port-5182"
    )
    try:
        client_id = await _follow(controller, session_ids=[])

        await sessions.deliver_reply(
            SessionPeerSendParams(
                target_session_id=bot_sender_id(BOT, "local_cb-1"),
                text="Done?",
                idempotency_key="k-1",
                sender=SessionMessageSender(session_id="ls-1"),
            ),
            instance="/profiles/dev-port-5182",
        )

        assert client.reports[-1]["clientId"] == client_id
        assert client_id != sessions.client_id(BOT)
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_reply_goes_under_the_id_of_the_desktop_that_runs_its_session(
    sessions: VibeBotLocalSessions,
) -> None:
    """The reply may leave on a connection with no Desktop instance (a new
    channel, a reconnect): the session's own Desktop still names it.
    """
    client = _FakeClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, instance="/profiles/dev-port-5182"
    )
    try:
        client_id = await _follow(
            controller,
            [
                {
                    "threadId": "local_cb-1",
                    "handle": "run-tests",
                    "localSessionId": "ls-1",
                    "clientId": "desk-of-the-session",
                }
            ],
            session_ids=[],
        )

        await sessions.deliver_reply(
            SessionPeerSendParams(
                target_session_id=bot_sender_id(BOT, "local_cb-1"),
                text="Done.",
                idempotency_key="k-1",
                sender=SessionMessageSender(session_id="ls-1"),
                reply=True,
            )
        )

        assert client.reports[-1]["clientId"] == "desk-of-the-session"
        assert client_id != "desk-of-the-session"
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


def test_desktops_minting_at_once_agree_on_one_id(tmp_path: Path) -> None:
    """A multiplexed Desktop's app servers can mint its id at the same time."""
    barrier = threading.Barrier(8)

    def mint() -> str:
        sessions = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
        barrier.wait()
        return sessions.client_id(BOT, instance="/profiles/dev")

    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(lambda _: mint(), range(8)))

    assert len(set(ids)) == 1


def test_a_first_desktop_with_no_machine_ids_mints_its_own(tmp_path: Path) -> None:
    sessions = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")

    mine = sessions.client_id(BOT, instance="/profiles/dev")

    assert mine != sessions.client_id(BOT, instance="/profiles/dev-port-5182")
    assert not (tmp_path / "vibe_bot_client_id").exists()


class _Peers:
    def __init__(self) -> None:
        self.sent: list[SessionPeerSendParams] = []

    def client_capabilities(self) -> ClientCapabilities:
        return ClientCapabilities(session_peers=True)

    async def send_to_peer_session(
        self, params: SessionPeerSendParams
    ) -> SessionPeerSendResponse:
        self.sent.append(params)
        return SessionPeerSendResponse(
            session_id=params.target_session_id, queued=False
        )


@pytest.mark.asyncio
async def test_an_owed_reply_carries_its_turn_id_which_never_reaches_the_client() -> (
    None
):
    peers = _Peers()

    async def sender() -> SessionMessageSender:
        return SessionMessageSender(session_id="ls-1")

    async def final(_turn: str) -> str | None:
        return None

    route = ReplyRoute(
        peers=peers,  # type: ignore[arg-type]
        session_id="ls-1",
        sender=sender,
        busy=lambda: False,
        final_message=final,
    )

    assert await send_reply(
        route, OwedReply(to_session_id="bot", turn_id="turn-7", hops=1), "done"
    )

    [params] = peers.sent
    assert params.reply_turn_id == "turn-7"
    assert "replyTurnId" not in params.model_dump(by_alias=True)


@pytest.mark.asyncio
async def test_a_mid_turn_question_to_the_bot_goes_to_its_thread_as_a_message(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        client_id = await _follow(controller)

        response = await sessions.deliver_reply(
            SessionPeerSendParams(
                target_session_id=bot_sender_id(BOT, "local_cb-1"),
                text="Which policies go in the notes?",
                idempotency_key="k-1",
                sender=SessionMessageSender(session_id="ls-1"),
            )
        )

        assert response.session_id == bot_sender_id(BOT, "local_cb-1")
        assert client.reports == [
            {
                "kind": "message",
                "clientId": client_id,
                "requestId": "k-1",
                "threadId": "local_cb-1",
                "localSessionId": "ls-1",
                "text": "Which policies go in the notes?",
            }
        ]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_message_to_another_thread_goes_to_the_bot_by_handle(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller)

        response = await sessions.deliver_reply(
            SessionPeerSendParams(
                target_session_id=bot_sender_id(BOT, "local_cb-1"),
                text="FYI: tests are slow here",
                idempotency_key="k-2",
                sender=SessionMessageSender(session_id="ls-1"),
                delivery="queue",
                bot_to="fix-login",
            )
        )

        # Live QA: answering with the starter's id made the model think the
        # message went to the wrong session.
        assert response.session_id == "fix-login" and response.queued
        [report] = client.reports
        assert report["to"] == "fix-login" and report["delivery"] == "deferred"
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_message_the_bot_refuses_fails_the_send_with_its_reason(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    client.failing_reports = [RuntimeError("no thread matches `nobody`")]
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller)

        with pytest.raises(
            SessionPeerError, match="may not have reached the bot.*nobody"
        ):
            await sessions.deliver_reply(
                SessionPeerSendParams(
                    target_session_id=bot_sender_id(BOT, "local_cb-1"),
                    text="hi",
                    idempotency_key="k-3",
                    sender=SessionMessageSender(session_id="ls-1"),
                    bot_to="nobody",
                )
            )
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


OTHER_BOT = "vibe-bot--u1--work"


@pytest.mark.asyncio
async def test_a_bots_held_messages_and_deliveries_stay_its_own(
    sessions: VibeBotLocalSessions,
) -> None:
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller, bot=BOT, session_ids=[])
        await _follow(controller, bot=OTHER_BOT, session_ids=[])
        for bot in (BOT, OTHER_BOT):
            await _deliver(controller, [WAKE], bot=bot)

        assert [s["bot"] for s in client.sent] == [BOT, OTHER_BOT]
        assert client.report_bots == [BOT, OTHER_BOT]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


def test_a_local_sessions_starter_names_its_bot_and_thread() -> None:
    assert (
        bot_sender_id(BOT, "local_cb-1") == "vibebot:vibe-bot--u1--default:local_cb-1"
    )


@pytest.mark.parametrize(
    "thread_id", ["local_cb-1", "local_typescript:146:hJrPQ7EqO:0:tool:0"]
)
def test_a_local_sessions_starter_reads_back_as_its_bot_and_thread(
    thread_id: str,
) -> None:
    assert _bot_thread_of_sender(bot_sender_id(BOT, thread_id)) == (BOT, thread_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["start_local_session", "send_to_local_session"])
async def test_a_call_of_a_retired_local_session_tool_is_answered_with_what_replaced_it(
    sessions: VibeBotLocalSessions, tool: str
) -> None:
    """A bot session from before `spawn_thread(kind: "code_local")` may still call one: it must not hang."""
    api = _FakeSessionApi([
        _call("cb-old", tool, {"handle": "run-tests", "brief": "x"})
    ])
    client = _FakeClient()
    controller = _controller(api, client, sessions)
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))

        [answer] = api.answers
        assert answer["callbackId"] == "cb-old"
        assert 'spawn_thread(kind: "code_local")' in answer["error"]["message"]
        assert client.started == [] and client.sent == [] and client.reports == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_call_the_stream_shows_is_answered_without_reading_the_calls_again(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([])
    controller = _controller(api, _FakeClient(), sessions)
    try:
        await _follow(controller)
        await _until(lambda: api.stream_opens == 1)
        api.callbacks.append(_call("cb-1", "list_local_tools", {}))
        await _until(lambda: bool(api.answers))

        assert api.answers[0]["callbackId"] == "cb-1"
        assert api.reads == 1 and api.stream_opens == 1
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


class _ClosingStreamApi(_FakeSessionApi):
    """Each stream sends the open calls once, then closes without the end."""

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        if str(request.url).endswith("/callbacks/stream"):
            self.stream_opens += 1
            return _sse_response(_sse("callbacks", {"callbacks": self.callbacks}))
        return super().handler(request)


@pytest.mark.asyncio
async def test_a_stream_that_closes_is_opened_again_after_reading_the_calls(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _ClosingStreamApi([])
    controller = _controller(
        api, _FakeClient(), sessions, reconnect_seconds=60, max_reconnect_seconds=60
    )
    slept: list[float] = []
    resume = asyncio.Event()

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        await resume.wait()
        resume.clear()

    controller.sleep = sleep
    try:
        await _follow(controller)
        await _until(lambda: bool(slept))
        api.callbacks.append(_call("cb-1", "list_local_tools", {}))
        reads = api.reads
        resume.set()
        await _until(lambda: bool(api.answers))

        assert api.answers[0]["callbackId"] == "cb-1"
        assert api.reads == reads + 1 and api.stream_opens >= 2
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_the_session_ending_stops_following_it(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([])
    api.ended = True
    controller = _controller(api, _FakeClient(), sessions)
    try:
        await _follow(controller)
        [task] = [f.task for f in controller._followers.values()]
        assert task is not None
        await asyncio.wait_for(task, 2)

        assert api.stream_opens == 1 and api.reads == 1
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_failing_stream_is_retried_with_a_growing_wait(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([])
    stream_failures = [
        httpx2.ConnectError("down"),
        httpx2.Response(503),
        httpx2.Response(500),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url).endswith("/callbacks/stream") and stream_failures:
            api.stream_opens += 1
            failure = stream_failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        return _FakeSessionApi.handler(api, request)

    api.handler = handler  # type: ignore[method-assign]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        await asyncio.sleep(0)

    controller = _controller(
        api,
        _FakeClient(),
        sessions,
        reconnect_seconds=1,
        max_reconnect_seconds=3,
        sleep=sleep,
    )
    try:
        await _follow(controller)
        await _until(lambda: api.stream_opens == 4)
        api.callbacks.append(_call("cb-1", "list_local_tools", {}))
        await _until(lambda: bool(api.answers))

        assert slept == [1, 2, 3]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


def test_a_failed_client_id_save_keeps_the_ids_already_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bugbot on 97805ff: a crash mid-write must not lose a bot's id, or its local threads stop getting deliveries."""
    client_id_file = tmp_path / "vibe_bot_client_id"
    first = VibeBotLocalSessions(client_id_file=client_id_file).client_id("bot-a")
    write_text = Path.write_text

    def torn_write(path: Path, data: str, *args: Any, **kwargs: Any) -> int:
        write_text(path, data[: len(data) // 2], *args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", torn_write)
    VibeBotLocalSessions(client_id_file=client_id_file).client_id("bot-b")
    monkeypatch.undo()

    assert (
        VibeBotLocalSessions(client_id_file=client_id_file).client_id("bot-a") == first
    )


@pytest.mark.asyncio
async def test_a_question_the_bot_refuses_fails_the_local_sessions_send(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81759: a hop-limit refusal of a mid-turn question reaches the
    local session as its send's error.
    """
    client = _FakeClient(claim_refused="hops_exceeded: stop and tell the user")
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller)

        with pytest.raises(SessionPeerError, match="hops_exceeded"):
            await sessions.deliver_reply(
                SessionPeerSendParams(
                    target_session_id=bot_sender_id(BOT, "local_cb-1"),
                    text="and back to you",
                    idempotency_key="k-loop",
                    sender=SessionMessageSender(session_id="ls-1"),
                )
            )
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_whose_report_did_not_reach_the_bot_keeps_its_session_for_the_retry(
    sessions: VibeBotLocalSessions,
) -> None:
    """The bot hands the start back under the same key, which finds this session: archiving it would lose it."""
    client = _FakeClient()
    client.failing_reports = [RuntimeError("the BFF is down")]
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller)

        await _deliver(controller, [START])

        assert client.archived == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "raised"),
    [
        (ProtocolErrorCode.NOT_FOUND, VibeBotLocalSessionGone),
        (ProtocolErrorCode.INTERNAL_ERROR, RuntimeError),
    ],
)
async def test_a_session_the_client_no_longer_has_is_gone_and_any_other_failure_is_not(
    code: ProtocolErrorCode, raised: type[Exception]
) -> None:
    class _Server:
        async def _request_client(self, *_args: Any, **_kwargs: Any) -> Any:
            raise _ClientRequestFailed("No session ls-9", code)

    client = _VibeBotClientRequests(cast(Any, _Server()))

    with pytest.raises(raised):
        await client.stop_local(bot=BOT, local_session_id="ls-9", idempotency_key="k")
