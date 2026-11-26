"""Vibebot: this machine's side of a user's bot.

The bot's workflow holds what its local threads are owed: a start
(``spawn_thread(kind: "code_local")``), a message or a stop. The client hands
them over from its heartbeat (``vibebot/deliver``); this server does each
through the client (``vibebot/startLocal``, ``vibebot/sendLocal``,
``vibebot/stopLocal``) under the delivery's key, then reports it. Once a client
names the bot's sessions (``vibebot/follow``), the connection also follows
their open local MCP tool calls on the Agents Session API with its platform
credentials, and answers them.

The bot's workflow stays the only writer to its sessions: what happened here
goes to it as ``local_session_event`` reports, through the client
(``vibebot/report``). A local session's reply owed to the bot (its starter
``vibebot:<bot workflow id>:<thread id>``) becomes a ``turn_ended`` report with the turn's id,
not a message. The decisions live here; the client only passes requests on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
import hashlib
from http import HTTPStatus
import json
import os
from pathlib import Path
import time
from typing import Any, BinaryIO, Final, Literal, Protocol
import uuid

import httpx2
from pydantic import Field, ValidationError

from vibe.app_server._client_instance import is_dev_desktop_copy
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._session_peers import VIBE_BOT_SENDER_PREFIX, SessionPeerError
from vibe.app_server._vibe_bot_tools import (
    LIST_TOOL,
    LOCAL_TOOLS,
    MAX_APPROVAL_ARGUMENTS,
    MAX_CALL_SECONDS,
    VIBE_BOT_LOCAL_TOOLS_METHOD,
    LocalToolFailure,
    LocalTools,
    VibeBotLocalToolsParams,
    VibeBotLocalToolsResponse,
    approval_text,
    args_hash,
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
from vibe.core.utils.file_lock import acquire_file_lock, release_file_lock
from vibe.observability.logging import logger
from vibe.utils.file_lock import exclusive_file_lock

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
VIBE_BOT_INSTANCE_CLIENT_IDS_FILE: Final = "vibe_bot_client_ids.json"
# Client tools of bot builds before `spawn_thread(kind: "code_local")`: a session
# of such a build may still call them, and must not wait an hour for an answer.
RETIRED_TOOLS: Final = frozenset({
    "bot.start_local_session",
    "bot.send_to_local_session",
})
RETIRED: Final = (
    'invalid_input: this tool is gone: start a local thread with spawn_thread(kind: "code_local"), '
    "and write to it with send_message"
)
VIBE_BOT_PENDING_TOOLS_FILE: Final = "vibe_bot_pending_tools.json"
VIBE_BOT_RUNS_DIR: Final = "vibe_bot_runs"
HELD_TOOL_MAX_AGE_SECONDS: Final = 7 * 24 * 3600
_LOCAL_TOOL_FAILED: Final = (
    "The local tool failed on this computer; the user can see why in Vibe's logs"
)


def bot_sender_id(bot: str, thread_id: str) -> str:
    """How a local session names the bot thread it works for, as its starter.
    `bot` is the bot's workflow id, `vibe-bot--<user>--<bot id>`: no `:` in it
    (`BOT_ID_PATTERN`, and user ids are UUIDs). `thread_id` may hold some (a
    `code_local` thread is `local_<tool call id>`), so `_bot_thread_of_sender`
    splits on the first `:`.
    """
    return f"{VIBE_BOT_SENDER_PREFIX}{bot}:{thread_id}"


def _bot_thread_of_sender(sender_id: str) -> tuple[str, str] | None:
    if not sender_id.startswith(VIBE_BOT_SENDER_PREFIX):
        return None
    bot, _, thread_id = sender_id.removeprefix(VIBE_BOT_SENDER_PREFIX).partition(":")
    return (bot, thread_id) if bot and thread_id else None


class VibeBotLocalSession(ProtocolModel):
    thread_id: str
    handle: str
    local_session_id: str
    # The Desktop (profile) that runs it; another leaves its calls alone.
    client_id: str | None = None


class VibeBotFollowParams(ProtocolModel):
    # The bot's workflow id; everything below is this bot's.
    bot_workflow_id: str = Field(min_length=1)
    session_ids: list[str]
    local_sessions: list[VibeBotLocalSession] = Field(default_factory=list)
    # The API the client is signed in to, whose credential it lends; without
    # it, this server's own Mistral provider's.
    api_base_url: str | None = None
    # The bot's run ended, as its BFF says: `archived`, the user archived it
    # (`local_sessions` are then those of its last state); `stopped`, any
    # other end, which a new run may follow.
    ended: Literal["archived", "stopped"] | None = None


class VibeBotFollowResponse(ProtocolModel):
    # This Desktop profile's id for this bot's workflow, kept across restarts.
    client_id: str
    # It starts code_local threads from `start` deliveries: the client says so
    # in its heartbeat, so the bot sends no start to a server that drops it.
    starts_local: bool = True
    # False once this server has nothing left to do for the bot: the client
    # stops asking about it.
    following: bool = True


class VibeBotDeliverParams(ProtocolModel):
    bot_workflow_id: str = Field(min_length=1)
    # What the bot's local threads are owed, from its heartbeat, in order: a
    # start, a message or a stop.
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


class VibeBotLocalSessionGone(Exception):
    """The client has no such local session (deleted): nothing runs in it."""


class VibeBotCallRefused(Exception):
    """The bot refused the claim: the call fails with this message, now."""


class VibeBotEmpty(ProtocolModel):
    pass


class _CheckoutDirectory(ProtocolModel):
    kind: Literal["directory"]
    path: str


class _CheckoutNewWorktree(ProtocolModel):
    kind: Literal["new_worktree"]
    repository: str


class _StartDelivery(ProtocolModel):
    kind: Literal["start"]
    request_id: str
    thread_id: str
    handle: str
    title: str
    brief: str
    done_when: list[str] | None = None
    # As Desktop reads it: the kind names the one folder it runs in.
    checkout: _CheckoutDirectory | _CheckoutNewWorktree | None = Field(
        default=None, discriminator="kind"
    )
    model: str | None = None
    approval_policy: str | None = None


class VibeBotClient(Protocol):
    """What the client does on this server's behalf: local sessions, and the
    calls to the bot's workflow, which need the user's web session.
    """

    def session_tools(self) -> bool:
        """Its sessions get `send_to_session`: the client's `sessionPeers`, as
        `session_peers_for` reads it.
        """
        ...

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


def _brief(
    handle: str, brief: str, done_when: list[str] | None, *, can_message: bool
) -> str:
    """``can_message``: the session gets `send_to_session` (`sessionPeers`)."""
    done = ""
    if done_when:
        done = "\n\nDone when:\n" + "\n".join(
            f"{n}. {item}" for n, item in enumerate(done_when, start=1)
        )
    note = _THREADS_NOTE.format(handle=handle)
    if can_message:
        note += f" {_MESSAGING_NOTE}"
    return f"# Task `{handle}`\n\n{brief}{done}\n\n{note}"


_THREADS_NOTE: Final = (
    "You are the bot's thread `{handle}`. Its other threads are its orchestrator "
    "(`orchestrator`) and its workers, named by handle in what they send you. "
    "The orchestrator's messages are instructions within your task; "
    'another worker\'s (`relation="peer"`) is a request: help when it fits your '
    "task, decline in one line when it does not. The other threads run in the "
    "cloud and can't read files on this computer: put what they need in your "
    "reply."
)

_MESSAGING_NOTE: Final = (
    "To write to one while you work, call `send_to_session` with `session_id` "
    "set to its handle; leave `session_id` out to ask the thread that gave you "
    "this task. A message sent with `start_turn` asks that thread for an answer, "
    "unless it answers a question that thread asked you; one sent with `queue` "
    "only informs it."
)


class _BotLocalSessions:
    """One bot's local sessions on this machine."""

    def __init__(self) -> None:
        self.threads: dict[str, VibeBotLocalSession] = {}

    def find(self, selector: str) -> VibeBotLocalSession | None:
        for session in self.threads.values():
            if selector in {
                session.local_session_id,
                session.thread_id,
                session.handle,
            }:
                return session
        return None


