from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.widgets import TabbedContent

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage
from vibe.cli.textual_ui.widgets.watchcat_report import WatchcatReportApp
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
from vibe.core.watchdog.demo_report import (
    DemoReport,
    DemoRunReport,
    DemoTraceEntry,
    display_demo_event,
    save_demo_report,
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


def test_watchcat_demo_status_labels_are_unambiguous() -> None:
    def entry(event: str, incident_state: str) -> DemoTraceEntry:
        return DemoTraceEntry(
            sequence=1,
            event=event,
            phase="verification",
            signal_quality="healthy",
            incident_state=incident_state,
        )

    assert display_demo_event(entry("verification_finished", "closed")) == (
        "verification_passed"
    )
    assert display_demo_event(entry("verification_finished", "verifying")) == (
        "verification_failed"
    )
    assert display_demo_event(entry("recovery_finished", "verifying")) == (
        "context_injection_succeeded"
    )


@pytest.mark.asyncio
async def test_watchdog_command_controls_full_runtime_lifecycle(tmp_path: Path) -> None:
    agent_loop, runtime = _runtime(tmp_path)
    app = build_test_vibe_app(agent_loop=agent_loop, watchdog_runtime=runtime)

    async with app.run_test() as pilot:
        for command in (
            "/watchcat",
            "/watchcat pause",
            "/watchcat resume",
            "/watchcat snapshot",
            "/watchcat snapshot list",
            "/watchcat recover",
            "/watchcat off",
            "/watchcat on",
            "/watchcat status",
        ):
            assert await app._handle_command(command)
        await pilot.pause()

        messages = [message._content for message in app.query(UserCommandMessage)]
        assert any("WATCHCAT [PAUSED]" in message for message in messages)
        assert any(
            "observe --> detect --> recover --> verify" in message
            for message in messages
        )
        assert any("Created Watchcat snapshot" in message for message in messages)
        assert any("SNAPSHOTS" in message for message in messages)
        assert any(
            message == "No confirmed recoverable Watchcat incident."
            for message in messages
        )
        assert any("WATCHCAT [OFF]" in message for message in messages)
        assert messages[-1].startswith("## Watchcat")
        assert "WATCHCAT [ENABLED]" in messages[-1]
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
        assert await app._handle_command("/watchcat snapshot parser checkpoint")
        agent_loop.messages.append(
            LLMMessage(role=Role.assistant, content="later work")
        )

        assert await app._handle_command("/watchcat snapshot apply")
        assert app.query_one(WatchdogSnapshotPickerApp)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert list(agent_loop.messages)[-1].content == "later work"

        assert await app._handle_command("/watchcat snapshot apply")
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
        assert await app._handle_command("/watchcat snapshot first")
        assert await app._handle_command("/watchcat snapshot second")
        store = app._watchdog_snapshot_store
        assert store is not None

        assert await app._handle_command("/watchcat snapshot drop")
        assert app.query_one(WatchdogSnapshotDropApp)
        await pilot.press("enter")
        await pilot.pause()
        assert len(await store.list()) == 2

        assert await app._handle_command("/watchcat snapshot drop")
        await pilot.press("down", "enter")
        await pilot.pause()
        assert await store.list() == []
        assert any(
            message._content == "Dropped 2 Watchcat snapshots."
            for message in app.query(UserCommandMessage)
        )


@pytest.mark.asyncio
async def test_watchcat_report_displays_latest_demo_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    save_demo_report(
        DemoReport(
            report_id="demo-report",
            created_at=datetime.now(UTC),
            runs=[
                DemoRunReport(
                    name="recovery",
                    title="Repeated failure recovery",
                    classification="mitigated",
                    detector="repeated_call",
                    issue="confirmed_repeated_failure",
                    trigger="4 exact repeated calls",
                    evidence=["repeat_count=4"],
                    flow=["observe x4", "confirm", "recover", "verify", "close"],
                    mitigation=["inject context", "verify changed action"],
                    outcome="recovered",
                    signal_quality="healthy",
                    incident_state="closed",
                    llm_scores=[],
                    injection_attempts=1,
                    context_injections=1,
                    artifacts="/tmp/demo-recovery",
                    trace=[
                        DemoTraceEntry(
                            sequence=1,
                            event="incident_confirmed",
                            phase="idle",
                            signal_quality="healthy",
                            incident_state="confirmed",
                        )
                    ],
                )
            ],
        )
    )
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        assert await app._handle_command("/watchcat report")
        await pilot.pause()

        report_app = app.query_one(WatchcatReportApp)
        tabs = report_app.query_one(TabbedContent)
        assert tabs.tab_count == 6
        assert tabs.active == "watchcat-summary"
        assert len(report_app.query(".watchcat-metric")) == 5
        assert report_app.query(".watchcat-pipeline-visual")

        await pilot.press("right")
        assert tabs.active == "watchcat-protected"
        await pilot.press("right")
        assert tabs.active == "watchcat-mitigated"
        assert report_app.query(".watchcat-run-card")
        await pilot.press("right", "right", "right")
        assert tabs.active == "watchcat-details"
        await pilot.press("escape")
        await pilot.pause()
        assert not app.query(WatchcatReportApp)


@pytest.mark.asyncio
async def test_watchcat_demo_all_runs_every_scenario_and_opens_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.cli.textual_ui.widgets.watchcat_report import WatchcatReportApp
    from vibe.core.watchdog.demo_report import load_latest_demo_report
    from vibe.core.watchdog.demo_runner import SCENARIOS

    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        assert await app._handle_command("/watchcat demo all")
        for _ in range(80):
            await pilot.pause(0.1)
            if load_latest_demo_report() is not None and app.query(WatchcatReportApp):
                break
        await pilot.pause()

        report = load_latest_demo_report()
        errors = [error._error for error in app.query(ErrorMessage)]
        assert report is not None, errors
        assert len(report.runs) == len(SCENARIOS) + 1
        assert [run.name for run in report.runs] == ["headless-cli", *SCENARIOS]
        assert report.runs[0].incident_state == "closed"
        assert report.runs[0].recovery_prompt is not None
        assert "WATCHCAT RECOVERY HANDOFF" in report.runs[0].recovery_prompt
        by_name = {run.name: run for run in report.runs}
        assert by_name["rewrite-command"].mitigation_attempts == [
            "inject_context",
            "rewrite_command",
        ]
        assert by_name["alternate-tool"].mitigation_attempts[-1] == "alternate_tool"
        assert by_name["snapshot-restore"].snapshot_restores == 1
        assert by_name["llm-recovery"].recovery_llm_calls == 3
        assert by_name["user-handoff"].incident_state == "needs_user"
        assert by_name["user-handoff"].user_handoffs == 1
        assert app.query_one(WatchcatReportApp)


@pytest.mark.asyncio
async def test_watchdog_command_rejects_unknown_control() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        assert await app._handle_command("/watchcat explode")
        await pilot.pause()

        errors = app.query(ErrorMessage)
        assert any(
            error._error
            == "Usage: /watchcat [status|demo|report|on|off|pause|resume|snapshot|recover]"
            for error in errors
        )
