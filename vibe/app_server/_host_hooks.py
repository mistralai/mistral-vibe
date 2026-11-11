"""Hooks the app server fires itself: ``notification``, ``session_start``, ``session_end``.

The engine has no stop point for these moments, so the Host runs them, outside any
turn:

* ``notification`` follows the CLI's terminal bell (an approval or a question opens,
  the main agent's turn completes or fails), with no focus filter and no debounce.
  It runs in the background and its output is ignored.
* ``session_start`` runs when a main session opens: ``startup``, ``resume``,
  ``clear`` or ``fork``. The user can type at once. Its ``additional_context`` is
  added, out of the user's sight, to the session's next user message; that message
  waits for the hook first.
* ``session_end`` runs when a main session closes: ``exit``, ``clear`` or ``other``.
  It runs in the background and its output is ignored.

Vibe never waits for a background hook, except when the app server closes: it then
waits for the ones still running, :data:`CLOSE_GRACE_S` at most in all, and stops
the rest.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibe.app_server.models import (
    ApprovalCallbackDetail,
    PublicCallbackEntry,
    PublicTurn,
    PublicTurnStatus,
    UserInputCallbackDetail,
)

if TYPE_CHECKING:
    # Type-only: the harness ``vibe`` package costs ~300 modules at startup.
    from mistralai_vibe_local_harness.protocol import RustHarnessHookBinding
    from mistralai_vibe_local_harness.vibe import PreAgentTurnHookHandler
    from mistralai_vibe_local_harness.vibe._foreign_hooks import HostHookResult
    from vibe.core.hooks.models import RuntimeHookDefinition

logger = logging.getLogger(__name__)

# How long a closing app server waits for the hooks still running, all together.
CLOSE_GRACE_S = 5.0
# The ``warning`` code of a hook's ``system_message``: the client shows it as the
# hook's line in the conversation, not among the warnings.
HOOK_MESSAGE_CODE = "hook_message"
# How long stopping the late ones may take, so the close stays bounded.
_STOP_TIMEOUT_S = 1.0

# The builtin pre_agent hook that hands a session_start context to the next user
# message. Bound only on sessions with session_start hooks.
SESSION_START_CONTEXT_BINDING_ID = "builtin:session_start_context"
# The ``_meta`` key marking a block a hook added for the model (see the harness
# projection), so public history leaves it out.
_HOOK_CONTEXT_META_KEY = "vibe.hookContext"


class NotificationType(StrEnum):
    APPROVAL_REQUESTED = "approval_requested"
    QUESTION_ASKED = "question_asked"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"


class SessionStartSource(StrEnum):
    STARTUP = "startup"
    RESUME = "resume"
    CLEAR = "clear"
    FORK = "fork"


class SessionEndReason(StrEnum):
    EXIT = "exit"
    CLEAR = "clear"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class HostHook:
    """One hook the Host fires, from ``hooks.toml`` or a plugin."""

    name: str
    command: str
    match: str | None = None
    timeout_s: float = 60.0
    # A plugin's hook runs in its plugin's directory, with variables naming it.
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    # Set for a plugin's hook: the session resolves its pinned copy from it, so the
    # hook runs the same plugin files as the plugin's other hooks and MCP servers.
    plugin_hook: RuntimeHookDefinition | None = None


@dataclass(frozen=True, slots=True)
class SessionHooks:
    """A session's Host hooks, by type, in run order."""

    notification: tuple[HostHook, ...] = ()
    session_start: tuple[HostHook, ...] = ()
    session_end: tuple[HostHook, ...] = ()


# Runs one hook for the session that fires it: what it printed, or why it failed.
type HostHookRunner = Callable[[HostHook, Mapping[str, Any]], Awaitable[HostHookResult]]


@dataclass(frozen=True, slots=True)
class Notification:
    type: NotificationType
    message: str
    agent_name: str | None
    tool_name: str | None = None


def callback_notification(
    callback: PublicCallbackEntry, agent_name: str | None
) -> Notification | None:
    """What a newly opened approval or question tells the hooks."""
    match callback.detail:
        case ApprovalCallbackDetail(effect=effect):
            return Notification(
                type=NotificationType.APPROVAL_REQUESTED,
                message=f"Vibe needs your approval to run {effect.tool_name}",
                agent_name=agent_name,
                tool_name=effect.tool_name,
            )
        case UserInputCallbackDetail():
            return Notification(
                type=NotificationType.QUESTION_ASKED,
                message="Vibe has a question for you",
                agent_name=agent_name,
            )
    return None


