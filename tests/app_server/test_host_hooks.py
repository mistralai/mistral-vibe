from __future__ import annotations

import asyncio
from collections.abc import Mapping
import contextlib
from pathlib import Path
from typing import Any, cast

import pytest

from mistralai_vibe_local_harness.protocol import (
    RustContentBlock,
    RustPreAgentTurnContinue,
    RustPreAgentTurnHookInput,
    RustTextContentBlock,
)
from mistralai_vibe_local_harness.vibe import HookContext, LocalRuntimeAdapterConfig
from mistralai_vibe_local_harness.vibe._foreign_hooks import HostHookResult
from mistralai_vibe_local_harness.vibe._local_actions import HookAgentIdentity
from mistralai_vibe_local_harness.vibe._projection import displayed_user_content
from vibe.app_server._host_hooks import (
    HostHook,
    HostHooks,
    NotificationType,
    SessionStartContexts,
    session_start_context_handler,
    turn_notification,
)
from vibe.app_server.models import PublicError, PublicTurn, PublicTurnStatus


def _runner(
    *, stdout: str = "", failure: str | None = None, delay_s: float = 0.0
) -> tuple[Any, list[tuple[str, Mapping[str, Any]]]]:
    ran: list[tuple[str, Mapping[str, Any]]] = []

    async def run(hook: HostHook, payload: Mapping[str, Any]) -> HostHookResult:
        ran.append((hook.name, payload))
        await asyncio.sleep(delay_s)
        return HostHookResult(stdout=stdout, failure=failure)

    return run, ran


def test_fire_runs_the_hooks_whose_match_selects_the_value() -> None:
    """*Prepare*: Two notification hooks, one scoped to ``turn_completed``.
    *Do*: Fire an ``approval_requested`` notification and wait for the hooks.
    *Assert*: Only the unscoped hook ran, with the payload as given.
    """
    # Prepare
    hooks = HostHooks()
    run, ran = _runner()
    declared = (
        HostHook(name="all", command="true"),
        HostHook(name="done-only", command="true", match="turn_completed"),
    )

    async def scenario() -> None:
        # Do
        hooks.fire(declared, "approval_requested", {"session_id": "s-1"}, run=run)
        await hooks.drain()

    asyncio.run(scenario())

    # Assert
    assert ran == [("all", {"session_id": "s-1"})]


def test_a_failing_background_hook_is_reported() -> None:
    """*Prepare*: A background hook whose command fails.
    *Do*: Fire it and wait for it.
    *Assert*: The failure reaches the reporter, with the hook's name.
    """
    # Prepare
    hooks = HostHooks()
    run, _ = _runner(failure="exited with code 1")
    failures: list[str] = []

    async def scenario() -> None:
        # Do
        hooks.fire(
            (HostHook(name="broken", command="false"),),
            "approval_requested",
            {"hook_event_name": "notification"},
            run=run,
            report_failure=lambda name, reason: failures.append(f"{name}: {reason}"),
        )
        await hooks.drain()

    asyncio.run(scenario())

    # Assert
    assert failures == ["broken: exited with code 1"]


def test_drain_stops_hooks_past_the_grace_and_starts_no_more() -> None:
    """*Prepare*: A background hook that would run for 10 s.
    *Do*: Drain with a short grace, then fire again.
    *Assert*: The drain returns quickly with nothing running, and the later fire
        starts nothing.
    """
    # Prepare
    hooks = HostHooks()
    run, ran = _runner(delay_s=10)
    slow = (HostHook(name="slow", command="true"),)

    async def scenario() -> float:
        hooks.fire(slow, "exit", {}, run=run)
        loop = asyncio.get_running_loop()
        started = loop.time()
        # Do
        await hooks.drain(timeout_s=0.05)
        elapsed = loop.time() - started
        hooks.fire(slow, "exit", {}, run=run)
        await asyncio.sleep(0)
        return elapsed

    elapsed = asyncio.run(scenario())

    # Assert
    assert elapsed < 1
    assert hooks.running == 0
    assert len(ran) == 1


