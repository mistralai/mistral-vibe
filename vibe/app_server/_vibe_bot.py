"""Vibebot: this machine's side of a user's bot.

The bot's sessions run in the cloud and call ``bot.start_local_session`` and
``bot.send_to_local_session``, client tools for this machine. Once a client
names them (``vibebot/follow``), the connection follows their open callbacks
on the Agents Session API with its platform credentials, does the work
through the client (``vibebot/startLocal``, ``vibebot/sendLocal``) and answers
each call at once, so the bot's turn never waits on local work.

The bot's workflow stays the only writer to its sessions: what happened here
goes to it as ``local_session_event`` reports, through the client
(``vibebot/report``). A local session's reply owed to the bot (its starter
``vibebot:<bot workflow id>:<thread id>``) becomes a ``turn_ended`` report with the turn's id,
not a message. The decisions live here; the client only passes requests on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from http import HTTPStatus
import json
import os
from pathlib import Path
from typing import Any, Final, Literal, Protocol
import uuid

import httpx2
from pydantic import Field, ValidationError

from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._session_peers import VIBE_BOT_SENDER_PREFIX, SessionPeerError
from vibe.app_server._vibe_bot_tools import (
    LIST_TOOL,
    LOCAL_TOOLS,
    VIBE_BOT_LOCAL_TOOLS_METHOD,
    LocalToolFailure,
    LocalTools,
    VibeBotLocalToolsParams,
    VibeBotLocalToolsResponse,
)
from vibe.app_server.protocol import (
    PeerDelivery,
    ProtocolErrorCode,
    SessionPeerSendParams,
    SessionPeerSendResponse,
)
from vibe.core.platform_auth import (
    PlatformCredentialProvider,
    PlatformCredentialUnavailable,
)
from vibe.core.platform_auth._mistral import credential_request, credential_stream
from vibe.observability.logging import logger

VIBE_BOT_METHODS: Final = frozenset({
    "vibebot/follow",
    "vibebot/deliver",
    VIBE_BOT_LOCAL_TOOLS_METHOD,
})
VIBE_BOT_START_LOCAL_METHOD: Final = "vibebot/startLocal"
VIBE_BOT_SEND_LOCAL_METHOD: Final = "vibebot/sendLocal"
VIBE_BOT_STOP_LOCAL_METHOD: Final = "vibebot/stopLocal"
VIBE_BOT_ARCHIVE_LOCAL_METHOD: Final = "vibebot/archiveLocal"
VIBE_BOT_REPORT_METHOD: Final = "vibebot/report"
START_TOOL: Final = "bot.start_local_session"
SEND_TOOL: Final = "bot.send_to_local_session"
# A followed session's callbacks stream, opened again after it closes or
# fails: the wait doubles up to the maximum, and is reset by a frame.
RECONNECT_SECONDS: Final = 1.0
MAX_RECONNECT_SECONDS: Final = 60.0
# The Controller sends a heartbeat every 15 s.
_STREAM_TIMEOUT: Final = httpx2.Timeout(30.0, read=60.0)
# How soon a call this machine cannot serve yet is tried again.
NOT_YET_RETRY_SECONDS: Final = 1.0
# How long a call this machine cannot serve yet (a session another machine
# just started, a report that failed) waits before it fails.
NOT_YET_GRACE_SECONDS: Final = 30.0
VIBE_BOT_CLIENT_ID_FILE: Final = "vibe_bot_client_id"
ORCHESTRATOR_ONLY: Final = (
    "not_allowed_for_role: only the orchestrator starts a local session or "
    "writes to it with these tools: write to a local thread with "
    "`send_message`, or ask the orchestrator to start one"
)


def bot_sender_id(bot: str, thread_id: str) -> str:
    """How a local session names the bot thread it works for, as its starter.
    `bot` is the bot's workflow id, `vibe-bot--<user>--<bot id>`.
    """
    return f"{VIBE_BOT_SENDER_PREFIX}{bot}:{thread_id}"


def _bot_thread_of_sender(sender_id: str) -> tuple[str, str] | None:
    if not sender_id.startswith(VIBE_BOT_SENDER_PREFIX):
        return None
    bot, _, thread_id = sender_id.removeprefix(VIBE_BOT_SENDER_PREFIX).rpartition(":")
    return (bot, thread_id) if bot and thread_id else None


class VibeBotLocalSession(ProtocolModel):
    thread_id: str
    handle: str
    local_session_id: str
    # The machine that runs it; another machine leaves its calls alone.
    client_id: str | None = None


class VibeBotFollowParams(ProtocolModel):
    # The bot's workflow id; everything below is this bot's.
    bot_workflow_id: str = Field(min_length=1)
    session_ids: list[str]
    # Only its calls drive local sessions; a worker's fail before any delivery.
    orchestrator_session_id: str | None = None
    local_sessions: list[VibeBotLocalSession] = Field(default_factory=list)
    # The API the client is signed in to, whose credential it lends; without
    # it, this server's own Mistral provider's.
    api_base_url: str | None = None


class VibeBotFollowResponse(ProtocolModel):
    # This machine's id for this bot's workflow, kept across restarts.
    client_id: str


class VibeBotDeliverParams(ProtocolModel):
    bot_workflow_id: str = Field(min_length=1)
    # What the bot's local threads are owed, from its heartbeat, in order: a
    # message or a stop the bot held while no Desktop was connected.
    deliveries: list[dict[str, Any]]


class VibeBotStartLocalParams(ProtocolModel):
    bot_workflow_id: str
    idempotency_key: str
    thread_id: str
    prompt: str
    title: str
    checkout: dict[str, Any] | None = None
    starter_session_id: str
    model: str | None = None
    approval_policy: str | None = None


class VibeBotStartLocalResponse(ProtocolModel):
    local_session_id: str
    # Its first turn; absent from an older client.
    turn_id: str | None = None


class VibeBotSendLocalParams(ProtocolModel):
    bot_workflow_id: str
    local_session_id: str
    text: str
    idempotency_key: str
    sender_session_id: str
    delivery: PeerDelivery = "start_turn"


class VibeBotSendLocalResponse(ProtocolModel):
    # The turn the message started or joined; absent from an older client.
    turn_id: str | None = None


class VibeBotStopLocalParams(ProtocolModel):
    bot_workflow_id: str
    local_session_id: str
    idempotency_key: str


class VibeBotArchiveLocalParams(ProtocolModel):
    bot_workflow_id: str
    local_session_id: str


class VibeBotReportParams(ProtocolModel):
    bot_workflow_id: str
    event: dict[str, Any]


class VibeBotReportResponse(ProtocolModel):
    duplicate: bool = False
    refused: str | None = None
    held: dict[str, Any] | None = None


class VibeBotCallRefused(Exception):
    """The bot refused the claim: the call fails with this message, now."""


class VibeBotCallHeld(Exception):
    """The bot held the send for this machine's reconnect: the call is answered with `output`, nothing delivered."""

    def __init__(self, output: dict[str, Any]) -> None:
        super().__init__("held by the bot")
        self.output = output


