#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import tempfile
import time

from pydantic import JsonValue

from vibe.core.watchdog import (
    CancelResult,
    EventKind,
    IdleResult,
    Incident,
    IncidentState,
    ObserveOnlySupervisor,
    ObserverState,
    QuiesceResult,
    RecoveryCoordinator,
    RecoveryDecision,
    RunState,
    WatchdogPaths,
    WatchdogStore,
    apply_event,
)
from vibe.core.watchdog.detectors import RepeatedCallDetector
from vibe.core.watchdog.event_adapter import PendingWatchdogEvent
from vibe.core.watchdog.incident import IncidentEngine


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    title: str
    intent: str


SCENARIOS = {
    "recovery": Scenario(
        name="recovery",
        title="Repeated failure -> recovery -> verified progress",
        intent="Show one bounded context injection followed by a changed action.",
    ),
    "tilt": Scenario(
        name="tilt",
        title="Observer anomaly -> TILT -> intervention blocked",
        intent="Show fail-safe behavior when observation integrity is uncertain.",
    ),
    "degraded": Scenario(
        name="degraded",
        title="Recovery adapter failure -> degraded state",
        intent="Show that failed intervention is recorded instead of reported as success.",
    ),
}


class DemoRecoveryPort:
    def __init__(self, *, fail_injection: bool = False) -> None:
        self.fail_injection = fail_injection
        self.injections: list[str] = []

    async def quiesce(self, incident: Incident) -> QuiesceResult:
        del incident
        return QuiesceResult(succeeded=True)

    async def cancel_active(self, incident: Incident) -> CancelResult:
        del incident
        return CancelResult(succeeded=False)

    async def wait_until_idle(self, incident: Incident) -> IdleResult:
        del incident
        return IdleResult(succeeded=True)

    async def inject_context(self, content: str) -> None:
        self.injections.append(content)
        if self.fail_injection:
            raise RuntimeError("simulated delivery failure")

    async def continue_once(self, prompt: str, incident: Incident) -> None:
        del prompt, incident

    async def request_approval(self, decision: RecoveryDecision) -> bool:
        del decision
        return False


def _tool_event(
    kind: EventKind, call_id: str, tool_name: str, payload: dict[str, JsonValue]
) -> PendingWatchdogEvent:
    return PendingWatchdogEvent(
        kind=kind,
        observed_at_monotonic=time.monotonic(),
        payload={"tool_call_id": call_id, "tool_name": tool_name, **payload},
        critical=True,
    )


def _enqueue_failed_call(supervisor: ObserveOnlySupervisor, call_id: str) -> None:
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


def _enqueue_changed_action(supervisor: ObserveOnlySupervisor) -> None:
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


async def _run_scenario(
    scenario: Scenario, root: Path
) -> tuple[WatchdogPaths, DemoRecoveryPort, RunState]:
    run_id = f"demo-{scenario.name}"
    paths = WatchdogPaths.for_run(run_id, root=root)
    store = WatchdogStore(paths)
    port = DemoRecoveryPort(fail_injection=scenario.name == "degraded")
    supervisor = ObserveOnlySupervisor(
        run_id=run_id,
        session_id="watchdog-demo",
        store=store,
        incident_engine=IncidentEngine((RepeatedCallDetector(),)),
        recovery=RecoveryCoordinator(
            store=store,
            port=port,
            objective="Fix the parser without repeating the failed command",
        ),
    )
    await supervisor.start()
    if scenario.name == "tilt":
        supervisor._queue.put_nowait(
            PendingWatchdogEvent(
                kind=EventKind.OBSERVER_ANOMALY,
                observed_at_monotonic=time.monotonic(),
                payload={"reason": "demo_sequence_gap"},
                critical=True,
            )
        )
    for attempt in range(1, 5):
        _enqueue_failed_call(supervisor, f"repeat-{attempt}")
    if scenario.name == "recovery":
        _enqueue_changed_action(supervisor)
    try:
        await supervisor.finish()
    except RuntimeError as error:
        if scenario.name != "degraded" or str(error) != "simulated delivery failure":
            raise
    state = await store.load_state()
    if state is None:
        raise RuntimeError("demo produced no Watchdog state")
    _assert_outcome(scenario, state, port)
    return paths, port, state


def _assert_outcome(
    scenario: Scenario, state: RunState, port: DemoRecoveryPort
) -> None:
    incident = state.incident
    if incident is None:
        raise RuntimeError(f"{scenario.name}: no incident detected")
    expected = {
        "recovery": (IncidentState.CLOSED, ObserverState.TRUSTED, 1),
        "tilt": (IncidentState.CONFIRMED, ObserverState.TILT, 0),
        "degraded": (IncidentState.DEGRADED, ObserverState.TRUSTED, 1),
    }[scenario.name]
    actual = (incident.state, state.observer_state, len(port.injections))
    if actual != expected:
        raise RuntimeError(f"{scenario.name}: expected {expected}, got {actual}")


async def _print_trace(
    scenario: Scenario, paths: WatchdogPaths, port: DemoRecoveryPort, *, delay: float
) -> None:
    events = await WatchdogStore(paths).load_events()
    state = RunState.new(run_id=events[0].run_id, session_id=events[0].session_id)
    print(f"\nWATCHDOG DEMO :: {scenario.name.upper()}")
    print(f"+- {scenario.title}")
    print(f"`- {scenario.intent}\n")
    print("SEQ  EVENT                    PHASE          OBSERVER  INCIDENT")
    print("---  -----------------------  -------------  --------  ----------")
    for event in events:
        state = apply_event(state, event)
        incident = state.incident.state.value if state.incident else "-"
        print(
            f"{event.sequence:>3}  {event.kind.value:<23}  "
            f"{state.phase.value:<13}  {state.observer_state.value:<8}  {incident}"
        )
        if delay:
            await asyncio.sleep(delay)
    print("\nRESULT")
    print(f"+- context injections : {len(port.injections)}")
    print(f"+- observer state     : {state.observer_state.value}")
    print(
        f"+- incident state     : {state.incident.state.value if state.incident else '-'}"
    )
    print(f"`- artifacts          : {paths.run_dir}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic Watchdog demonstration scenarios."
    )
    parser.add_argument(
        "--scenario",
        choices=["all", *SCENARIOS],
        default="all",
        help="Scenario to run (default: all).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.08,
        help="Seconds between trace rows; use 0 for instant output.",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        help="Artifact root. Defaults to a new temporary directory.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    if args.delay < 0:
        raise SystemExit("--delay must be non-negative")
    root = args.artifacts or Path(tempfile.mkdtemp(prefix="vibe-watchdog-demo-"))
    selected = (
        SCENARIOS.values() if args.scenario == "all" else [SCENARIOS[args.scenario]]
    )
    for scenario in selected:
        paths, port, _state = await _run_scenario(scenario, root)
        await _print_trace(scenario, paths, port, delay=args.delay)
    print(f"\nPASS :: artifacts retained at {root}")


if __name__ == "__main__":
    asyncio.run(_main())
