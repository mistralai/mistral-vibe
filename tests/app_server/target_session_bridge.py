"""Send a Vibe Session's commands to a real target-runtime Session, in tests.

The app server has no connection to the target runtime yet. Until it has one,
this bridge puts a real target Session under the adapter's commands: Turn
start, steer and interrupt, and callback answers go to a ``LocalSessionManager``
Session of the target runtime, whose errors come back to the adapter as the
target raises them. Translating those errors is the job of the code under test.

What it leaves out, and a real connection does differently:

- Reads and events still come from the local runtime's Session, so Vibe's
  history and open callbacks are the local Session's, not the target's. Only
  the bare ``session/read`` goes to the target, so the active Turn ID that a
  Turn conflict names is the target's. A test
  drives the target Session itself, through ``TargetSession``, into the state
  it needs, and names which target callback a Vibe callback stands for.
- It converts the local runtime's copied Session API models to the target's
  and back through their JSON, and swaps the Session ID.
- It answers the target with a raw approval (once, for this Session, deny),
  taken from ``RAW_APPROVALS``; Vibe's own choices are not mapped here. The
  target has no permanent scope, so "approve permanently" goes as "session".
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator, Mapping
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import httpx
from mistralai.vibe.harness.app_server.session import (
    AgentConfig,
    PermissionPolicy,
    PermissionRule,
    procedures as target_procedures,
)
from mistralai.vibe.harness.app_server.session.local import (
    LocalManagerAttachment,
    LocalSessionTransport,
)
from mistralai.vibe.harness.app_server.session.local_manager import (
    LocalSessionBinding,
    LocalSessionManager,
    LocalSessionWorkspace,
)
from mistralai.vibe.harness.app_server.session.models import events as target_events
from mistralai.vibe.harness.runtime.hosts.local.tools import LocalWorkspaceTools
from mistralai.vibe.harness.runtime.session.definition import SessionDefinition
import respx

from mistralai_vibe_local_harness.session_api.app_server.session.models.base import (
    SessionModel,
)
from mistralai_vibe_local_harness.vibe import UnifiedHarnessSessionBackend
from mistralai_vibe_local_harness.vibe._session_api_transport import (
    ChildSessions,
    LocalSessionApiTransport,
    session_api_view_of,
)
from tests.constants import MISTRAL_BASE_URL

TARGET_MODEL = "mistral-medium-latest"
TARGET_WRITE_TOOL = "write_file"
"""The target's workspace tool that asks for approval."""

type Json = dict[str, Any]

RAW_APPROVALS: Mapping[str, Json] = {
    "approve": {"decision": "allow", "scope": "once"},
    "approve_for_session": {"decision": "allow", "scope": "session"},
    "approve_permanently": {"decision": "allow", "scope": "session"},
    "deny": {"decision": "deny"},
}
"""The target's raw answer for each Vibe approval choice the bridge sends."""

TARGET_COMMANDS = frozenset({
    # The bare read: the commands read the active Turn ID with it. The adapter's
    # reads of history and events take other paths, which stay local.
    "app_server/session/read",
    "app_server/session/turn/start",
    "app_server/session/turn/steer",
    "app_server/session/turn/interrupt",
    "app_server/session/callback/result",
})
"""What the bridge sends to the target Session by default."""

TARGET_ANSWERS = frozenset({"app_server/session/callback/result"})
"""Callback answers only: Turns run on the local Session, so Vibe sees its
callbacks open, and their answers go to the target callbacks they are mapped to."""


@contextmanager
def target_model_facts() -> Iterator[None]:
    """Serve the facts of the target runtime's model, which it reads at start.

    The target reads them with HTTPX, which the suite's default respx mocker
    (HTTPCore2) does not patch, so they would otherwise go to the network.
    Without them the target adds a warning notice to the Session's history.
    Its completions go through the Mistral SDK on HTTPX2: the suite's mocker
    answers them, on the same route as Vibe's own.
    """
    with respx.mock(
        using="httpcore", assert_all_mocked=True, assert_all_called=False
    ) as router:
        router.get(f"{MISTRAL_BASE_URL}/v1/models/{TARGET_MODEL}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": TARGET_MODEL,
                    "max_context_length": 128_000,
                    "capabilities": {"vision": False},
                },
            )
        )
        yield