class VibeBotEmpty(ProtocolModel):
    pass


class _CheckoutDirectory(ProtocolModel):
    kind: Literal["directory"]
    path: str


class _CheckoutNewWorktree(ProtocolModel):
    kind: Literal["new_worktree"]
    repository: str


class _StartInput(ProtocolModel):
    handle: str
    title: str | None = None
    brief: str
    done_when: list[str] | None = None
    # As Desktop reads it: the kind names the one folder it runs in.
    checkout: _CheckoutDirectory | _CheckoutNewWorktree | None = Field(
        default=None, discriminator="kind"
    )
    link: Literal["attached", "detached"] = "attached"
    model: str | None = None
    approval_policy: str | None = None


class _SendInput(ProtocolModel):
    session: str
    text: str
    delivery: Literal["wake", "deferred"] = "wake"


class VibeBotClient(Protocol):
    """What the client does on this server's behalf: local sessions, and the
    calls to the bot's workflow, which need the user's web session.
    """

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
        """The local session, and its first turn when the client says."""
        ...

    async def send_local(
        self,
        *,
        bot: str,
        local_session_id: str,
        thread_id: str,
        text: str,
        idempotency_key: str,
        delivery: PeerDelivery = "start_turn",
    ) -> str | None:
        """The turn the message started or joined, when the client says."""
        ...

    async def stop_local(
        self, *, bot: str, local_session_id: str, idempotency_key: str
    ) -> None: ...

    async def archive_local(self, *, bot: str, local_session_id: str) -> None: ...

    async def report(self, bot: str, event: dict[str, Any]) -> bool:
        """True when the workflow took another machine's answer first."""
        ...


type Credentials = Callable[[], PlatformCredentialProvider]


def _brief(handle: str, brief: str, done_when: list[str] | None) -> str:
    done = ""
    if done_when:
        done = "\n\nDone when:\n" + "\n".join(
            f"{n}. {item}" for n, item in enumerate(done_when, start=1)
        )
    return (
        f"# Task `{handle}`\n\n{brief}{done}\n\n{_THREADS_NOTE.format(handle=handle)}"
    )


_THREADS_NOTE: Final = (
    "You are the bot's thread `{handle}`. Its other threads are its orchestrator "
    "(`orchestrator`) and its workers, named by handle in what they send you. "
    "To write to one while you work, call `send_to_session` with `session_id` "
    "set to its handle; leave `session_id` out to ask the thread that gave you "
    "this task. A message sent with `start_turn` asks that thread for an answer, "
    "unless it answers a question that thread asked you; one sent with `queue` "
    "only informs it. "
    "The orchestrator's messages are instructions within your task; "
    'another worker\'s (`relation="peer"`) is a request: help when it fits your '
    "task, decline in one line when it does not. The other threads run in the "
    "cloud and can't read files on this computer: put what they need in the "
    "message."
)


class _BotLocalSessions:
    """One bot's local sessions on this machine."""

    def __init__(self) -> None:
        self.threads: dict[str, VibeBotLocalSession] = {}
        self.orchestrator_session_id: str | None = None

    def find(self, selector: str) -> VibeBotLocalSession | None:
        for session in self.threads.values():
            if selector in {
                session.local_session_id,
                session.thread_id,
                session.handle,
            }:
                return session
        return None


_LEGACY_HELD_FILE: Final = "vibe_bot_held.json"