def test_session_start_merges_context_and_messages_in_order() -> None:
    """*Prepare*: Two session_start hooks that both return context and a message,
        and one scoped to ``resume``.
    *Do*: Run them for a ``startup``.
    *Assert*: The scoped one is skipped; contexts join in order; messages are kept.
    """
    # Prepare
    hooks = HostHooks()
    outputs = {
        "first": '{"hook_specific_output": {"additional_context": "a"}, '
        '"system_message": "loaded a"}',
        "second": '{"hook_specific_output": {"additional_context": "b"}}',
        "resume-only": '{"hook_specific_output": {"additional_context": "r"}}',
    }

    async def run(hook: HostHook, _payload: Mapping[str, Any]) -> HostHookResult:
        return HostHookResult(stdout=outputs[hook.name])

    declared = (
        HostHook(name="first", command="true"),
        HostHook(name="second", command="true"),
        HostHook(name="resume-only", command="true", match="resume"),
    )

    # Do
    result = asyncio.run(hooks.run_session_start(declared, "startup", {}, run=run))

    # Assert
    assert result.additional_context == "a\n\nb"
    assert result.system_messages == (("first", "loaded a"),)
    assert result.failures == ()


@pytest.mark.parametrize(
    ("stdout", "failure"),
    [
        ("not json", "stdout is not valid JSON"),
        ("[1]", "stdout did not match the hook response schema: not an object"),
        ("", None),
    ],
)
def test_session_start_reports_invalid_output(stdout: str, failure: str | None) -> None:
    """*Prepare*: A session_start hook printing invalid JSON, JSON that is not an
        object, or nothing.
    *Do*: Run it.
    *Assert*: The first two are failures with no context, named plainly; empty
        output is fine.
    """
    # Prepare
    hooks = HostHooks()
    run, _ = _runner(stdout=stdout)

    # Do
    result = asyncio.run(
        hooks.run_session_start(
            (HostHook(name="h", command="true"),), "startup", {}, run=run
        )
    )

    # Assert
    assert result.additional_context is None
    if failure is None:
        assert result.failures == ()
    else:
        assert result.failures == (("h", failure),)


def _context(tmp_path: Path, *, main: bool = True) -> HookContext:
    return HookContext(
        config=LocalRuntimeAdapterConfig.at(cwd=tmp_path),
        messages=(),
        session_id="s-1",
        agent=HookAgentIdentity(name="default", main=main),
    )


def test_the_builtin_hook_adds_the_pending_context_once(tmp_path: Path) -> None:
    """*Prepare*: A session owing a session_start context.
    *Do*: Run the builtin pre_agent hook on two messages.
    *Assert*: The first message carries the context, hidden from the user's view;
        the second does not.
    """
    # Prepare
    contexts = SessionStartContexts()
    handler = session_start_context_handler(contexts)
    message: list[RustContentBlock] = [RustTextContentBlock(text="hi")]

    async def scenario() -> tuple[list[Any], list[Any]]:
        async def context_text() -> str | None:
            return "branch: main"

        contexts.set("s-1", asyncio.create_task(context_text()))
        # Do
        first = await handler(
            RustPreAgentTurnHookInput(user_content=message), _context(tmp_path)
        )
        second = await handler(
            RustPreAgentTurnHookInput(user_content=message), _context(tmp_path)
        )
        assert isinstance(first.output, RustPreAgentTurnContinue)
        assert isinstance(second.output, RustPreAgentTurnContinue)
        return first.output.user_content, second.output.user_content

    first, second = asyncio.run(scenario())

    # Assert
    assert [block.text for block in first] == ["hi", "branch: main"]
    assert displayed_user_content(first) == message
    assert second == message


def test_the_builtin_hook_leaves_subagent_messages_alone(tmp_path: Path) -> None:
    """*Prepare*: A session owing a context, and a subagent turn.
    *Do*: Run the builtin pre_agent hook for the subagent.
    *Assert*: The subagent's message is unchanged and the context is still owed.
    """
    # Prepare
    contexts = SessionStartContexts()
    handler = session_start_context_handler(contexts)
    message: list[RustContentBlock] = [RustTextContentBlock(text="task")]

    async def scenario() -> tuple[list[Any], str | None]:
        async def context_text() -> str | None:
            return "branch: main"

        contexts.set("s-1", asyncio.create_task(context_text()))
        # Do
        result = await handler(
            RustPreAgentTurnHookInput(user_content=message),
            _context(tmp_path, main=False),
        )
        assert isinstance(result.output, RustPreAgentTurnContinue)
        return result.output.user_content, await contexts.take("s-1")

    content, still_owed = asyncio.run(scenario())

    # Assert
    assert content == message
    assert still_owed == "branch: main"


