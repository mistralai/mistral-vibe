from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage
from vibe.cli.textual_ui.widgets.watchdog_snapshot import (
    WatchdogSnapshotDropApp,
    WatchdogSnapshotPickerApp,
)
from vibe.core.types import LLMMessage, Role
from vibe.core.watchdog import (
    ObserveOnlySupervisor,
    WatchdogPaths,
    WatchdogRuntime,
    WatchdogStore,
)


def _runtime(tmp_path: Path):
    agent_loop = build_test_agent_loop()
    paths = WatchdogPaths.for_run("command-run", root=tmp_path)
    supervisor = ObserveOnlySupervisor(
        run_id="command-run",
        session_id=agent_loop.session_id,
        store=WatchdogStore(paths),
    )
    runtime = WatchdogRuntime(run_id="command-run", paths=paths, supervisor=supervisor)
    return agent_loop, runtime


@pytest.mark.asyncio
async def test_watchdog_command_controls_full_runtime_lifecycle(tmp_path: Path) -> None:
    agent_loop, runtime = _runtime(tmp_path)
    app = build_test_vibe_app(agent_loop=agent_loop, watchdog_runtime=runtime)

    async with app.run_test() as pilot:
        for command in (
            "/watchdog",
            "/watchdog pause",
            "/watchdog resume",
            "/watchdog snapshot",
            "/watchdog snapshot list",
            "/watchdog recover",
            "/watchdog off",
            "/watchdog on",
            "/watchdog status",
        ):
            assert await app._handle_command(command)
        await pilot.pause()

        messages = [message._content for message in app.query(UserCommandMessage)]
        assert any("WATCHDOG [PAUSED]" in message for message in messages)
        assert any(
            "observe --> detect --> recover --> verify" in message
            for message in messages
        )
        assert any("Created Watchdog snapshot" in message for message in messages)
        assert any("SNAPSHOTS" in message for message in messages)
        assert any(
            message == "No confirmed recoverable Watchdog incident."
            for message in messages
        )
        assert any("WATCHDOG [OFF]" in message for message in messages)
        assert messages[-1].startswith("## Watchdog")
        assert "WATCHDOG [ENABLED]" in messages[-1]
        assert "signal quality : healthy" in messages[-1]

    snapshots = runtime.paths.snapshots / "conversations"
    assert len(list(snapshots.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_watchdog_snapshot_picker_applies_and_close_option_cancels(
    tmp_path: Path,
) -> None:
    agent_loop, runtime = _runtime(tmp_path)
    agent_loop.messages.append(LLMMessage(role=Role.user, content="saved point"))
    app = build_test_vibe_app(agent_loop=agent_loop, watchdog_runtime=runtime)

    async with app.run_test() as pilot:
        assert await app._handle_command("/watchdog snapshot parser checkpoint")
        agent_loop.messages.append(
            LLMMessage(role=Role.assistant, content="later work")
        )

        assert await app._handle_command("/watchdog snapshot apply")
        assert app.query_one(WatchdogSnapshotPickerApp)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert list(agent_loop.messages)[-1].content == "later work"

        assert await app._handle_command("/watchdog snapshot apply")
        await pilot.press("enter")
        await pilot.pause()

        conversation = [
            message.content
            for message in agent_loop.messages
            if message.role is not Role.system
        ]
        assert conversation == ["saved point"]
        assert any(
            "Conversation restored; files unchanged." in message._content
            for message in app.query(UserCommandMessage)
        )


@pytest.mark.asyncio
async def test_watchdog_snapshot_drop_all_defaults_to_cancel_then_confirms(
    tmp_path: Path,
) -> None:
    agent_loop, runtime = _runtime(tmp_path)
    app = build_test_vibe_app(agent_loop=agent_loop, watchdog_runtime=runtime)

    async with app.run_test() as pilot:
        assert await app._handle_command("/watchdog snapshot first")
        assert await app._handle_command("/watchdog snapshot second")
        store = app._watchdog_snapshot_store
        assert store is not None

        assert await app._handle_command("/watchdog snapshot drop")
        assert app.query_one(WatchdogSnapshotDropApp)
        await pilot.press("enter")
        await pilot.pause()
        assert len(await store.list()) == 2

        assert await app._handle_command("/watchdog snapshot drop")
        await pilot.press("down", "enter")
        await pilot.pause()
        assert await store.list() == []
        assert any(
            message._content == "Dropped 2 Watchdog snapshots."
            for message in app.query(UserCommandMessage)
        )


@pytest.mark.asyncio
async def test_watchdog_command_rejects_unknown_control() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        assert await app._handle_command("/watchdog explode")
        await pilot.pause()

        errors = app.query(ErrorMessage)
        assert any(
            error._error
            == "Usage: /watchdog [status|on|off|pause|resume|snapshot|recover]"
            for error in errors
        )