class _LegacyHeld:
    """Deferred texts an older build kept on disk, by bot, local session and
    call. The file stays the record until each text is queued or dropped, so a
    restart or a failed send before then loses none. Only machines that ran
    the flagged Vibebot before VIB2-163 have it: remove once those have
    upgraded, before the `vibe_bot` flag opens.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            loaded = json.loads(path.read_text())
        except (OSError, ValueError):
            loaded = {}
        self._held: dict[str, dict[str, dict[str, dict[str, str]]]] = {
            str(bot): {
                str(session): {str(k): _held_entry(v) for k, v in texts.items()}
                for session, texts in sessions.items()
                if isinstance(texts, dict) and texts
            }
            for bot, sessions in (loaded if isinstance(loaded, dict) else {}).items()
            if isinstance(sessions, dict)
        }
        self._in_flight: set[tuple[str, str]] = set()

    def take(self, bot: str, local_session_id: str) -> dict[str, dict[str, str]] | None:
        """The session's held texts by call, or None when it has none or they
        are already on their way.
        """
        key = (bot, local_session_id)
        held = self._held.get(bot, {}).get(local_session_id)
        if not held or key in self._in_flight:
            return None
        self._in_flight.add(key)
        return dict(held)

    def done(self, bot: str, local_session_id: str, callback_ids: list[str]) -> None:
        """Ends a `take`, dropping the texts queued or no longer this
        machine's to send.
        """
        self._in_flight.discard((bot, local_session_id))
        if not callback_ids:
            return
        sessions = self._held.get(bot, {})
        texts = sessions.get(local_session_id, {})
        for callback_id in callback_ids:
            texts.pop(callback_id, None)
        if not texts:
            sessions.pop(local_session_id, None)
        if not sessions:
            self._held.pop(bot, None)
        self._save()

    def _save(self) -> None:
        try:
            if not any(self._held.values()):
                self._path.unlink(missing_ok=True)
                return
            _write_atomically(self._path, json.dumps(self._held))
        except OSError:
            logger.warning("Could not update Vibebot's old held file", exc_info=True)


def _held_entry(raw: object) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {"text": str(raw)}


def _write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text)
    temporary.replace(path)


class VibeBotLocalSessions:
    """The bots' local sessions this process runs, by bot workflow id, shared
    by every connection: a local session's reply reaches its bot from
    whichever connection runs it.
    """

    def __init__(self, *, client_id_file: Path) -> None:
        self._client_id_file = client_id_file
        self.legacy_held = _LegacyHeld(client_id_file.with_name(_LEGACY_HELD_FILE))
        self._client_ids: dict[str, str] | None = None
        self._bots: dict[str, _BotLocalSessions] = {}
        self.reporter: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None

    def of(self, bot: str) -> _BotLocalSessions:
        return self._bots.setdefault(bot, _BotLocalSessions())

    def client_id(self, bot: str) -> str:
        """This machine's id for one bot's workflow, kept across restarts."""
        if self._client_ids is None:
            try:
                loaded = json.loads(self._client_id_file.read_text())
                self._client_ids = (
                    {str(k): str(v) for k, v in loaded.items()}
                    if isinstance(loaded, dict)
                    else {}
                )
            except (OSError, ValueError):
                self._client_ids = {}
        known = self._client_ids.get(bot)
        if known is not None:
            return known
        created = str(uuid.uuid4())
        self._client_ids[bot] = created
        try:
            _write_atomically(self._client_id_file, json.dumps(self._client_ids))
        except OSError:
            logger.warning("Could not save the Vibebot client id", exc_info=True)
        return created

    def handles(self, target_session_id: str) -> bool:
        return _bot_thread_of_sender(target_session_id) is not None

    async def deliver_reply(
        self, params: SessionPeerSendParams
    ) -> SessionPeerSendResponse:
        """A local session's message to its bot, reported to that bot's
        workflow: the end of the turn the thread it works for started, or a
        message mid-turn to the thread it names (`bot_to`, by handle), or to
        the thread it works for. The workflow decides; a refusal fails the send.
        """
        target = _bot_thread_of_sender(params.target_session_id)
        if target is None or self.reporter is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, "No Vibebot is followed here"
            )
        bot, thread_id = target
        if not params.reply:
            try:
                await self.reporter(
                    bot,
                    {
                        "kind": "message",
                        "clientId": self.client_id(bot),
                        "requestId": params.idempotency_key,
                        "threadId": thread_id,
                        "localSessionId": params.sender.session_id,
                        "text": params.text,
                        **({"to": params.bot_to} if params.bot_to else {}),
                        **(
                            {"delivery": "deferred"}
                            if params.delivery == "queue"
                            else {}
                        ),
                    },
                )
            except VibeBotCallRefused as exc:
                raise SessionPeerError(str(exc)) from exc
            except Exception as exc:
                raise SessionPeerError(
                    f"The message may not have reached the bot: {exc}"
                ) from exc
            return SessionPeerSendResponse(
                session_id=params.bot_to or params.target_session_id,
                queued=params.delivery == "queue",
            )
        await self.reporter(
            bot,
            {
                "kind": "turn_ended",
                "clientId": self.client_id(bot),
                "threadId": thread_id,
                "localSessionId": params.sender.session_id,
                **({"turnId": params.reply_turn_id} if params.reply_turn_id else {}),
                "result": {"outcome": "completed", "finalText": params.text},
            },
        )
        return SessionPeerSendResponse(
            session_id=params.target_session_id, queued=False
        )


class _Follower:
    def __init__(
        self, controller: VibeBotController, bot: str, session_id: str
    ) -> None:
        self._controller = controller
        self.bot = bot
        self.session_id = session_id
        self._answering: set[str] = set()
        self._answered: set[str] = set()
        self._results: dict[str, dict[str, Any]] = {}
        self._first_seen: dict[str, float] = {}
        self._open: dict[str, dict[str, Any]] = {}
        self._retries: set[asyncio.TimerHandle] = set()
        self._reconnect_wait = controller.reconnect_seconds
        self.task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        """Answers the session's open calls until it ends: one read of them,
        then their stream, again after the stream closes or fails.
        """
        try:
            while True:
                try:
                    self._on_open_calls(await self._open_calls())
                    if await self._follow_stream():
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Following Vibebot callbacks failed",
                        extra={"bot_session_id": self.session_id},
                        exc_info=exc,
                    )
                await self._controller.sleep(self._reconnect_wait)
                self._reconnect_wait = min(
                    self._reconnect_wait * 2, self._controller.max_reconnect_seconds
                )
        finally:
            for retry in self._retries:
                retry.cancel()

    async def _follow_stream(self) -> bool:
        """Whether the session ended; otherwise the stream closed."""
        async with credential_stream(
            self._controller.credentials(),
            "GET",
            f"{self._controller.sessions_url}/{self.session_id}/callbacks/stream",
            timeout=_STREAM_TIMEOUT,
            client=self._controller.http,
        ) as response:
            if response.status_code != HTTPStatus.OK:
                raise RuntimeError(
                    f"Following the callbacks failed: HTTP {response.status_code}"
                )
            event = ""
            async for line in response.aiter_lines():
                if not line:
                    event = ""
                elif line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                    if event == "session_updated":
                        return True
                elif line.startswith("data:") and event == "callbacks":
                    try:
                        frame = json.loads(line.removeprefix("data:"))
                    except json.JSONDecodeError:
                        logger.debug("Skipping a malformed Vibebot callbacks frame")
                        continue
                    self._reconnect_wait = self._controller.reconnect_seconds
                    self._on_open_calls(list(frame.get("callbacks", [])))
        return False

    def _on_open_calls(self, items: list[dict[str, Any]]) -> None:
        self._open = {
            str((item.get("callback") or {}).get("id")): item for item in items
        }
        for item in items:
            self._maybe_answer(item)

    def _retry_later(self, callback_id: str) -> None:
        def retry() -> None:
            self._retries.discard(handle)
            if (item := self._open.get(callback_id)) is not None:
                self._maybe_answer(item)

        handle = asyncio.get_running_loop().call_later(
            self._controller.not_yet_retry_seconds, retry
        )
        self._retries.add(handle)

    async def _open_calls(self) -> list[dict[str, Any]]:
        try:
            response = await credential_request(
                self._controller.credentials(),
                "GET",
                f"{self._controller.sessions_url}/{self.session_id}/callbacks",
                timeout=30,
                client=self._controller.http,
            )
        except PlatformCredentialUnavailable as exc:
            raise RuntimeError(f"No platform credential: {exc}") from exc
        if response.status_code != HTTPStatus.OK:
            raise RuntimeError(
                f"Reading the callbacks failed: HTTP {response.status_code}"
            )
        return list(response.json().get("callbacks", []))

    def _maybe_answer(self, item: dict[str, Any]) -> None:
        callback = item.get("callback") or {}
        callback_id = callback.get("id")
        if (
            callback.get("kind") != "client_tool"
            or callback.get("name") not in {START_TOOL, SEND_TOOL, *LOCAL_TOOLS}
            or not isinstance(callback_id, str)
            or callback_id in self._answering
            or callback_id in self._answered
        ):
            return
        self._answering.add(callback_id)
        self._first_seen.setdefault(callback_id, asyncio.get_running_loop().time())
        session_id = str(item.get("sessionId") or self.session_id)
        asyncio.get_running_loop().create_task(
            self._answer(
                session_id,
                callback_id,
                str(callback["name"]),
                callback.get("input") or {},
            )
        )

    async def _answer(
        self, session_id: str, callback_id: str, name: str, tool_input: dict[str, Any]
    ) -> None:
        try:
            result = self._results.get(callback_id) or await self._result(
                session_id, callback_id, name, tool_input
            )
            if result is None:
                return
            # A post that fails sends this result again: the call never runs twice.
            self._results[callback_id] = result
            response = await credential_request(
                self._controller.credentials(),
                "POST",
                f"{self._controller.sessions_url}/{session_id}/callback/result",
                json={"result": result},
                timeout=30,
                client=self._controller.http,
            )
            # 404/409/410: the call closed, another answer came first, or the
            # session ended: nothing left to answer.
            if response.status_code in {
                HTTPStatus.OK,
                HTTPStatus.NOT_FOUND,
                HTTPStatus.CONFLICT,
                HTTPStatus.GONE,
            }:
                self._answered.add(callback_id)
                self._results.pop(callback_id, None)
            else:
                logger.warning(
                    "Answering a Vibebot call failed",
                    extra={
                        "bot_session_id": session_id,
                        "status": response.status_code,
                    },
                )
                self._retry_later(callback_id)
        except Exception as exc:
            logger.warning("Answering a Vibebot call failed", exc_info=exc)
            self._retry_later(callback_id)
        finally:
            self._answering.discard(callback_id)

    async def _result(
        self, session_id: str, callback_id: str, name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The call's answer, or None when this machine does not answer it."""
        try:
            output = await self._controller.run_call(
                self.bot, session_id, callback_id, name, tool_input
            )
        except _OtherMachine:
            # Another machine answered first, or runs the session: it
            # answers the call.
            self._answered.add(callback_id)
            return None
        except _NotYet as exc:
            waited = asyncio.get_running_loop().time() - self._first_seen[callback_id]
            if waited < self._controller.not_yet_grace_seconds:
                self._retry_later(callback_id)
                return None
            result: dict[str, Any] = {
                "callbackId": callback_id,
                "error": {"code": "callback_failed", "message": str(exc)},
            }
        except _NotOrchestrator:
            result = {
                "callbackId": callback_id,
                "error": {"code": "callback_failed", "message": ORCHESTRATOR_ONLY},
            }
        except LocalToolFailure as exc:
            result = {
                "callbackId": callback_id,
                "error": {"code": exc.code, "message": str(exc)},
            }
        except Exception as exc:
            result = {
                "callbackId": callback_id,
                "error": {"code": "callback_failed", "message": str(exc)},
            }
        else:
            result = {"callbackId": callback_id, "output": output}
        return result