def test_an_older_open_cannot_drop_a_newer_context() -> None:
    """*Prepare*: A session reopened while attached: the old open's context task,
        then the new open's.
    *Do*: The old adapter, closing, discards its own task.
    *Assert*: The new open's context is still owed.
    """
    # Prepare
    contexts = SessionStartContexts()

    async def scenario() -> str | None:
        async def text(value: str) -> str | None:
            return value

        old = asyncio.create_task(text("old"))
        contexts.set("s-1", old)
        contexts.set("s-1", asyncio.create_task(text("new")))
        # Do
        contexts.discard("s-1", old)
        return await contexts.take("s-1")

    # Assert
    assert asyncio.run(scenario()) == "new"


def test_a_turn_waiting_on_stopped_hooks_goes_on_without_their_context() -> None:
    """*Prepare*: A turn waiting for a session's session_start hooks.
    *Do*: Stop the hooks, as a closing app server does.
    *Assert*: The turn is not cancelled: it gets no context.
    """
    # Prepare
    contexts = SessionStartContexts()

    async def scenario() -> str | None:
        hooks = asyncio.create_task(asyncio.Event().wait())
        contexts.set("s-1", cast(Any, hooks))
        waiting = asyncio.create_task(contexts.take("s-1"))
        await asyncio.sleep(0)
        # Do
        hooks.cancel()
        return await waiting

    # Assert
    assert asyncio.run(scenario()) is None


def test_a_cancelled_turn_leaves_the_hooks_running() -> None:
    """*Prepare*: A turn waiting for a session's session_start hooks.
    *Do*: Cancel the turn.
    *Assert*: The turn is cancelled; the hooks keep running.
    """
    # Prepare
    contexts = SessionStartContexts()

    async def scenario() -> tuple[bool, bool]:
        release = asyncio.Event()

        async def run() -> str:
            await release.wait()
            return "context"

        hooks = asyncio.create_task(run())
        contexts.set("s-1", hooks)
        waiting = asyncio.create_task(contexts.take("s-1"))
        await asyncio.sleep(0)
        # Do
        waiting.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiting
        hooks_alive = not hooks.done()
        release.set()
        await hooks
        return waiting.cancelled(), hooks_alive

    # Assert
    assert asyncio.run(scenario()) == (True, True)


def test_a_cancelled_turn_leaves_the_context_to_the_next_message() -> None:
    """*Prepare*: A turn waiting for a session's session_start hooks.
    *Do*: Cancel the turn, let the hooks finish, then take the context again.
    *Assert*: The next message gets the context.
    """
    # Prepare
    contexts = SessionStartContexts()

    async def scenario() -> str | None:
        release = asyncio.Event()

        async def run() -> str:
            await release.wait()
            return "context"

        contexts.set("s-1", asyncio.create_task(run()))
        waiting = asyncio.create_task(contexts.take("s-1"))
        await asyncio.sleep(0)
        # Do
        waiting.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiting
        release.set()
        return await contexts.take("s-1")

    # Assert
    assert asyncio.run(scenario()) == "context"


def test_a_closing_session_leaves_no_context_behind() -> None:
    """*Prepare*: A turn waiting for session_start hooks that take a while to stop.
    *Do*: Stop the hooks (the session closes), then cancel the turn before they
        are gone.
    *Assert*: No context is left for the closed session.
    """
    # Prepare
    contexts = SessionStartContexts()

    async def scenario() -> dict[str, Any]:
        async def run() -> str:
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
            return "context"

        hooks = asyncio.create_task(run())
        contexts.set("s-1", hooks)
        waiting = asyncio.create_task(contexts.take("s-1"))
        await asyncio.sleep(0)
        # Do
        hooks.cancel()
        waiting.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiting
        with contextlib.suppress(asyncio.CancelledError):
            await hooks
        return dict(cast(Any, contexts)._pending)

    # Assert
    assert asyncio.run(scenario()) == {}