def target_agent_config(workspace: Path) -> AgentConfig:
    """A target Session that writes files in ``workspace`` only after approval."""
    return AgentConfig(
        completion={
            "type": "mistral",
            "model": TARGET_MODEL,
            "base_url": MISTRAL_BASE_URL,
        },
        sandbox_access="files",
        workdir=str(workspace),
        permission_policy=PermissionPolicy(
            default="allow",
            rules=(
                PermissionRule(
                    tools=(f"file_system.{TARGET_WRITE_TOOL}",), decision="ask"
                ),
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class TargetSession:
    """One target Session, driven through its own Session API."""

    attachment: LocalManagerAttachment
    session_id: str

    async def start_turn(self, text: str) -> str:
        """Start a Turn with ``text``; return its ID."""
        started = await self.attachment.request(
            "app_server/session/turn/start",
            target_procedures.TurnStartParams.model_validate({
                "sessionId": self.session_id,
                "message": [{"type": "text", "text": text}],
            }),
            target_procedures.TurnStartResponse,
        )
        return started.turn.id

    async def read(self) -> Json:
        read = await self.attachment.request(
            "app_server/session/read",
            target_procedures.SessionReadParams(session_id=self.session_id),
            target_procedures.SessionReadResponse,
        )
        return read.model_dump(mode="json", by_alias=True)

    async def active_callback_ids(self) -> list[str]:
        return [
            callback["id"]
            for callback in (await self.read())["state"]["activeCallbacks"]
        ]

    async def open_callback_id(self, *, timeout: float = 5.0) -> str:
        """The ID of the Session's first callback, from its ``callback_opened`` event.

        The event stays in the Session's events after the callback closes, so a
        slow test still finds a callback that timed out.
        """
        opened = await self.next_event(
            lambda payload: payload["type"] == "callback_opened", timeout=timeout
        )
        return opened["callback"]["id"]

    async def wait_until_callback_resolved(
        self, callback_id: str, *, timeout: float = 5.0
    ) -> Json:
        """The ``callback_resolved`` event of ``callback_id``, once the Session has it."""
        return await self.next_event(
            lambda payload: (
                payload["type"] == "callback_resolved"
                and payload["callbackId"] == callback_id
            ),
            timeout=timeout,
        )

    async def next_event(
        self, matches: Callable[[Json], bool], *, timeout: float = 5.0
    ) -> Json:
        """The payload of the Session's first event that ``matches``, waiting for it."""
        async with asyncio.timeout(timeout), aclosing(self._events()) as events:
            async for event in events:
                if matches(event["payload"]):
                    return event["payload"]
        raise AssertionError("The target Session's event stream ended")

    async def stored_events(self, *, timeout: float = 5.0) -> list[Json]:
        """The payloads of every event the Session holds now, oldest first."""
        last_event_id = (await self.read())["lastEventId"]
        payloads: list[Json] = []
        if last_event_id is None:
            return payloads
        async with asyncio.timeout(timeout), aclosing(self._events()) as events:
            async for event in events:
                payloads.append(event["payload"])
                if event["eventId"] == last_event_id:
                    return payloads
        raise AssertionError("The target Session's event stream ended")

    async def _events(self) -> AsyncGenerator[Json]:
        """The Session's events from its first, through its public event read.

        It skips the stream's markers and heartbeats, and waits for new events.
        """
        items = self.attachment.read_events(
            target_procedures.EventsReadParams(session_id=self.session_id)
        )
        try:
            async for item in items:
                if isinstance(item, target_events.Event):
                    yield item.model_dump(mode="json", by_alias=True)
                elif isinstance(item, target_events.EventBatch):
                    for event in item.events:
                        yield event.model_dump(mode="json", by_alias=True)
        finally:
            if isinstance(items, AsyncGenerator):
                await items.aclose()

    async def wait_until_idle(self, *, timeout: float = 5.0) -> None:
        async with asyncio.timeout(timeout):
            while (await self.read())["state"]["activeTurnId"] is not None:
                await asyncio.sleep(0.02)


@dataclass(slots=True)
class TargetRuntime:
    """Target-runtime Sessions, under one ``LocalSessionManager``.

    ``callback_timeout`` is how long an open callback waits for its answer.
    """

    root: Path
    callback_timeout: timedelta = timedelta(minutes=5)
    _manager: LocalSessionManager | None = None
    _attachments: list[LocalManagerAttachment] = field(default_factory=list)

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    async def start_session(self) -> TargetSession:
        attachment = LocalSessionTransport.attach(self._started_manager())
        self._attachments.append(attachment)
        started = await attachment.request(
            "app_server/session/start",
            target_procedures.SessionStartParams(
                agent_config=target_agent_config(self.workspace)
            ),
            target_procedures.SessionStartResponse,
        )
        return TargetSession(attachment, started.state.session.id)

    async def close(self) -> None:
        while self._attachments:
            await self._attachments.pop().close()
        if self._manager is not None:
            await self._manager.close()
            self._manager = None

    def _started_manager(self) -> LocalSessionManager:
        if self._manager is not None:
            return self._manager
        self.workspace.mkdir(parents=True, exist_ok=True)
        tools = LocalWorkspaceTools(self.workspace)

        async def bind(
            definition: SessionDefinition, _root: LocalSessionWorkspace | None
        ) -> LocalSessionBinding:
            return LocalSessionBinding(
                definition=definition,
                runtime_builtin_tools=tools.handlers,
                filesystem=tools.filesystem,
            )

        self._manager = LocalSessionManager(
            binding_factory=bind, callback_timeout=self.callback_timeout
        )
        return self._manager


class TargetCommandsTransport(LocalSessionApiTransport):
    """The local Session's transport, with the ``forwarded`` methods sent to ``target``.

    ``callback_ids`` names, for a Vibe callback ID, the target callback its
    answer goes to (the same ID when it names none); ``RAW_APPROVALS`` gives the
    raw answer for each Vibe choice.
    """

    def __init__(
        self,
        session: UnifiedHarnessSessionBackend,
        *,
        children: ChildSessions | None,
        target: TargetSession,
        forwarded: frozenset[str] = TARGET_COMMANDS,
        callback_ids: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(session, session_api_view_of(session), children=children)
        self._target = target
        self._forwarded = forwarded
        self._callback_ids: Mapping[str, str] = (
            callback_ids if callback_ids is not None else {}
        )

    async def request[ResultT: SessionModel](
        self, method: str, params: SessionModel, result_type: type[ResultT]
    ) -> ResultT:
        if method not in self._forwarded:
            return await super().request(method, params, result_type)
        wire = self._to_target(params.model_dump(mode="json", by_alias=True))
        if method == "app_server/session/callback/result":
            wire = self._target_answer(wire)
        target_params = getattr(target_procedures, type(params).__name__)
        target_result = getattr(target_procedures, result_type.__name__)
        result = await self._target.attachment.request(
            method, target_params.model_validate(wire), target_result
        )
        return result_type.model_validate(
            self._to_vibe(result.model_dump(mode="json", by_alias=True))
        )

    def _target_answer(self, wire: Json) -> Json:
        result = cast(Json, wire["result"])
        callback_id = self._callback_ids.get(result["callbackId"], result["callbackId"])
        result = {**result, "callbackId": callback_id}
        output = result.get("output")
        if isinstance(output, dict) and output.get("type") == "approval":
            choice = cast(Json, output["decision"])["type"]
            result["output"] = RAW_APPROVALS[choice]
        return {**wire, "result": result}

    def _to_target(self, wire: Json) -> Json:
        return cast(Json, _replace(wire, self.session_id, self._target.session_id))

    def _to_vibe(self, wire: Json) -> Json:
        return cast(Json, _replace(wire, self._target.session_id, self.session_id))


def _replace(value: object, old: str, new: str) -> object:
    if isinstance(value, str):
        return new if value == old else value
    if isinstance(value, list):
        return [_replace(item, old, new) for item in cast(list[object], value)]
    if isinstance(value, dict):
        return {
            key: _replace(item, old, new)
            for key, item in cast(dict[str, object], value).items()
        }
    return value


__all__ = [
    "RAW_APPROVALS",
    "TARGET_ANSWERS",
    "TARGET_COMMANDS",
    "TARGET_MODEL",
    "TARGET_WRITE_TOOL",
    "TargetCommandsTransport",
    "TargetRuntime",
    "TargetSession",
    "target_agent_config",
    "target_model_facts",
]