def _failed_locally(exc: Exception, name: str, server: object) -> LocalToolFailure:
    """A config, start or auth failure can name a command, a path or a URL: the
    bot gets a code, this machine keeps the text.
    """
    logger.warning(
        "A Vibebot local tool call failed",
        extra={"tool": name, "server": server},
        exc_info=exc,
    )
    return LocalToolFailure(
        "local_tool_failed",
        "The local tool failed on this computer; the user can see why in Vibe's logs",
    )


class _OtherMachine(Exception):
    pass


class _NotOrchestrator(Exception):
    pass


class _NotYet(Exception):
    """This machine cannot serve the call yet; it fails after a grace."""


class VibeBotController:
    """One connection's followed bot sessions."""

    def __init__(  # noqa: PLR0913 - one collaborator per concern, all keyword-only
        self,
        *,
        credentials: Credentials,
        client: VibeBotClient,
        sessions: VibeBotLocalSessions,
        api_origin: Callable[[], Awaitable[str]],
        project_roots: Callable[[], Awaitable[list[Path]]],
        http: httpx2.AsyncClient | None = None,
        reconnect_seconds: float = RECONNECT_SECONDS,
        max_reconnect_seconds: float = MAX_RECONNECT_SECONDS,
        not_yet_grace_seconds: float = NOT_YET_GRACE_SECONDS,
        not_yet_retry_seconds: float = NOT_YET_RETRY_SECONDS,
        local_tools: LocalTools | None = None,
    ) -> None:
        self._local_tools = local_tools
        # A restart or a reconnect claims anew: the workflow tells a claim with
        # another nonce that the call may have run, rather than letting it run again.
        self._claim_nonce = uuid.uuid4().hex
        self.not_yet_grace_seconds = not_yet_grace_seconds
        self.not_yet_retry_seconds = not_yet_retry_seconds
        self.credentials = credentials
        self._api_origin = api_origin
        self._project_roots = project_roots
        self.sessions_url = ""
        self.http = http
        self.reconnect_seconds = reconnect_seconds
        self.max_reconnect_seconds = max_reconnect_seconds
        self.sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._client = client
        self._sessions = sessions
        self._followers: dict[tuple[str, str], _Follower] = {}

    @property
    def sessions(self) -> VibeBotLocalSessions:
        return self._sessions

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> ProtocolModel:
        match method:
            case "vibebot/follow":
                params = validate_wire(VibeBotFollowParams, raw_params)
                origin = (params.api_base_url or await self._api_origin()).rstrip("/")
                self.sessions_url = f"{origin}/v2/agents/sessions"
                self._follow(params)
                return VibeBotFollowResponse(
                    client_id=self._sessions.client_id(params.bot_workflow_id)
                )
            case "vibebot/deliver":
                params = validate_wire(VibeBotDeliverParams, raw_params)
                await self._deliver(params.bot_workflow_id, params.deliveries)
                return VibeBotEmpty()
            case "vibebot/localTools":
                validate_wire(VibeBotLocalToolsParams, raw_params)
                if self._local_tools is None:
                    return VibeBotLocalToolsResponse(servers=[], digest="")
                return await self._local_tools.summary()
        raise RequestFailure(ProtocolErrorCode.METHOD_NOT_FOUND, method)

    def orchestrator_of(self, bot: str) -> str | None:
        return self._sessions.of(bot).orchestrator_session_id

    def _follow(self, params: VibeBotFollowParams) -> None:
        bot = params.bot_workflow_id
        self._sessions.reporter = self._client.report
        local = self._sessions.of(bot)
        # The bot's live local threads, archived ones left out: a send to one
        # not listed fails before it reaches any session.
        local.threads = {s.thread_id: s for s in params.local_sessions}
        local.orchestrator_session_id = params.orchestrator_session_id
        self._queue_legacy_held(bot)
        wanted = {(bot, session_id) for session_id in params.session_ids}
        for key in list(self._followers):
            if key[0] == bot and key not in wanted:
                self._unfollow(key)
        for session_id in params.session_ids:
            if (bot, session_id) in self._followers:
                continue
            follower = _Follower(self, bot, session_id)
            follower.task = asyncio.get_running_loop().create_task(follower.run())
            self._followers[bot, session_id] = follower

    def _queue_legacy_held(self, bot: str) -> None:
        mine = self._sessions.client_id(bot)
        for session in self._sessions.of(bot).threads.values():
            if session.client_id not in {None, mine}:
                continue
            held = self._sessions.legacy_held.take(bot, session.local_session_id)
            if held:
                asyncio.get_running_loop().create_task(
                    self._queue_one_legacy_held(bot, session, held)
                )

    async def _queue_one_legacy_held(
        self, bot: str, session: VibeBotLocalSession, held: dict[str, dict[str, str]]
    ) -> None:
        """Queues an older build's held texts into their session. A text that
        build claimed is claimed again first: one the bot answered itself
        meanwhile is dropped, one whose claim cannot be checked stays held.
        """
        done: list[str] = []
        texts: list[str] = []
        try:
            for callback_id, entry in held.items():
                caller = entry.get("caller")
                if caller is not None:
                    try:
                        taken = await self._client.report(
                            bot,
                            {
                                "kind": "claimed",
                                "clientId": self._sessions.client_id(bot),
                                "requestId": callback_id,
                                "threadId": session.thread_id,
                                "callerSessionId": caller,
                            },
                        )
                    except (VibeBotCallHeld, VibeBotCallRefused):
                        taken = True
                    if taken:
                        done.append(callback_id)
                        continue
                texts.append(entry.get("text", ""))
            if texts:
                await self._client.send_local(
                    bot=bot,
                    local_session_id=session.local_session_id,
                    thread_id=session.thread_id,
                    text="\n\n".join(texts),
                    idempotency_key=f"held-{session.local_session_id}",
                    delivery="queue",
                )
            done = list(held)
        except Exception:
            logger.warning(
                "Could not queue a Vibebot text an older build held; the next "
                "follow tries again",
                extra={"local_session_id": session.local_session_id},
                exc_info=True,
            )
        finally:
            self._sessions.legacy_held.done(bot, session.local_session_id, done)

    def _unfollow(self, key: tuple[str, str]) -> None:
        follower = self._followers.pop(key, None)
        if follower is not None and follower.task is not None:
            follower.task.cancel()

    async def _require_local_project(
        self, checkout: _CheckoutDirectory | _CheckoutNewWorktree | None
    ) -> None:
        """A start runs only in one of the user's local projects, or a folder
        inside one: the cloud call never picks any folder on the computer.
        """
        if checkout is None:
            return
        raw = (
            checkout.path
            if isinstance(checkout, _CheckoutDirectory)
            else checkout.repository
        )
        # Resolved, so a symlink inside a project cannot lead outside it.
        folder = Path(os.path.realpath(raw)) if os.path.isabs(raw) else None
        roots = [Path(os.path.realpath(root)) for root in await self._project_roots()]
        if folder is None or not any(folder.is_relative_to(root) for root in roots):
            raise ValueError(
                f"`{raw}` is not one of the user's local projects: start in one of "
                "them (or a folder inside one), or ask the user to add it as a project in Desktop"
            )

    async def run_call(
        self,
        bot: str,
        session_id: str,
        callback_id: str,
        name: str,
        tool_input: dict[str, Any],
    ) -> dict[str, Any]:
        if name in {START_TOOL, SEND_TOOL} and self.orchestrator_of(bot) not in {
            None,
            session_id,
        }:
            raise _NotOrchestrator
        if name == START_TOOL:
            return await self.start(bot, session_id, callback_id, tool_input)
        if name == SEND_TOOL:
            return await self.send(bot, session_id, callback_id, tool_input)
        return await self._local_tool_call(
            bot, session_id, callback_id, name, tool_input
        )

    async def _local_tool_call(
        self,
        bot: str,
        caller_session_id: str,
        callback_id: str,
        name: str,
        tool_input: dict[str, Any],
    ) -> dict[str, Any]:
        """Claims the call, then lists or runs it. A machine that cannot serve
        it (another machine named, or a server it does not expose) claims it
        too: the workflow lets it answer only when no connected machine can.
        """
        _validated_input(tool_input)
        machine = self._sessions.client_id(bot)
        named = tool_input.get("machine")
        server = tool_input.get("server")
        tools = self._local_tools
        if named is not None and named != machine:
            serves = False
        elif tools is None:
            serves = False
        elif named is None and isinstance(server, str):
            try:
                serves = await tools.exposes(server)
            except Exception as exc:
                raise _failed_locally(exc, name, server) from exc
        else:
            serves = True
        await self._claim(
            bot,
            caller_session_id,
            callback_id,
            name,
            server=server if isinstance(server, str) else None,
            machine=named if isinstance(named, str) else None,
            serves=serves,
        )
        if not serves:
            if named is not None and named != machine:
                raise LocalToolFailure(
                    "local_host_unreachable",
                    f"The computer `{named}` is not connected: retry when it is back, "
                    "or call the tool without `machine`",
                )
            raise LocalToolFailure(
                "unknown_server",
                f"No connected computer exposes `{server}` to Vibebot: "
                "`list_local_tools` shows what each one exposes",
            )
        assert tools is not None
        try:
            return await self._serve_local_tool(tools, machine, name, tool_input)
        except LocalToolFailure:
            raise
        except Exception as exc:
            raise _failed_locally(exc, name, server) from exc

    async def _serve_local_tool(
        self, tools: LocalTools, machine: str, name: str, tool_input: dict[str, Any]
    ) -> dict[str, Any]:
        if name == LIST_TOOL:
            return await tools.list(tool_input, machine)
        call = _validated_call(tool_input)
        approval, max_bytes, timeout_s = await tools.mode(call.server, call.tool)
        if approval == "ask":
            raise LocalToolFailure(
                "approval_required",
                f"`{call.server}.{call.tool}` needs the user's approval on this "
                "computer, which Vibebot cannot ask for yet: ask the user to run "
                "it, or to allow it in vibe_bot_local_tools",
            )
        result = await tools.run(
            call.server,
            call.tool,
            call.arguments,
            max_bytes=max_bytes,
            timeout_s=timeout_s,
            require_read_only=await tools.allowed_only_as_read_only(
                call.server, call.tool
            ),
        )
        return {"machine": machine, "server": call.server, "tool": call.tool, **result}

    async def _claim(
        self,
        bot: str,
        caller_session_id: str,
        callback_id: str,
        name: str,
        *,
        server: str | None,
        machine: str | None,
        serves: bool,
    ) -> None:
        """Only the machine whose claim the workflow takes runs the call."""
        try:
            duplicate = await self._client.report(
                bot,
                {
                    "kind": "tool_claimed",
                    "clientId": self._sessions.client_id(bot),
                    "requestId": callback_id,
                    "callerSessionId": caller_session_id,
                    "tool": name.removeprefix("bot."),
                    "server": server,
                    "machine": machine,
                    "serves": serves,
                    "claimNonce": self._claim_nonce,
                },
            )
        except VibeBotCallRefused as refused:
            code, _, message = str(refused).partition(": ")
            raise LocalToolFailure(code, message) from refused
        except Exception as exc:
            raise _NotYet(f"The bot did not take this computer's claim: {exc}") from exc
        if duplicate:
            raise _OtherMachine

    async def start(
        self,
        bot: str,
        caller_session_id: str,
        callback_id: str,
        raw_input: dict[str, Any],
    ) -> dict[str, Any]:
        tool_input = _validated(_StartInput, raw_input)
        await self._require_local_project(tool_input.checkout)
        thread_id = f"local_{callback_id}"
        title = tool_input.title or tool_input.handle
        local_session_id, turn_id = await self._client.start_local(
            bot=bot,
            idempotency_key=callback_id,
            thread_id=thread_id,
            prompt=_brief(tool_input.handle, tool_input.brief, tool_input.done_when),
            title=title,
            checkout=tool_input.checkout.model_dump(by_alias=True)
            if tool_input.checkout is not None
            else None,
            model=tool_input.model,
            approval_policy=tool_input.approval_policy,
        )
        try:
            duplicate = await self._client.report(
                bot,
                {
                    "kind": "accepted",
                    "clientId": self._sessions.client_id(bot),
                    "requestId": callback_id,
                    "threadId": thread_id,
                    "localSessionId": local_session_id,
                    **({"turnId": turn_id} if turn_id else {}),
                    "call": {
                        "kind": "start",
                        "callerSessionId": caller_session_id,
                        "handle": tool_input.handle,
                        "title": title,
                        "link": tool_input.link,
                    },
                },
            )
        except Exception:
            # The bot refused it (a taken handle, too many sessions) or could
            # not be told: no session it does not know of keeps running.
            await self._client.archive_local(bot=bot, local_session_id=local_session_id)
            raise
        if duplicate:
            await self._client.archive_local(bot=bot, local_session_id=local_session_id)
            raise _OtherMachine
        self._sessions.of(bot).threads[thread_id] = VibeBotLocalSession(
            thread_id=thread_id,
            handle=tool_input.handle,
            local_session_id=local_session_id,
            client_id=self._sessions.client_id(bot),
        )
        return {
            "sessionId": local_session_id,
            "handle": tool_input.handle,
            "title": title,
        }

    async def send(
        self,
        bot: str,
        caller_session_id: str,
        callback_id: str,
        raw_input: dict[str, Any],
    ) -> dict[str, Any]:
        tool_input = _validated(_SendInput, raw_input)
        local = self._sessions.of(bot)
        target = local.find(tool_input.session)
        if target is None:
            raise _NotYet(
                f"No local session `{tool_input.session}` of this bot runs on the user's computers"
            )
        if target.client_id not in {None, self._sessions.client_id(bot)}:
            raise _OtherMachine
        client_id = self._sessions.client_id(bot)
        # Claimed before it is delivered or held, unlike a start: a delivered
        # message cannot be taken back, while a second session can be archived.
        try:
            taken = await self._client.report(
                bot,
                {
                    "kind": "claimed",
                    "clientId": client_id,
                    "requestId": callback_id,
                    "threadId": target.thread_id,
                    "callerSessionId": caller_session_id,
                },
            )
        except VibeBotCallHeld as held:
            return held.output
        except VibeBotCallRefused:
            raise
        except Exception as exc:
            raise _NotYet(f"The bot could not be asked for the message: {exc}") from exc
        if taken:
            # The bot answered it while this machine was away (it holds the
            # message for it), or another machine claimed it.
            raise _OtherMachine
        deferred = tool_input.delivery == "deferred"
        try:
            turn_id = await self._client.send_local(
                bot=bot,
                local_session_id=target.local_session_id,
                thread_id=target.thread_id,
                text=tool_input.text,
                idempotency_key=callback_id,
                delivery="queue" if deferred else "start_turn",
            )
        except Exception as exc:
            await self._release(bot, callback_id, target.thread_id, exc)
            raise _NotYet(f"The message could not be delivered: {exc}") from exc
        try:
            await self._client.report(
                bot,
                {
                    "kind": "accepted",
                    "clientId": client_id,
                    "requestId": callback_id,
                    "threadId": target.thread_id,
                    "localSessionId": target.local_session_id,
                    **({"turnId": turn_id} if turn_id else {}),
                    "call": {
                        "kind": "send",
                        "callerSessionId": caller_session_id,
                        "text": tool_input.text,
                        **({"delivery": "deferred"} if deferred else {}),
                    },
                },
            )
        except Exception as exc:
            # Delivered, but the bot does not know yet: the next poll claims
            # it again (this machine's claim stands) and sends again under the
            # same key, which delivers nothing twice.
            raise _NotYet(f"The bot was not told of the message: {exc}") from exc
        return {
            "sessionId": target.local_session_id,
            "delivered": "queued" if deferred else "started_or_joined_turn",
        }

    async def _release(
        self, bot: str, callback_id: str, thread_id: str, error: Exception
    ) -> None:
        """Gives back a claimed call this machine could not deliver: its retry
        claims it again, or the bot holds it.
        """
        try:
            await self._client.report(
                bot,
                {
                    "kind": "refused",
                    "clientId": self._sessions.client_id(bot),
                    "requestId": callback_id,
                    "threadId": thread_id,
                    "error": str(error),
                },
            )
        except Exception:
            logger.warning("Releasing a Vibebot call failed", exc_info=True)

    async def _deliver(self, bot: str, deliveries: list[dict[str, Any]]) -> None:
        """Delivers each, in order, then reports it; a delivery whose report
        is lost comes back in the next heartbeat and goes again under the same
        key, which Desktop runs once.
        """
        for delivery in deliveries:
            request_id = str(delivery.get("requestId") or "")
            local_session_id = str(delivery.get("localSessionId") or "")
            if (
                not request_id
                or delivery.get("kind") not in {"send", "stop"}
                or not local_session_id
            ):
                continue
            try:
                await self._deliver_one(bot, delivery, local_session_id)
            except Exception as exc:
                logger.warning(
                    "Reporting a Vibebot delivery failed",
                    extra={"request_id": request_id},
                    exc_info=exc,
                )
                return

    async def _deliver_one(
        self, bot: str, delivery: dict[str, Any], local_session_id: str
    ) -> None:
        request_id = str(delivery["requestId"])
        thread_id = str(delivery.get("threadId") or "")
        report: dict[str, Any] = {
            "clientId": self._sessions.client_id(bot),
            "requestId": request_id,
            "threadId": thread_id,
        }
        turn_id: str | None = None
        try:
            if delivery["kind"] == "send":
                turn_id = await self._client.send_local(
                    bot=bot,
                    local_session_id=local_session_id,
                    thread_id=thread_id,
                    text=str(delivery.get("text") or ""),
                    idempotency_key=request_id,
                    delivery="queue"
                    if delivery.get("delivery") == "deferred"
                    else "start_turn",
                )
            else:
                await self._client.stop_local(
                    bot=bot,
                    local_session_id=local_session_id,
                    idempotency_key=request_id,
                )
        except Exception as exc:
            report |= {"kind": "refused", "error": str(exc)}
        else:
            report |= {"kind": "accepted", "localSessionId": local_session_id}
            if turn_id:
                report["turnId"] = turn_id
        await self._client.report(bot, report)

    def close(self) -> list[asyncio.Task[None]]:
        """The tasks to cancel; the MCP servers close in `aclose_tools` (final
        close) or `drop_tools` (detach), awaited after them.
        """
        tasks = [f.task for f in self._followers.values() if f.task is not None]
        self._followers.clear()
        for task in tasks:
            task.cancel()
        return tasks

    async def aclose_tools(self) -> None:
        if self._local_tools is not None:
            await self._local_tools.aclose()

    async def drop_tools(self) -> None:
        """On a detach: the controller lives on, and a call it was running may
        be claimed again by the next connection, which is not a retry.
        """
        self._claim_nonce = uuid.uuid4().hex
        if self._local_tools is not None:
            await self._local_tools.drop_runtime()


