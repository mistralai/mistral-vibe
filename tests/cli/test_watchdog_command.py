from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage
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
            "/watchdog recover",
            "/watchdog replay",
            "/watchdog off",
            "/watchdog replay",
            "/watchdog on",
            "/watchdog status",
        ):
            assert await app._handle_command(command)
        await pilot.pause()

        messages = [message._content for message in app.query(UserCommandMessage)]
        assert any("**Status**: paused" in message for message in messages)
        assert any("Watchdog snapshot:" in message for message in messages)
        assert any(
            message == "No confirmed recoverable Watchdog incident."
            for message in messages
        )
        assert any("No Watchdog events found" in message for message in messages)
        assert any("**Status**: disabled" in message for message in messages)
        assert messages[-1].startswith("## Watchdog")
        assert "**Status**: enabled" in messages[-1]

    assert (runtime.paths.snapshots / "state-000000.json").exists()


@pytest.mark.asyncio
async def test_watchdog_command_rejects_unknown_control() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        assert await app._handle_command("/watchdog explode")
        await pilot.pause()

        errors = app.query(ErrorMessage)
        assert any(
            error._error
            == "Usage: /watchdog [status|on|off|pause|resume|snapshot|recover|replay]"
            for error in errors
        )