def turn_notification(turn: PublicTurn, agent_name: str | None) -> Notification | None:
    """What the end of a main-agent turn tells the hooks; nothing on an interrupt."""
    match turn.status:
        case PublicTurnStatus.COMPLETED:
            return Notification(
                type=NotificationType.TURN_COMPLETED,
                message="Vibe finished its turn",
                agent_name=agent_name,
            )
        case PublicTurnStatus.FAILED:
            reason = turn.error.message if turn.error is not None else "unknown error"
            return Notification(
                type=NotificationType.TURN_FAILED,
                message=f"Vibe's turn failed: {reason}",
                agent_name=agent_name,
            )
    return None


def _selected(hooks: Sequence[HostHook], value: str) -> list[HostHook]:
    from mistralai_vibe_local_harness.vibe._hook_matcher import match_predicate

    return [hook for hook in hooks if match_predicate(hook.match)(value)]


@dataclass(frozen=True, slots=True)
class SessionStartResult:
    """What the session_start hooks returned, merged in run order."""

    additional_context: str | None = None
    # ``(hook name, message)`` for each hook that returned a ``system_message``.
    system_messages: tuple[tuple[str, str], ...] = ()
    failures: tuple[tuple[str, str], ...] = ()


def _session_start_output(stdout: str) -> tuple[str | None, str | None, str | None]:
    """``(additional_context, system_message, failure)`` from a hook's stdout."""
    text = stdout.strip()
    if not text:
        return None, None, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, None, "stdout is not valid JSON"
    if not isinstance(parsed, dict):
        return (
            None,
            None,
            "stdout did not match the hook response schema: not an object",
        )
    hook_output = parsed.get("hook_specific_output")
    extra = (
        hook_output.get("additional_context") if isinstance(hook_output, dict) else None
    )
    message = parsed.get("system_message")
    return (
        extra if isinstance(extra, str) and extra else None,
        message if isinstance(message, str) and message else None,
        None,
    )


async def _run_safely(
    run: HostHookRunner, hook: HostHook, payload: Mapping[str, Any]
) -> HostHookResult:
    try:
        return await run(hook, payload)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        from mistralai_vibe_local_harness.vibe._foreign_hooks import HostHookResult

        return HostHookResult(failure=f"hook failed: {error}")


class HostHooks:
    """Runs the Host's hooks for every session of one app server.

    Background hooks are tracked here, so a closing app server waits for them
    all at once (:meth:`drain`), whichever session started them. ``run`` runs one
    hook for the session that fires it.
    """

    def __init__(self) -> None:
        self._running: set[asyncio.Task[Any]] = set()
        # The session_end hooks still running: a session_start waits for them.
        self._ending: set[asyncio.Task[Any]] = set()
        self._closed = False

    @property
    def running(self) -> int:
        return len(self._running)

    def ending(self) -> tuple[asyncio.Task[Any], ...]:
        """The session_end hooks running now, which a session opening waits for."""
        return tuple(self._ending)

    def track[T](
        self, work: Coroutine[Any, Any, T], name: str
    ) -> asyncio.Task[T] | None:
        """Run ``work`` as a task a closing app server waits for, or not at all
        once it is closing.
        """
        if self._closed:
            work.close()
            return None
        task = asyncio.create_task(work, name=name)
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return task

    def fire(
        self,
        hooks: Sequence[HostHook],
        value: str,
        payload: Mapping[str, Any],
        *,
        run: HostHookRunner,
        report_failure: Callable[[str, str], None] | None = None,
        ending: bool = False,
        after: asyncio.Task[Any] | None = None,
    ) -> tuple[asyncio.Task[None], ...]:
        """Start, in the background, every hook whose ``match`` selects ``value``,
        and return their tasks.

        ``ending`` marks session_end hooks, which a session opening waits for.
        Each hook starts once ``after`` is done.
        """
        started: list[asyncio.Task[None]] = []
        for hook in _selected(hooks, value):
            task = self.track(
                self._run_in_background(run, hook, payload, report_failure, after),
                name=f"vibe-host-hook-{hook.name}",
            )
            if task is None:
                continue
            started.append(task)
            if ending:
                self.mark_ending(task)
        return tuple(started)

    def mark_ending(self, task: asyncio.Task[Any]) -> None:
        """Make the sessions opening from now on wait for ``task``."""
        self._ending.add(task)
        task.add_done_callback(self._ending.discard)

    async def _run_in_background(
        self,
        run: HostHookRunner,
        hook: HostHook,
        payload: Mapping[str, Any],
        report_failure: Callable[[str, str], None] | None,
        after: asyncio.Task[Any] | None = None,
    ) -> None:
        if after is not None:
            await asyncio.wait((after,))
        failure = (await _run_safely(run, hook, payload)).failure
        if failure is None:
            return
        logger.warning(
            "%s hook %r failed: %s", payload.get("hook_event_name"), hook.name, failure
        )
        if report_failure is not None:
            report_failure(hook.name, failure)

    async def run_session_start(
        self,
        hooks: Sequence[HostHook],
        source: str,
        payload: Mapping[str, Any],
        *,
        run: HostHookRunner,
    ) -> SessionStartResult:
        """Run the selected session_start hooks one after the other, and merge what
        they return.
        """
        contexts: list[str] = []
        messages: list[tuple[str, str]] = []
        failures: list[tuple[str, str]] = []
        for hook in _selected(hooks, source):
            outcome = await _run_safely(run, hook, payload)
            if outcome.failure is not None:
                failures.append((hook.name, outcome.failure))
                continue
            extra, message, failure = _session_start_output(outcome.stdout)
            if failure is not None:
                failures.append((hook.name, failure))
                continue
            if extra is not None:
                contexts.append(extra)
            if message is not None:
                messages.append((hook.name, message))
        return SessionStartResult(
            additional_context="\n\n".join(contexts) if contexts else None,
            system_messages=tuple(messages),
            failures=tuple(failures),
        )

    async def drain(self, timeout_s: float = CLOSE_GRACE_S) -> None:
        """Start no more hooks, wait for those still running, then stop the ones
        past ``timeout_s``.

        Stopping one kills its command and the processes it started.
        """
        self._closed = True
        pending = set(self._running)
        if not pending:
            return
        _done, late = await asyncio.wait(pending, timeout=timeout_s)
        for task in late:
            task.cancel()
        if late:
            await asyncio.wait(late, timeout=_STOP_TIMEOUT_S)