_MAX_NAME: Final = 256
_MAX_ARGUMENTS_BYTES: Final = 32 * 1024


class _Names(ProtocolModel):
    server: str | None = Field(default=None, min_length=1, max_length=_MAX_NAME)
    tool: str | None = Field(default=None, min_length=1, max_length=_MAX_NAME)
    machine: str | None = Field(default=None, min_length=1, max_length=_MAX_NAME)


class _CallInput(ProtocolModel):
    server: str = Field(min_length=1, max_length=_MAX_NAME)
    tool: str = Field(min_length=1, max_length=_MAX_NAME)
    arguments: dict[str, Any] = Field(default_factory=dict)
    machine: str | None = None


def _validated_input(raw: dict[str, Any]) -> None:
    """What the bot's tool schema caps, checked here before the claim: only this
    machine reads the input.
    """
    names = {k: raw[k] for k in ("server", "tool", "machine") if k in raw}
    try:
        validate_wire(_Names, names)
    except ValidationError as exc:
        raise LocalToolFailure(
            "invalid_input", f"Invalid input: {exc.errors()[0]['msg']}"
        ) from exc
    size = len(json.dumps(raw.get("arguments", {}), default=str).encode())
    if size > _MAX_ARGUMENTS_BYTES:
        raise LocalToolFailure(
            "invalid_input",
            f"arguments must be at most {_MAX_ARGUMENTS_BYTES} bytes as JSON: "
            "pass less, or a reference the tool can read",
        )


