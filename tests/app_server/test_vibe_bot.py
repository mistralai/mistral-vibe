from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import copy
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from vibe.app_server._connection_protocol import ClientCapabilities
from vibe.app_server._session_peers import SessionPeerError
from vibe.app_server._session_reply import ReplyRoute, send_reply
from vibe.app_server._vibe_bot import (
    VibeBotCallHeld,
    VibeBotCallRefused,
    VibeBotClient,
    VibeBotController,
    VibeBotLocalSessions,
    bot_sender_id,
)
from vibe.app_server._vibe_bot_tools import LocalTools
from vibe.app_server.protocol import (
    SessionMessageSender,
    SessionPeerSendParams,
    SessionPeerSendResponse,
)
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
        claim_taken: bool = False,
        claim_refused: str | None = None,
        claim_held: dict[str, Any] | None = None,
    ) -> None:
        self.started: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.archived: list[str] = []
        self.reports: list[dict[str, Any]] = []
        self.report_bots: list[str] = []
        self.duplicate = duplicate
        self.claim_taken = claim_taken
        self.claim_refused = claim_refused
        self.claim_held = claim_held
        self.refused_claims: dict[str, str] = {}
        self.failing_claims: set[str] = set()
        self.failing_sends = 0
        self.failing_reports: list[Exception] = []

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
        if event["kind"] == "claimed" and event["requestId"] in self.failing_claims:
            raise RuntimeError("the bot could not be reached")
        if event["kind"] == "claimed" and event["requestId"] in self.refused_claims:
            raise VibeBotCallRefused(self.refused_claims[event["requestId"]])
        if event["kind"] in {"claimed", "message"} and self.claim_refused is not None:
            raise VibeBotCallRefused(self.claim_refused)
        if event["kind"] == "claimed" and self.claim_held is not None:
            raise VibeBotCallHeld(self.claim_held)
        if event["kind"] == "claimed":
            return self.claim_taken
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
async def test_a_start_call_starts_one_local_session_reports_it_and_is_answered_once(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "start_local_session",
            {"handle": "run-tests", "brief": "Run the tests.", "link": "attached"},
        ),
        _call("cb-2", "other_tool", {}),
    ])
    client = _FakeClient()
    controller = _controller(api, client, sessions)
    try:
        client_id = await _follow(controller)
        await _until(lambda: bool(api.answers))
        await asyncio.sleep(0.05)

        [started] = client.started
        assert started["thread"] == "local_cb-1" and started["key"] == "cb-1"
        assert started["bot"] == BOT and client.report_bots == [BOT]
        assert "Run the tests." in started["prompt"] and started["title"] == "run-tests"
        # Benjamin's review: a peer's message is a request, and the cloud threads cannot read this computer's files.
        assert 'relation="peer"' in started["prompt"]
        assert "can't read files on this computer" in started["prompt"]
        assert "`send_to_session` with `session_id`" in started["prompt"]
        assert client.reports == [
            {
                "kind": "accepted",
                "clientId": client_id,
                "requestId": "cb-1",
                "threadId": "local_cb-1",
                "localSessionId": "ls-1",
                "turnId": "turn-first",
                "call": {
                    "kind": "start",
                    "callerSessionId": ORCH,
                    "handle": "run-tests",
                    "title": "run-tests",
                    "link": "attached",
                },
            }
        ]
        assert api.answers == [
            {
                "callbackId": "cb-1",
                "output": {
                    "sessionId": "ls-1",
                    "handle": "run-tests",
                    "title": "run-tests",
                },
            }
        ]
        assert [c["callback"]["id"] for c in api.callbacks] == ["cb-2"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_asks_for_the_model_and_approval_policy_the_bot_picked(
    sessions: VibeBotLocalSessions,
) -> None:
    """The app server's start caps the policy at this machine's Vibebot ceiling."""
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "start_local_session",
            {
                "handle": "run-tests",
                "brief": "Run the tests.",
                "model": "glm-5-latest-short",
                "approvalPolicy": "smart-approve",
            },
        ),
        _call("cb-2", "start_local_session", {"handle": "other", "brief": "x"}),
    ])
    client = _FakeClient()
    controller = _controller(api, client, sessions)
    try:
        await _follow(controller)
        await _until(lambda: len(api.answers) == 2)

        picks = {
            started["thread"]: (started["model"], started["approval_policy"])
            for started in client.started
        }
        assert picks == {
            "local_cb-1": ("glm-5-latest-short", "smart-approve"),
            "local_cb-2": (None, None),
        }
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
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "start_local_session",
            {
                "handle": "run-tests",
                "brief": "x",
                "checkout": {"kind": "directory", "path": folder},
            },
        )
    ])
    client = _FakeClient()
    controller = _controller(
        api, client, sessions, project_roots=["/Users/ada/dev/app"]
    )
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))

        assert [s["checkout"]["path"] for s in client.started] == [folder]
        assert "output" in api.answers[0]
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
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "start_local_session",
            {"handle": "run-tests", "brief": "x", "checkout": checkout},
        )
    ])
    client = _FakeClient()
    controller = _controller(
        api, client, sessions, project_roots=["/Users/ada/dev/app"]
    )
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))

        assert client.started == []
        assert api.answers[0]["error"]["code"] == "callback_failed"
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_new_worktree_of_a_local_project_starts(
    sessions: VibeBotLocalSessions,
) -> None:
    checkout = {"kind": "new_worktree", "repository": "/Users/ada/dev/app"}
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "start_local_session",
            {"handle": "run-tests", "brief": "x", "checkout": checkout},
        )
    ])
    client = _FakeClient()
    controller = _controller(
        api, client, sessions, project_roots=["/Users/ada/dev/app"]
    )
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))

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
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "start_local_session",
            {
                "handle": "run-tests",
                "brief": "x",
                "checkout": {"kind": "directory", "path": str(project / "escape")},
            },
        )
    ])
    client = _FakeClient()
    controller = _controller(api, client, sessions, project_roots=[str(project)])
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))

        assert client.started == []
        assert (
            "not one of the user's local projects" in api.answers[0]["error"]["message"]
        )
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_in_a_project_reached_through_a_symlinked_root_runs(
    sessions: VibeBotLocalSessions, tmp_path: Path
) -> None:
    real = tmp_path / "real" / "app"
    real.mkdir(parents=True)
    (tmp_path / "linked").symlink_to(tmp_path / "real")
    folder = str(real / ".")
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "start_local_session",
            {
                "handle": "run-tests",
                "brief": "x",
                "checkout": {"kind": "directory", "path": folder},
            },
        )
    ])
    client = _FakeClient()
    controller = _controller(
        api, client, sessions, project_roots=[str(tmp_path / "linked" / "app")]
    )
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))

        assert "output" in api.answers[0]
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
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "start_local_session",
            {
                "handle": "run-tests",
                "brief": "x",
                "checkout": {"kind": "directory", "path": folder},
            },
        )
    ])
    client = _FakeClient()
    controller = _controller(
        api, client, sessions, project_roots=["/Users/ada/dev/app"]
    )
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))

        assert client.started == [] and client.reports == []
        [answer] = api.answers
        assert answer["error"]["code"] == "callback_failed"
        assert "not one of the user's local projects" in answer["error"]["message"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


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
async def test_a_second_desktop_archives_its_copy_and_leaves_the_call_to_the_first(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([
        _call("cb-1", "start_local_session", {"handle": "run-tests", "brief": "x"})
    ])
    client = _FakeClient(duplicate=True)
    controller = _controller(api, client, sessions)
    try:
        await _follow(controller)
        await _until(lambda: bool(client.archived))
        await asyncio.sleep(0.05)

        assert client.archived == ["ls-1"]
        assert api.answers == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_deferred_send_is_queued_into_the_session_and_reported_as_a_steer(
    sessions: VibeBotLocalSessions,
) -> None:
    """VIB2-163: Desktop's queue joins a running turn, or waits in the session's
    context for its next one; it never starts a turn.
    """
    thread = {"threadId": "local_cb-1", "handle": "run-tests", "localSessionId": "ls-1"}
    api = _FakeSessionApi([
        _call(
            "cb-2",
            "send_to_local_session",
            {"session": "run-tests", "text": "FYI: CI is red", "delivery": "deferred"},
        )
    ])
    client = _FakeClient()
    controller = _controller(api, client, sessions)
    try:
        client_id = await _follow(controller, [thread])
        await _until(lambda: len(api.answers) == 1)

        assert client.sent == [
            {
                "bot": BOT,
                "session": "ls-1",
                "thread": "local_cb-1",
                "text": "FYI: CI is red",
                "key": "cb-2",
                "delivery": "queue",
            }
        ]
        assert client.reports == [
            {
                "kind": "claimed",
                "clientId": client_id,
                "requestId": "cb-2",
                "threadId": "local_cb-1",
                "callerSessionId": ORCH,
            },
            {
                "kind": "accepted",
                "clientId": client_id,
                "requestId": "cb-2",
                "threadId": "local_cb-1",
                "localSessionId": "ls-1",
                "turnId": "turn-cb-2",
                "call": {
                    "kind": "send",
                    "callerSessionId": ORCH,
                    "text": "FYI: CI is red",
                    "delivery": "deferred",
                },
            },
        ]
        assert api.answers[0]["output"] == {"sessionId": "ls-1", "delivered": "queued"}
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_send_to_an_unknown_session_fails_the_call(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([
        _call("cb-2", "send_to_local_session", {"session": "nope", "text": "x"})
    ])
    client = _FakeClient()
    controller = _controller(api, client, sessions)
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))

        [answer] = api.answers
        assert (
            answer["error"]["code"] == "callback_failed"
            and "nope" in answer["error"]["message"]
        )
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
OTHER_ORCH = "01a1e9c9-0000-7000-8000-00000000000b"


class _TwoBotsApi(_FakeSessionApi):
    """Two bots of one user, each with its orchestrator's open calls."""

    def __init__(self, calls: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__([])
        self.by_session = calls

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        for session_id, calls in self.by_session.items():
            if request.method == "GET" and url == f"{BASE}/{session_id}/callbacks":
                return httpx2.Response(200, json={"callbacks": calls})
            if (
                request.method == "GET"
                and url == f"{BASE}/{session_id}/callbacks/stream"
            ):
                return _callbacks_stream(
                    lambda session_id=session_id: self.by_session[session_id]
                )
            if (
                request.method == "POST"
                and url == f"{BASE}/{session_id}/callback/result"
            ):
                result = json.loads(request.content)["result"]
                self.answers.append(result)
                self.by_session[session_id] = [
                    c for c in calls if c["callback"]["id"] != result["callbackId"]
                ]
                return httpx2.Response(200, json={})
        return httpx2.Response(404)


def _call_of(
    session_id: str, callback_id: str, tool: str, tool_input: dict[str, Any]
) -> dict[str, Any]:
    return {**_call(callback_id, tool, tool_input), "sessionId": session_id}


@pytest.mark.asyncio
async def test_two_bots_of_one_user_are_followed_and_reported_apart(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _TwoBotsApi({
        ORCH: [
            _call_of(
                ORCH,
                "cb-a",
                "start_local_session",
                {"handle": "run-tests", "brief": "a"},
            )
        ],
        OTHER_ORCH: [
            _call_of(
                OTHER_ORCH,
                "cb-b",
                "start_local_session",
                {"handle": "run-tests", "brief": "b"},
            )
        ],
    })
    client = _FakeClient()
    controller = _controller(api, client, sessions)
    try:
        await _follow(controller, bot=BOT, session_ids=[ORCH])
        await _follow(controller, bot=OTHER_BOT, session_ids=[OTHER_ORCH])
        await _until(lambda: len(api.answers) == 2)

        assert sorted((s["bot"], s["key"]) for s in client.started) == [
            (BOT, "cb-a"),
            (OTHER_BOT, "cb-b"),
        ]
        assert sorted(
            zip(
                client.report_bots,
                [r["requestId"] for r in client.reports],
                strict=True,
            )
        ) == [(BOT, "cb-a"), (OTHER_BOT, "cb-b")]

        # Following one bot again leaves the other bot's sessions followed.
        await _follow(controller, bot=BOT, session_ids=[ORCH])
        api.by_session[OTHER_ORCH] = [
            _call_of(
                OTHER_ORCH,
                "cb-c",
                "send_to_local_session",
                {"session": "run-tests", "text": "more"},
            )
        ]
        await _until(lambda: len(api.answers) == 3)
        assert client.sent[-1]["bot"] == OTHER_BOT
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


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


class _FlakyResultApi(_FakeSessionApi):
    """Fails the first answer of each call with a 500, as a Session API blip."""

    def __init__(self, callbacks: list[dict[str, Any]]) -> None:
        super().__init__(callbacks)
        self.failed: set[str] = set()

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST":
            callback_id = json.loads(request.content)["result"]["callbackId"]
            if callback_id not in self.failed:
                self.failed.add(callback_id)
                return httpx2.Response(500)
        return super().handler(request)


RUN_TESTS = {"threadId": "local_cb-1", "handle": "run-tests", "localSessionId": "ls-1"}


@pytest.mark.asyncio
async def test_a_send_to_a_session_another_desktop_runs_is_left_to_that_desktop(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([
        _call("cb-2", "send_to_local_session", {"session": "run-tests", "text": "x"})
    ])
    client = _FakeClient()
    controller = _controller(api, client, sessions)
    try:
        controller.not_yet_grace_seconds = 60
        await _follow(controller, [{**RUN_TESTS, "clientId": "other-desktop"}])
        await asyncio.sleep(0.1)

        assert api.answers == [] and client.sent == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_start_the_bot_refuses_archives_its_session_and_fails_the_call(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([
        _call("cb-1", "start_local_session", {"handle": "run-tests", "brief": "x"})
    ])
    client = _FakeClient()
    client.failing_reports = [RuntimeError("`run-tests` is taken; pick another handle")]
    controller = _controller(api, client, sessions)
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))

        assert client.archived == ["ls-1"]
        assert "is taken" in api.answers[0]["error"]["message"]
        assert sessions.of(BOT).threads == {}
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_send_to_an_archived_session_fails_without_reaching_it(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([])
    client = _FakeClient()
    controller = _controller(api, client, sessions)
    try:
        await _follow(controller, [RUN_TESTS])
        # The bot archived it: Desktop's next follow no longer lists it.
        await _follow(controller, [])
        api.callbacks.append(
            _call(
                "cb-2", "send_to_local_session", {"session": "run-tests", "text": "x"}
            )
        )
        await _until(lambda: bool(api.answers))

        assert "error" in api.answers[0]
        assert client.sent == [] and client.reports == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


START_RUN_TESTS = {"handle": "run-tests", "brief": "Run the tests."}


@pytest.mark.asyncio
async def test_a_call_the_stream_shows_is_answered_without_reading_the_calls_again(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([])
    controller = _controller(api, _FakeClient(), sessions)
    try:
        await _follow(controller)
        await _until(lambda: api.stream_opens == 1)
        api.callbacks.append(_call("cb-1", "start_local_session", START_RUN_TESTS))
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
        api.callbacks.append(_call("cb-1", "start_local_session", START_RUN_TESTS))
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
        api.callbacks.append(_call("cb-1", "start_local_session", START_RUN_TESTS))
        await _until(lambda: bool(api.answers))

        assert slept == [1, 2, 3]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_call_this_machine_cannot_serve_yet_is_tried_again_without_a_new_frame(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([
        _call("cb-2", "send_to_local_session", {"session": "run-tests", "text": "x"})
    ])
    client = _FakeClient()
    controller = _controller(api, client, sessions, not_yet_grace_seconds=60)
    try:
        await _follow(controller)
        await asyncio.sleep(0.05)
        assert api.answers == []
        # The session another machine started shows up here; no call changed.
        await _follow(controller, [RUN_TESTS])
        await _until(lambda: bool(api.answers))

        assert api.answers[0]["output"]["sessionId"] == "ls-1"
        assert [s["key"] for s in client.sent] == ["cb-2"]
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
async def test_texts_an_older_build_held_are_queued_into_their_session_then_the_file_goes(
    tmp_path: Path,
) -> None:
    """gabriel/vb-review: on the upgrade, a deferred text an older build kept in
    `vibe_bot_held.json` reaches its session (queued, never a turn), not dropped.
    """
    stale = tmp_path / "vibe_bot_held.json"
    stale.write_text(json.dumps({BOT: {"ls-1": {"cb-2": "FYI: CI is red"}}}))
    sessions = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    client = _FakeClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller, [RUN_TESTS])
        await _until(lambda: bool(client.sent))

        assert [(s["session"], s["text"], s["delivery"]) for s in client.sent] == [
            ("ls-1", "FYI: CI is red", "queue")
        ]
        assert not stale.exists()
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


def test_texts_an_older_build_held_outlive_a_restart_before_they_are_queued(
    tmp_path: Path,
) -> None:
    """Felix and Bugbot on 5c3eb641: the file stays until its texts are queued."""
    held = tmp_path / "vibe_bot_held.json"
    held.write_text(json.dumps({BOT: {"ls-1": {"cb-2": "FYI: CI is red"}}}))

    VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")

    assert json.loads(held.read_text()) == {BOT: {"ls-1": {"cb-2": "FYI: CI is red"}}}


@pytest.mark.asyncio
async def test_a_held_text_whose_queueing_fails_stays_for_the_next_follow(
    tmp_path: Path,
) -> None:
    held = tmp_path / "vibe_bot_held.json"
    held.write_text(json.dumps({BOT: {"ls-1": {"cb-2": "FYI: CI is red"}}}))
    sessions = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    client = _FakeClient()
    client.failing_sends = 1
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller, [RUN_TESTS])
        await asyncio.sleep(0.05)
        assert client.sent == [] and held.exists()

        await _follow(controller, [RUN_TESTS])
        await _until(lambda: bool(client.sent))
        await _until(lambda: not held.exists())

        assert [s["text"] for s in client.sent] == ["FYI: CI is red"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


WORKER = "01a1e9c9-0000-7000-8000-00000000000c"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("send_to_local_session", {"session": "run-tests", "text": "x"}),
        ("start_local_session", START_RUN_TESTS),
    ],
)
async def test_a_workers_local_session_call_is_refused_before_it_reaches_a_session(
    sessions: VibeBotLocalSessions, tool: str, tool_input: dict[str, Any]
) -> None:
    """Only the orchestrator drives local sessions: a worker's call fails
    without starting or messaging one, so its retry cannot deliver twice.
    """
    api = _TwoBotsApi({ORCH: [], WORKER: [_call_of(WORKER, "cb-w", tool, tool_input)]})
    client = _FakeClient()
    controller = _controller(api, client, sessions)
    try:
        await controller.dispatch(
            "vibebot/follow",
            {
                "botWorkflowId": BOT,
                "sessionIds": [ORCH, WORKER],
                "orchestratorSessionId": ORCH,
                "localSessions": [RUN_TESTS],
            },
        )
        await _until(lambda: bool(api.answers))

        [answer] = api.answers
        assert "only the orchestrator" in answer["error"]["message"]
        assert client.sent == [] and client.started == [] and client.reports == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_send_the_bot_already_answered_is_not_delivered_again(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #80995: the bot held the message while this Desktop was
    away; delivering it too would send it twice.
    """
    api = _FakeSessionApi([
        _call("cb-3", "send_to_local_session", {"session": "ls-1", "text": "go"})
    ])
    client = _FakeClient(claim_taken=True)
    controller = _controller(api, client, sessions)
    try:
        await _follow(controller, [RUN_TESTS])
        await _until(lambda: bool(client.reports))
        await asyncio.sleep(0.05)

        assert [r["kind"] for r in client.reports] == ["claimed"]
        assert client.sent == [] and api.answers == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_send_whose_claim_does_not_reach_the_bot_is_not_delivered_until_it_does(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([
        _call("cb-3", "send_to_local_session", {"session": "ls-1", "text": "go"})
    ])
    client = _FakeClient()
    client.failing_reports = [RuntimeError("the BFF is down")]
    controller = _controller(api, client, sessions, not_yet_grace_seconds=60)
    try:
        await _follow(controller, [RUN_TESTS])
        await _until(lambda: bool(api.answers))

        assert [r["kind"] for r in client.reports] == ["claimed", "accepted"]
        assert [s["key"] for s in client.sent] == ["cb-3"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_send_that_fails_gives_its_claim_back_before_it_is_tried_again(
    sessions: VibeBotLocalSessions,
) -> None:
    api = _FakeSessionApi([
        _call("cb-3", "send_to_local_session", {"session": "ls-1", "text": "go"})
    ])
    client = _FakeClient()
    client.failing_sends = 1
    controller = _controller(api, client, sessions, not_yet_grace_seconds=60)
    try:
        await _follow(controller, [RUN_TESTS])
        await _until(lambda: bool(api.answers))

        kinds = [(r["kind"], r["requestId"]) for r in client.reports]
        assert kinds == [
            ("claimed", "cb-3"),
            ("refused", "cb-3"),
            ("claimed", "cb-3"),
            ("accepted", "cb-3"),
        ]
        assert [s["key"] for s in client.sent] == ["cb-3"]
        assert api.answers[0]["output"]["delivered"] == "started_or_joined_turn"
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_send_the_bot_refuses_to_claim_fails_at_once(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81759: a hop-limit refusal was retried as if the bot were
    unreachable, so a later user message could still let it through.
    """
    api = _FakeSessionApi([
        _call("cb-3", "send_to_local_session", {"session": "ls-1", "text": "go"})
    ])
    client = _FakeClient(claim_refused="hops_exceeded: stop and tell the user")
    controller = _controller(api, client, sessions, not_yet_grace_seconds=60)
    try:
        await _follow(controller, [RUN_TESTS])
        await _until(lambda: bool(api.answers))

        [answer] = api.answers
        assert answer["error"]["message"].startswith("hops_exceeded")
        assert client.sent == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


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
async def test_a_send_the_bot_held_for_this_machine_is_answered_with_it_and_not_delivered(
    sessions: VibeBotLocalSessions,
) -> None:
    """The bot held the message while this machine was away: the call gets that answer, and the heartbeat brings
    the held copy, the only one.
    """
    api = _FakeSessionApi([
        _call("cb-3", "send_to_local_session", {"session": "ls-1", "text": "go"})
    ])
    held = {"sessionId": "ls-1", "delivered": "held_until_reconnect"}
    client = _FakeClient(claim_held=held)
    controller = _controller(api, client, sessions)
    try:
        await _follow(controller, [RUN_TESTS])
        await _until(lambda: bool(api.answers))

        assert api.answers == [{"callbackId": "cb-3", "output": held}]
        assert client.sent == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_held_text_the_bot_answered_since_is_dropped_and_the_others_queued(
    tmp_path: Path,
) -> None:
    """A text #81759's build held with its caller is claimed again before it
    is queued: one the bot failed meanwhile is dropped, not delivered.
    """
    held = tmp_path / "vibe_bot_held.json"
    held.write_text(
        json.dumps({
            BOT: {
                "ls-1": {
                    "cb-2": {"text": "FYI: CI is red", "caller": ORCH},
                    "cb-4": {"text": "stale", "caller": ORCH},
                    "cb-5": "from an even older build",
                }
            }
        })
    )
    sessions = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    client = _FakeClient()
    client.refused_claims["cb-4"] = "local_host_unreachable: it may already have"
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller, [RUN_TESTS])
        await _until(lambda: not held.exists())

        [sent] = client.sent
        assert sent["delivery"] == "queue"
        assert sent["text"] == "FYI: CI is red\n\nfrom an even older build"
        assert [r["requestId"] for r in client.reports] == ["cb-2", "cb-4"]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)