class SessionStartContexts:
    """The ``session_start`` context each session still owes its next user message.

    A session's entry is the task running its hooks, so the next message waits for
    them. The builtin pre_agent hook takes it when that message starts its turn.
    It runs after the user's own pre_agent hooks, and the first one to block a
    message stops the others, so a blocked message leaves it for the next one.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Task[str | None]] = {}

    def set(self, session_id: str, task: asyncio.Task[str | None]) -> None:
        previous = self._pending.get(session_id)
        if previous is not None and previous is not task:
            previous.cancel()
        self._pending[session_id] = task

    def discard(self, session_id: str, task: asyncio.Task[str | None]) -> None:
        """Drop ``task`` if it is still the session's: a newer open of the same
        session (a resume of the attached one) keeps its own.
        """
        if self._pending.get(session_id) is task:
            del self._pending[session_id]
            task.cancel()

    async def take(self, session_id: str) -> str | None:
        task = self._pending.pop(session_id, None)
        if task is None:
            return None
        try:
            # Shielded: a turn cancelled while it waits leaves the hooks running.
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The hooks were stopped (the app server closing), not this turn:
            # the turn goes on without the context.
            if task.cancelled() and not _cancelling():
                return None
            # The turn was cancelled, not the hooks: the next message gets the
            # context, unless the session opened again meanwhile. Hooks being
            # stopped belong to a closing session, which has no next message.
            if not task.cancelled() and task.cancelling() == 0:
                self._pending.setdefault(session_id, task)
            raise
        except Exception:
            logger.warning("session_start context failed", exc_info=True)
            return None


def _cancelling() -> bool:
    """Whether the current task is being cancelled."""
    current = asyncio.current_task()
    return current is not None and current.cancelling() > 0


def session_start_context_handler(
    contexts: SessionStartContexts,
) -> PreAgentTurnHookHandler:
    """The builtin pre_agent hook body that adds a session's pending context.

    Bound after the user's pre_agent hooks, so the context lands after whatever
    they did to the message. Main sessions only. It never raises: a failure only
    loses the context.
    """
    from mistralai_vibe_local_harness.protocol import (
        RustPreAgentTurnContinue,
        RustPreAgentTurnHookInput,
        RustPreAgentTurnHookResult,
        RustTextContentBlock,
    )
    from mistralai_vibe_local_harness.vibe import HookContext

    async def handler(
        hook_input: RustPreAgentTurnHookInput, context: HookContext
    ) -> RustPreAgentTurnHookResult:
        content = list(hook_input.user_content)
        if context.agent is not None and not context.agent.main:
            return RustPreAgentTurnHookResult(
                output=RustPreAgentTurnContinue(user_content=content)
            )
        try:
            extra = await contexts.take(context.session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            extra = None
        if extra:
            block = RustTextContentBlock.model_validate({
                "type": "text",
                "text": extra,
                "_meta": {_HOOK_CONTEXT_META_KEY: True},
            })
            content = [*content, block]
        return RustPreAgentTurnHookResult(
            output=RustPreAgentTurnContinue(user_content=content)
        )

    return handler


def session_start_context_binding(order: int) -> RustHarnessHookBinding:
    """The Core binding for :func:`session_start_context_handler`."""
    from mistralai_vibe_local_harness.protocol import (
        RustAlwaysHookSelector,
        RustHarnessHookBinding,
    )

    return RustHarnessHookBinding(
        id=SESSION_START_CONTEXT_BINDING_ID,
        point="pre_agent_turn",
        order=order,
        selector=RustAlwaysHookSelector(),
    )