def _validated_call(raw: dict[str, Any]) -> _CallInput:
    try:
        return validate_wire(_CallInput, raw)
    except ValidationError as exc:
        raise LocalToolFailure(
            "invalid_input", f"Invalid input: {exc.errors()[0]['msg']}"
        ) from exc


def _validated[ModelT: ProtocolModel](
    model: type[ModelT], raw: dict[str, Any]
) -> ModelT:
    try:
        return validate_wire(model, raw)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid input: {exc.errors()[0]['msg']}") from exc


__all__ = [
    "VIBE_BOT_ARCHIVE_LOCAL_METHOD",
    "VIBE_BOT_CLIENT_ID_FILE",
    "VIBE_BOT_METHODS",
    "VIBE_BOT_REPORT_METHOD",
    "VIBE_BOT_SEND_LOCAL_METHOD",
    "VIBE_BOT_START_LOCAL_METHOD",
    "VIBE_BOT_STOP_LOCAL_METHOD",
    "VibeBotArchiveLocalParams",
    "VibeBotCallHeld",
    "VibeBotCallRefused",
    "VibeBotClient",
    "VibeBotController",
    "VibeBotEmpty",
    "VibeBotLocalSessions",
    "VibeBotReportParams",
    "VibeBotReportResponse",
    "VibeBotSendLocalParams",
    "VibeBotSendLocalResponse",
    "VibeBotStartLocalParams",
    "VibeBotStartLocalResponse",
    "VibeBotStopLocalParams",
    "bot_sender_id",
]
