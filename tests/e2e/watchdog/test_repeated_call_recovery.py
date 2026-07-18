from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import JsonValue
import pytest

from vibe.core.watchdog import (
    EventKind,
    ObserveOnlySupervisor,
    RecoveryCoordinator,
    WatchdogPaths,
    WatchdogStore,
    render_replay,
)
from vibe.core.watchdog.detectors import RepeatedCallDetector
from vibe.core.watchdog.event_adapter import PendingWatchdogEvent
from vibe.core.watchdog.incident import IncidentEngine
from vibe.core.watchdog.models import IncidentState
from vibe.core.watchdog.recovery_port import RecoveryPort


class DemoRecoveryPort:
    def __init__(self) -> None:
        self.injections: list[str] = []

    async def inject_context(self, content: str) -> None:
        self.injections.append(content)


def _tool_event(
    kind: EventKind, call_id: str, tool_name: str, payload: dict[str, JsonValue]
) -> PendingWatchdogEvent:
    return PendingWatchdogEvent(
        kind=kind,
        observed_at_monotonic=1,
        payload={"tool_call_id": call_id, "tool_name": tool_name, **payload},
        critical=True,
    )


@pytest.mark.asyncio
async def test_demo_detects_recovers_continues_and_persists_trace_three_times(
    tmp_path: Path,
) -> None:
    for attempt in range(3):
        run_id = f"demo-{attempt}"
        paths = WatchdogPaths.for_run(run_id, root=tmp_path)
        store = WatchdogStore(paths)
        port = DemoRecoveryPort()
        supervisor = ObserveOnlySupervisor(
            run_id=run_id,
            session_id="demo-session",
            store=store,
            incident_engine=IncidentEngine((RepeatedCallDetector(),)),
            recovery=RecoveryCoordinator(
                store=store, port=cast(RecoveryPort, port), objective="fix parser"
            ),
        )
        await supervisor.start()
        for call_id in ("repeat-1", "repeat-2", "repeat-3", "repeat-4"):
            supervisor._queue.put_nowait(
                _tool_event(
                    EventKind.TOOL_STARTED,
                    call_id,
                    "bash",
                    {"arguments": {"cmd": "pytest test_parser.py"}},
                )
            )
            supervisor._queue.put_nowait(
                _tool_event(
                    EventKind.TOOL_FINISHED,
                    call_id,
                    "bash",
                    {"result": "1 failed", "repository_fingerprint": "unchanged"},
                )
            )
        supervisor._queue.put_nowait(
            _tool_event(
                EventKind.TOOL_STARTED,
                "changed-1",
                "edit",
                {"arguments": {"path": "parser.py", "fix": "bounds check"}},
            )
        )
        supervisor._queue.put_nowait(
            _tool_event(
                EventKind.TOOL_FINISHED,
                "changed-1",
                "edit",
                {"result": "updated", "repository_fingerprint": "changed"},
            )
        )
        await supervisor.finish()

        state = await store.load_state()
        events = await store.load_events()
        replay = render_replay(paths)

        assert state is not None and state.incident is not None
        assert state.incident.state == IncidentState.CLOSED
        assert len(port.injections) == 1
        assert [event.kind for event in events].count(EventKind.RECOVERY_STARTED) == 1
        assert "incident_confirmed detector=repeated_call" in replay
        assert "recovery_started epoch=1 strategy=inject_context" in replay
        assert "verification_finished epoch=1 status=passed" in replay
        assert "reason=next_action_changed" in replay
        assert "STATE phase=idle observer=trusted incident=closed epoch=1" in replay