def test_a_turn_cancelled_once_its_hooks_were_stopped_stays_cancelled() -> None:
    """*Prepare*: A turn waiting for session_start hooks.
    *Do*: Stop the hooks, and cancel the turn once they are gone.
    *Assert*: The turn ends cancelled, not as one that goes on without context.
    """
    # Prepare
    contexts = SessionStartContexts()

    async def scenario() -> bool:
        hooks = asyncio.create_task(asyncio.Event().wait())
        contexts.set("s-1", cast(Any, hooks))
        waiting = asyncio.create_task(contexts.take("s-1"))
        await asyncio.sleep(0)
        # Do
        hooks.cancel()
        while not hooks.done():
            await asyncio.sleep(0)
        waiting.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiting
        return waiting.cancelled()

    # Assert
    assert asyncio.run(scenario()) is True


def test_session_end_hooks_wait_for_what_they_follow() -> None:
    """*Prepare*: A task still stopping, as a session's own session_start.
    *Do*: Fire a session_end hook after it.
    *Assert*: The hook starts only once that task is done, and the session_end
        hooks list it as ending meanwhile.
    """
    # Prepare
    hooks = HostHooks()
    run, ran = _runner()

    async def scenario() -> tuple[int, int, int]:
        release = asyncio.Event()
        starting = asyncio.create_task(release.wait())
        hooks.mark_ending(starting)
        # Do
        hooks.fire(
            (HostHook(name="end", command="true"),),
            "other",
            {},
            run=run,
            ending=True,
            after=starting,
        )
        await asyncio.sleep(0.01)
        ran_before, ending = len(ran), len(hooks.ending())
        release.set()
        await hooks.drain()
        return ran_before, ending, len(ran)

    # Assert
    assert asyncio.run(scenario()) == (0, 2, 1)


def test_session_end_hooks_are_listed_while_they_run() -> None:
    """*Prepare*: A session_end hook and a notification hook.
    *Do*: Fire both.
    *Assert*: Only the session_end one is listed as ending, until it finishes.
    """
    # Prepare
    hooks = HostHooks()
    run, _ = _runner(delay_s=0.05)

    async def scenario() -> tuple[int, int]:
        # Do
        hooks.fire(
            (HostHook(name="end", command="true"),), "exit", {}, run=run, ending=True
        )
        hooks.fire(
            (HostHook(name="notify", command="true"),), "turn_completed", {}, run=run
        )
        await asyncio.sleep(0)
        while_running = len(hooks.ending())
        await hooks.drain()
        return while_running, len(hooks.ending())

    # Assert
    assert asyncio.run(scenario()) == (1, 0)


def test_a_tracked_task_after_close_does_not_run() -> None:
    """*Prepare*: A drained runner.
    *Do*: Track new work.
    *Assert*: Nothing runs and no task is returned.
    """
    # Prepare
    hooks = HostHooks()
    ran: list[str] = []

    async def scenario() -> object:
        await hooks.drain()

        async def work() -> None:
            ran.append("ran")

        # Do
        task = hooks.track(work(), name="late")
        await asyncio.sleep(0)
        return task

    # Assert
    assert asyncio.run(scenario()) is None
    assert ran == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PublicTurnStatus.COMPLETED, NotificationType.TURN_COMPLETED),
        (PublicTurnStatus.FAILED, NotificationType.TURN_FAILED),
        (PublicTurnStatus.INTERRUPTED, None),
    ],
)
def test_turn_notification_follows_the_turn_status(
    status: PublicTurnStatus, expected: NotificationType | None
) -> None:
    """*Prepare*: A main-agent turn that completed, failed or was interrupted.
    *Do*: Map it to a notification.
    *Assert*: Completed and failed notify; an interrupt does not.
    """
    # Prepare
    turn = PublicTurn(
        id="t-1",
        session_id="s-1",
        status=status,
        started_at=0,
        error=PublicError(message="boom")
        if status is PublicTurnStatus.FAILED
        else None,
    )

    # Do
    notification = turn_notification(turn, "default")

    # Assert
    assert (notification.type if notification else None) == expected