def _fresh(call: dict[str, Any]) -> bool:
    """A hold untouched for a week is let go: unanswered, or its outcome never
    reported. `update_tool` restarts the clock.
    """
    return call.get("heldAt", 0) >= time.time() - HELD_TOOL_MAX_AGE_SECONDS


def _write_atomically(path: Path, text: str, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w") as file:
        file.write(text)
    temporary.replace(path)


class VibeBotLocalSessions:
    """The bots' local sessions this process runs, by bot workflow id, shared
    by every connection: a local session's reply reaches its bot from
    whichever connection runs it.
    """

    def __init__(self, *, client_id_file: Path) -> None:
        self._client_id_file = client_id_file
        self._instance_client_ids_file = client_id_file.with_name(
            VIBE_BOT_INSTANCE_CLIENT_IDS_FILE
        )
        self._pending_tools_file = client_id_file.with_name(VIBE_BOT_PENDING_TOOLS_FILE)
        self._runs_dir = client_id_file.with_name(VIBE_BOT_RUNS_DIR)
        # Approved runs in flight, shared like their holds: a second connection
        # handed the same approval leaves the run to the first.
        self.approved_runs: set[tuple[str, str]] = set()
        self._client_ids: dict[str, str] | None = None
        self._instance_client_ids: dict[tuple[str, str], str] = {}
        self._bots: dict[str, _BotLocalSessions] = {}
        self.reporter: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None

    def of(self, bot: str) -> _BotLocalSessions:
        return self._bots.setdefault(bot, _BotLocalSessions())

    def hold_tool(self, bot: str, callback_id: str, call: dict[str, Any]) -> None:
        """A retry of the same call keeps its hold: a `running` or `outcome` it
        holds means the tool ran, and must not run again.
        """
        with self._editing_pending_tools() as pending:
            kept = pending.get(bot, {}).get(callback_id)
            if kept is not None and kept.get("argsHash") == call.get("argsHash"):
                return
            pending.setdefault(bot, {})[callback_id] = call | {"heldAt": time.time()}

    def held_tool(self, bot: str, callback_id: str) -> dict[str, Any] | None:
        """The hold, or None once it is a week old, checked on every read: an
        app server can run for longer than a week.
        """
        with exclusive_file_lock(self._pending_tools_file):
            return self._read_pending_tools().get(bot, {}).get(callback_id)

    def update_tool(
        self, bot: str, callback_id: str, *, required: bool = False, **fields: Any
    ) -> None:
        """`required`: a mark the one-run guarantee rests on raises when it
        cannot be saved, rather than letting the run go on without it.
        """
        with self._editing_pending_tools(required=required) as pending:
            call = pending.get(bot, {}).get(callback_id)
            if call is not None:
                # The week counts from the last change: a run near it keeps its
                # `running` mark and outcome, so a retry never says it did not run.
                call.update(fields | {"heldAt": time.time()})

    def drop_tool(self, bot: str, callback_id: str) -> None:
        with self._editing_pending_tools() as pending:
            pending.get(bot, {}).pop(callback_id, None)

    def held_outcomes(self, bot: str, client_id: str) -> list[str]:
        """A hold from before holds named their Desktop goes to whichever
        follows first: the bot refuses the report of a Desktop it did not ask.
        """
        return [
            callback_id
            for callback_id, call in self._locked_pending_tools().get(bot, {}).items()
            if "outcome" in call and call.get("clientId", client_id) == client_id
        ]

    def claim_run(self, bot: str, callback_id: str) -> BinaryIO | None:
        """The lock that makes this process the machine's only runner of an
        approved call, or None while another app server here holds it. The OS
        lets go of it when its process dies.
        """
        name = hashlib.sha256(f"{bot}\0{callback_id}".encode()).hexdigest()[:32]
        try:
            self._runs_dir.mkdir(parents=True, exist_ok=True)
            self._clear_old_run_locks()
            path = self._runs_dir / name
            # Before the lock: Windows may refuse to touch a file it holds open.
            path.touch()
            lock = path.open("a+b")
        except OSError:
            logger.warning("Could not open a Vibebot run lock", exc_info=True)
            return None
        try:
            acquire_file_lock(lock)
        except BlockingIOError:
            lock.close()
            return None
        return lock

    def _clear_old_run_locks(self) -> None:
        # A run's lock outlives its hold by no more than the week a hold lasts.
        cutoff = time.time() - HELD_TOOL_MAX_AGE_SECONDS
        for path in self._runs_dir.iterdir():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    @staticmethod
    def release_run(lock: BinaryIO) -> None:
        # The file stays: unlinking it would let two runners lock different inodes.
        release_file_lock(lock)
        lock.close()

    def _read_pending_tools(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Read anew on every use: every app server of this machine shares it."""
        try:
            loaded = json.loads(self._pending_tools_file.read_text())
        except (OSError, ValueError):
            loaded = {}
        return {
            str(bot): {
                str(k): v for k, v in calls.items() if isinstance(v, dict) and _fresh(v)
            }
            for bot, calls in (loaded if isinstance(loaded, dict) else {}).items()
            if isinstance(calls, dict)
        }

    def _locked_pending_tools(self) -> dict[str, dict[str, dict[str, Any]]]:
        with exclusive_file_lock(self._pending_tools_file):
            return self._read_pending_tools()

    @contextmanager
    def _editing_pending_tools(
        self, *, required: bool = False
    ) -> Iterator[dict[str, dict[str, dict[str, Any]]]]:
        with exclusive_file_lock(self._pending_tools_file):
            pending = self._read_pending_tools()
            yield pending
            kept = {bot: c for bot, c in pending.items() if c}
            try:
                # The bot's tool arguments: as private as a token file.
                _write_atomically(
                    self._pending_tools_file, json.dumps(kept), mode=0o600
                )
            except OSError:
                if required:
                    raise
                logger.warning(
                    "Could not save Vibebot's pending tool calls", exc_info=True
                )

    def client_id(self, bot: str, *, instance: str | None = None) -> str:
        """This Desktop's id for one bot's workflow, kept across restarts.

        `instance` names the Desktop (its profile): every Desktop sharing this
        Vibe home gets its own ids, or the bot takes them for one and each
        takes the other's starts and calls. Without it, the machine's ids.
        """
        if instance is not None:
            return self._instance_client_id(bot, instance)
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

    def _instance_client_id(self, bot: str, instance: str) -> str:
        # Entries are never pruned: a profile gone for good (an old dev port)
        # leaves a few bytes, and pruning one still in use would orphan its
        # open start cards and local tool rules.
        known = self._instance_client_ids.get((instance, bot))
        if known is not None:
            return known
        with exclusive_file_lock(self._instance_client_ids_file):
            try:
                loaded = json.loads(self._instance_client_ids_file.read_text())
            except (OSError, ValueError):
                loaded = {}
            saved = loaded if isinstance(loaded, dict) else {}
            instances = saved.get("instances")
            instances = instances if isinstance(instances, dict) else {}
            ids = instances.get(instance)
            ids = (
                {str(k): str(v) for k, v in ids.items()}
                if isinstance(ids, dict)
                else None
            )
            adopted = saved.get("adoptedBy")
            if ids is None and adopted is None and not is_dev_desktop_copy(instance):
                # The first Desktop after the upgrade keeps the machine's ids:
                # its open start cards and local tool rules are keyed on them.
                # Only Desktop follows a bot, so no other client still uses them.
                ids = {k: self.client_id(k) for k in self._legacy_client_ids()}
                adopted = instance
            ids = ids or {}
            created = ids.get(bot) is None
            if created:
                ids[bot] = str(uuid.uuid4())
            if created or saved.get("adoptedBy") != adopted:
                instances[instance] = ids
                try:
                    _write_atomically(
                        self._instance_client_ids_file,
                        json.dumps({"adoptedBy": adopted, "instances": instances}),
                    )
                except OSError:
                    logger.warning(
                        "Could not save the Vibebot client id", exc_info=True
                    )
        self._instance_client_ids[instance, bot] = ids[bot]
        return ids[bot]

    def _legacy_client_ids(self) -> list[str]:
        try:
            loaded = json.loads(self._client_id_file.read_text())
        except (OSError, ValueError):
            return []
        return [str(k) for k in loaded] if isinstance(loaded, dict) else []

    def handles(self, target_session_id: str) -> bool:
        return _bot_thread_of_sender(target_session_id) is not None

    async def deliver_reply(
        self, params: SessionPeerSendParams, *, instance: str | None = None
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
        # The Desktop that runs the session, whichever connection the reply
        # leaves on: one with no instance would name another Desktop.
        session = self.of(bot).threads.get(thread_id)
        client_id = (
            session.client_id
            if session is not None and session.client_id is not None
            else self.client_id(bot, instance=instance)
        )
        if not params.reply:
            try:
                await self.reporter(
                    bot,
                    {
                        "kind": "message",
                        "clientId": client_id,
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
        try:
            await self.reporter(
                bot,
                {
                    "kind": "turn_ended",
                    "clientId": client_id,
                    "threadId": thread_id,
                    "localSessionId": params.sender.session_id,
                    **(
                        {"turnId": params.reply_turn_id} if params.reply_turn_id else {}
                    ),
                    "result": {"outcome": "completed", "finalText": params.text},
                },
            )
        except VibeBotCallRefused as exc:
            # Final: `send_reply` stops sending it (a thread the bot no longer
            # has, or that no longer runs this session). An error stays retried.
            raise SessionPeerError(str(exc)) from exc
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
            or callback.get("name") not in {*LOCAL_TOOLS, *RETIRED_TOOLS}
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
        if name in RETIRED_TOOLS:
            return {
                "callbackId": callback_id,
                "error": {"code": "callback_failed", "message": RETIRED},
            }
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
    return LocalToolFailure("local_tool_failed", _LOCAL_TOOL_FAILED)


class _OtherMachine(Exception):
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
        client_instance: Callable[[], str | None] = lambda: None,
    ) -> None:
        self._local_tools = local_tools
        self._client_instance = client_instance
        # A restart or a reconnect claims anew: the workflow tells a claim with
        # another nonce that the call may have run, rather than letting it run again.
        self._claim_nonce = uuid.uuid4().hex
        self._approved_runs: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._dropping: set[asyncio.Task[None]] = set()
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

    def client_id(self, bot: str) -> str:
        return self._sessions.client_id(bot, instance=self._client_instance())

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> ProtocolModel:
        match method:
            case "vibebot/follow":
                params = validate_wire(VibeBotFollowParams, raw_params)
                origin = (params.api_base_url or await self._api_origin()).rstrip("/")
                self.sessions_url = f"{origin}/v2/agents/sessions"
                client_id = self.client_id(params.bot_workflow_id)
                if params.ended == "archived":
                    return VibeBotFollowResponse(
                        client_id=client_id, following=not await self._archived(params)
                    )
                if params.ended == "stopped":
                    self._unfollow_bot(params.bot_workflow_id)
                    return VibeBotFollowResponse(client_id=client_id)
                self._follow(params)
                return VibeBotFollowResponse(client_id=client_id)
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

    def _follow(self, params: VibeBotFollowParams) -> None:
        bot = params.bot_workflow_id
        self._sessions.reporter = self._client.report
        local = self._sessions.of(bot)
        # The bot's live local threads, archived ones left out: a send to one
        # not listed fails before it reaches any session.
        local.threads = {s.thread_id: s for s in params.local_sessions}
        # Once the workflow expires a call it stops sending its approval: an
        # outcome whose report was lost goes out without one.
        for callback_id in self._sessions.held_outcomes(bot, self.client_id(bot)):
            self._start_approved(bot, {"callbackId": callback_id, "flush": True})
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

    async def _archived(self, params: VibeBotFollowParams) -> bool:
        """The user archived the bot, maybe while this machine was offline, so
        its stops never came: it is no longer followed, and each of its local
        sessions here has its running turn interrupted. False while an
        interrupt failed: the next follow tries again.
        """
        bot = params.bot_workflow_id
        self._unfollow_bot(bot)
        here = self.client_id(bot)
        local = self._sessions.of(bot)
        held = {s.local_session_id: s for s in local.threads.values()}
        held |= {s.local_session_id: s for s in params.local_sessions}
        stopped = True
        for session in held.values():
            if session.client_id not in {None, here}:
                continue
            try:
                await self._client.stop_local(
                    bot=bot,
                    local_session_id=session.local_session_id,
                    idempotency_key=f"archived-{session.local_session_id}",
                )
            except VibeBotLocalSessionGone:
                continue
            except Exception:
                logger.warning(
                    "Could not stop a local session of an archived bot",
                    extra={"local_session_id": session.local_session_id},
                    exc_info=True,
                )
                stopped = False
        if stopped:
            local.threads = {}
        return stopped

    def _unfollow_bot(self, bot: str) -> None:
        for key in [key for key in self._followers if key[0] == bot]:
            self._unfollow(key)

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
        return await self._local_tool_call(
            bot, session_id, callback_id, name, tool_input
        )

    def _tools(self) -> LocalTools:
        if self._local_tools is None:
            raise LocalToolFailure(
                "local_tools_unavailable",
                "This computer runs no local tools for Vibebot",
            )
        return self._local_tools

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
        machine = self.client_id(bot)
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
            return await self._serve_local_tool(
                tools,
                machine,
                name,
                tool_input,
                bot=bot,
                caller_session_id=caller_session_id,
                callback_id=callback_id,
            )
        except (LocalToolFailure, _NotYet):
            raise
        except Exception as exc:
            raise _failed_locally(exc, name, server) from exc

    async def _serve_local_tool(
        self,
        tools: LocalTools,
        machine: str,
        name: str,
        tool_input: dict[str, Any],
        *,
        bot: str,
        caller_session_id: str,
        callback_id: str,
    ) -> dict[str, Any]:
        if name == LIST_TOOL:
            return await tools.list(tool_input, machine)
        call = _validated_call(tool_input)
        approval, max_bytes, timeout_s = await tools.mode(call.server, call.tool)
        if approval == "ask":
            return await self._ask_user(bot, caller_session_id, callback_id, call)
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

    async def _ask_user(
        self, bot: str, caller_session_id: str, callback_id: str, call: _CallInput
    ) -> dict[str, Any]:
        """The call waits on this machine for the user's answer in the inbox; the
        bot hears of it at once and its result wakes the bot later.
        """
        if len(approval_text(call.arguments)) > MAX_APPROVAL_ARGUMENTS:
            raise LocalToolFailure(
                "arguments_too_long",
                f"`{call.server}.{call.tool}` needs the user's approval, and the inbox "
                f"shows at most {MAX_APPROVAL_ARGUMENTS} characters of arguments: "
                "call it with shorter arguments",
            )
        digest = args_hash(call.arguments)
        self._sessions.hold_tool(
            bot,
            callback_id,
            {
                "server": call.server,
                "tool": call.tool,
                "arguments": call.arguments,
                "argsHash": digest,
                "clientId": self.client_id(bot),
            },
        )
        try:
            await self._client.report(
                bot,
                {
                    "kind": "tool_pending",
                    "clientId": self.client_id(bot),
                    "requestId": callback_id,
                    "callerSessionId": caller_session_id,
                    "server": call.server,
                    "tool": call.tool,
                    "arguments": call.arguments,
                    "argsHash": digest,
                    "readOnly": await self._tools().is_read_only(
                        call.server, call.tool
                    ),
                    "context": {"ceilingAllows": await self._tools().ceiling_allows()},
                },
            )
        except Exception as exc:
            # Kept: the workflow may have taken the report whose answer was lost.
            # A hold nobody answers goes after a week.
            raise _NotYet(f"The bot did not take the approval request: {exc}") from exc
        return {"status": "awaiting_approval", "approvalId": callback_id}

    def _start_approved(self, bot: str, delivery: dict[str, Any]) -> None:
        """The run goes on after `vibebot/deliver` answers, which the heartbeat
        waits for; the workflow sends the approval again until the run's report
        arrives, and a call already running here is not run again.
        """
        callback_id = str(delivery.get("callbackId") or "")
        key = (bot, callback_id)
        if key in self._sessions.approved_runs:
            return
        self._sessions.approved_runs.add(key)
        task = asyncio.get_running_loop().create_task(
            self._report_approved(bot, delivery)
        )
        self._approved_runs[key] = task

        def done(_: asyncio.Task[None]) -> None:
            self._approved_runs.pop(key, None)
            self._sessions.approved_runs.discard(key)

        task.add_done_callback(done)

    async def _report_approved(self, bot: str, delivery: dict[str, Any]) -> None:
        try:
            await self._run_approved(bot, delivery)
        except Exception as exc:
            logger.warning("Reporting a Vibebot tool approval failed", exc_info=exc)

    async def _run_approved(self, bot: str, delivery: dict[str, Any]) -> None:
        """Runs the approved call once. The call stays held until its report is
        accepted: marked running before the run and given its outcome after, so
        a lost report is sent again and a crash mid-run reports `expired`
        rather than running it twice.
        """
        callback_id = str(delivery.get("callbackId") or "")
        lock = self._sessions.claim_run(bot, callback_id)
        if lock is None:
            # Another app server of this machine runs and reports it.
            return
        try:
            await self._run_claimed(bot, callback_id, delivery)
        finally:
            self._sessions.release_run(lock)

    async def _run_claimed(
        self, bot: str, callback_id: str, delivery: dict[str, Any]
    ) -> None:
        held = self._sessions.held_tool(bot, callback_id)
        if delivery.get("flush") and (held is None or "outcome" not in held):
            # Another app server of this machine reported it meanwhile.
            return
        report: dict[str, Any] = {
            "kind": "tool_result",
            "clientId": self.client_id(bot),
            "requestId": callback_id,
        }
        if held is not None and "outcome" in held:
            report |= held["outcome"]
        elif delivery.get("decision") != "allow":
            # A deny needs nothing held: it stays the user's refusal.
            report |= {"outcome": "rejected"}
        elif held is None:
            report |= {
                "outcome": "expired",
                "error": "this computer no longer held the call: it did not run",
            }
        elif held.get("running"):
            report |= {
                "outcome": "expired",
                "error": "it was running when Vibe stopped on this computer: it may have run",
            }
        else:
            if delivery.get("argsHash") != held["argsHash"]:
                outcome: dict[str, Any] = {
                    "outcome": "failed",
                    "error": "the approval was for other arguments: nothing ran",
                }
            else:
                self._sessions.update_tool(
                    bot, callback_id, required=True, running=True
                )
                outcome = await self._run_held(held)
            self._sessions.update_tool(bot, callback_id, outcome=outcome)
            report |= outcome
        await self._client.report(bot, report)
        self._sessions.drop_tool(bot, callback_id)

    async def _run_held(self, held: dict[str, Any]) -> dict[str, Any]:
        tools = self._tools()
        try:
            _, max_bytes, timeout_s = await tools.mode(held["server"], held["tool"])
            result = await tools.run(
                held["server"],
                held["tool"],
                held["arguments"],
                max_bytes=max_bytes,
                timeout_s=timeout_s,
            )
        except LocalToolFailure as exc:
            return {"outcome": "failed", "error": f"{exc.code}: {exc}"}
        except Exception as exc:
            logger.warning(
                "An approved Vibebot local tool call failed",
                extra={"tool": held["tool"], "server": held["server"]},
                exc_info=exc,
            )
            return {
                "outcome": "failed",
                "error": f"local_tool_failed: {_LOCAL_TOOL_FAILED}",
            }
        return {"outcome": "ok", **result}

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
                    "clientId": self.client_id(bot),
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

    async def _start(self, bot: str, raw: dict[str, Any]) -> None:
        """A `code_local` start the bot holds for this machine: started under
        its key, so a start handed back after a lost report finds the same
        session, then reported. A start the bot no longer waits for (stopped,
        archived, another machine's) leaves no session running.
        """
        report: dict[str, Any] = {
            "clientId": self.client_id(bot),
            "requestId": str(raw.get("requestId") or ""),
            "threadId": str(raw.get("threadId") or ""),
        }
        try:
            delivery = _validated(_StartDelivery, raw)
            await self._require_local_project(delivery.checkout)
            local_session_id, turn_id = await self._client.start_local(
                bot=bot,
                idempotency_key=delivery.request_id,
                thread_id=delivery.thread_id,
                prompt=_brief(
                    delivery.handle,
                    delivery.brief,
                    delivery.done_when,
                    can_message=self._client.session_tools(),
                ),
                title=delivery.title,
                checkout=delivery.checkout.model_dump(by_alias=True)
                if delivery.checkout is not None
                else None,
                model=delivery.model,
                approval_policy=delivery.approval_policy,
            )
        except Exception as exc:
            await self._client.report(
                bot, report | {"kind": "refused", "error": str(exc)}
            )
            return
        accepted = report | {"kind": "accepted", "localSessionId": local_session_id}
        if turn_id:
            accepted["turnId"] = turn_id
        if await self._client.report(bot, accepted):
            await self._client.archive_local(bot=bot, local_session_id=local_session_id)
            return
        self._sessions.of(bot).threads[delivery.thread_id] = VibeBotLocalSession(
            thread_id=delivery.thread_id,
            handle=delivery.handle,
            local_session_id=local_session_id,
            client_id=self.client_id(bot),
        )

    async def _deliver(self, bot: str, deliveries: list[dict[str, Any]]) -> None:
        """Delivers each, in order, then reports it; a delivery whose report
        is lost comes back in the next heartbeat and goes again under the same
        key, which Desktop runs once.
        """
        for delivery in deliveries:
            request_id = str(delivery.get("requestId") or "")
            local_session_id = str(delivery.get("localSessionId") or "")
            if delivery.get("kind") == "tool_approval":
                self._start_approved(bot, delivery)
                continue
            starts = delivery.get("kind") == "start"
            if not request_id or not (
                starts
                or (delivery.get("kind") in {"send", "stop"} and local_session_id)
            ):
                continue
            try:
                if starts:
                    await self._start(bot, delivery)
                else:
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
            "clientId": self.client_id(bot),
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
        close) or `drop_tools` (detach), awaited after them. An approved run is
        not cancelled: the user allowed it, and its tool's timeout bounds it.
        """
        tasks = [f.task for f in self._followers.values() if f.task is not None]
        self._followers.clear()
        for task in tasks:
            task.cancel()
        return tasks

    async def aclose_tools(self) -> None:
        """Waits for the approved runs in flight, bounded by the longest a tool
        may run, then closes the servers.
        """
        await _finish(list(self._approved_runs.values()))
        if self._dropping:
            await asyncio.wait(list(self._dropping), timeout=_RUN_GRACE_SECONDS)
        if self._local_tools is not None:
            await self._local_tools.aclose()

    async def drop_tools(self) -> None:
        """On a detach: the controller lives on, and a call it was running may
        be claimed again by the next connection, which is not a retry. The
        servers in use are taken out now, so the next connection opens its own,
        and closed once the approved runs in flight are done.
        """
        self._claim_nonce = uuid.uuid4().hex
        tools = self._local_tools
        if tools is None:
            return
        mcp = await tools.detach_runtime()
        if mcp is None:
            return
        runs = list(self._approved_runs.values())
        if not runs:
            await mcp.aclose()
            return

        async def close_after_runs() -> None:
            await _finish(runs)
            await mcp.aclose()

        task = asyncio.get_running_loop().create_task(close_after_runs())
        self._dropping.add(task)
        task.add_done_callback(self._dropping.discard)


_RUN_GRACE_SECONDS: Final = 10.0


async def _finish(runs: list[asyncio.Task[None]]) -> None:
    """Approved runs end within their tool's timeout; one still running after
    that is cancelled, and its hold reports that it may have run.
    """
    if not runs:
        return
    _, late = await asyncio.wait(runs, timeout=MAX_CALL_SECONDS + _RUN_GRACE_SECONDS)
    for task in late:
        task.cancel()
    if late:
        _, stuck = await asyncio.wait(late, timeout=_RUN_GRACE_SECONDS)
        if stuck:
            logger.warning(
                "Vibebot approved runs ignored their cancel",
                extra={"count": len(stuck)},
            )


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
