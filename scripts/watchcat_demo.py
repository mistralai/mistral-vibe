#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import time
from uuid import uuid4

from pydantic import JsonValue

from vibe.core.paths._vibe_home import VIBE_HOME
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
    TiltEvaluation,
    TiltEvaluationRequest,
    WatchdogEvent,
    WatchdogPaths,
    WatchdogStore,
    apply_event,
)
from vibe.core.watchdog.demo_report import DemoReport, DemoRunReport, save_demo_report
from vibe.core.watchdog.detectors import RepeatedCallDetector
from vibe.core.watchdog.event_adapter import PendingWatchdogEvent
from vibe.core.watchdog.incident import IncidentEngine


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    title: str
    intent: str
    trigger: str


SCENARIOS = {
    "guarded": Scenario(
        name="guarded",
        title="Three repeated failures -> no incident",
        intent="Prove that more than three exact repeats are required.",
        trigger="none; only 3 exact repeated calls",
    ),
    "recovery": Scenario(
        name="recovery",
        title="Repeated failure -> recovery -> verified progress",
        intent="Show one bounded context injection followed by a changed action.",
        trigger="4 exact calls + same result + unchanged repository",
    ),
    "signal": Scenario(
        name="signal",
        title="Observer anomaly -> LLM score -> deterministic recovery",
        intent="Show degraded signal quality using a score without selecting an action.",
        trigger="4 exact failures + observer anomaly + LLM score 92/100",
    ),
    "signal-blocked": Scenario(
        name="signal-blocked",
        title="Observer anomaly -> low LLM score -> intervention blocked",
        intent="Show that deterministic policy rejects recovery below threshold.",
        trigger="4 exact failures + observer anomaly + LLM score 40/100",
    ),
    "degraded": Scenario(
        name="degraded",
        title="Recovery adapter failure -> degraded state",
        intent="Show that failed intervention is recorded instead of reported as success.",
        trigger="4 exact failures + recovery adapter delivery failure",
    ),
}


class DemoRecoveryPort:
    def __init__(self, *, fail_injection: bool = False) -> None:
        self.fail_injection = fail_injection
        self.injection_attempts = 0
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
        self.injection_attempts += 1
        if self.fail_injection:
            raise RuntimeError("simulated delivery failure")
        self.injections.append(content)

    async def continue_once(self, prompt: str, incident: Incident) -> None:
        del prompt, incident

    async def request_approval(self, decision: RecoveryDecision) -> bool:
        del decision
        return False


class DemoTiltEvaluator:
    def __init__(self, score: int) -> None:
        self.score = score
        self.requests: list[TiltEvaluationRequest] = []

    async def evaluate(self, request: TiltEvaluationRequest) -> TiltEvaluation:
        self.requests.append(request)
        return TiltEvaluation(score=self.score)


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


def _signal_quality(state: RunState) -> str:
    return "healthy" if state.observer_state == ObserverState.TRUSTED else "degraded"


async def _run_scenario(
    scenario: Scenario, root: Path
) -> tuple[WatchdogPaths, DemoRecoveryPort, DemoTiltEvaluator, RunState]:
    run_id = f"demo-{scenario.name}"
    paths = WatchdogPaths.for_run(run_id, root=root)
    store = WatchdogStore(paths)
    port = DemoRecoveryPort(fail_injection=scenario.name == "degraded")
    evaluator = DemoTiltEvaluator(score=40 if scenario.name == "signal-blocked" else 92)
    supervisor = ObserveOnlySupervisor(
        run_id=run_id,
        session_id="watchcat-demo",
        store=store,
        incident_engine=IncidentEngine((RepeatedCallDetector(),)),
        recovery=RecoveryCoordinator(
            store=store,
            port=port,
            objective="Fix the parser without repeating the failed command",
        ),
        tilt_evaluator=(
            evaluator if scenario.name in {"signal", "signal-blocked"} else None
        ),
    )
    await supervisor.start()
    if scenario.name in {"signal", "signal-blocked"}:
        supervisor._queue.put_nowait(
            PendingWatchdogEvent(
                kind=EventKind.OBSERVER_ANOMALY,
                observed_at_monotonic=time.monotonic(),
                payload={"reason": "demo_sequence_gap"},
                critical=True,
            )
        )
    attempts = 3 if scenario.name == "guarded" else 4
    for attempt in range(1, attempts + 1):
        _enqueue_failed_call(supervisor, f"repeat-{attempt}")
    if scenario.name in {"recovery", "signal"}:
        _enqueue_changed_action(supervisor)
    try:
        await supervisor.finish()
    except RuntimeError as error:
        if scenario.name != "degraded" or str(error) != "simulated delivery failure":
            raise
    state = await store.load_state()
    if state is None:
        raise RuntimeError("demo produced no Watchcat state")
    _assert_outcome(scenario, state, port, evaluator)
    return paths, port, evaluator, state


def _assert_outcome(
    scenario: Scenario,
    state: RunState,
    port: DemoRecoveryPort,
    evaluator: DemoTiltEvaluator,
) -> None:
    incident = state.incident
    expected = {
        "guarded": (None, ObserverState.TRUSTED, 0),
        "recovery": (IncidentState.CLOSED, ObserverState.TRUSTED, 1),
        "signal": (IncidentState.CLOSED, ObserverState.TILT, 1),
        "signal-blocked": (IncidentState.CONFIRMED, ObserverState.TILT, 0),
        "degraded": (IncidentState.DEGRADED, ObserverState.TRUSTED, 0),
    }[scenario.name]
    actual = (
        incident.state if incident is not None else None,
        state.observer_state,
        len(port.injections),
    )
    if actual != expected:
        raise RuntimeError(f"{scenario.name}: expected {expected}, got {actual}")
    expected_attempts = 1 if scenario.name in {"recovery", "signal", "degraded"} else 0
    if port.injection_attempts != expected_attempts:
        raise RuntimeError(
            f"{scenario.name}: expected {expected_attempts} injection attempts, "
            f"got {port.injection_attempts}"
        )
    expected_evaluations = 1 if scenario.name in {"signal", "signal-blocked"} else 0
    if len(evaluator.requests) != expected_evaluations:
        raise RuntimeError(
            f"{scenario.name}: expected {expected_evaluations} evaluations, "
            f"got {len(evaluator.requests)}"
        )


async def _print_trace(
    scenario: Scenario,
    paths: WatchdogPaths,
    port: DemoRecoveryPort,
    evaluator: DemoTiltEvaluator,
    *,
    delay: float,
) -> DemoRunReport:
    events = await WatchdogStore(paths).load_events()
    state = RunState.new(run_id=events[0].run_id, session_id=events[0].session_id)
    print(f"\nWATCHCAT DEMO :: {scenario.name.upper()}")
    print(f"+- {scenario.title}")
    print(f"`- {scenario.intent}\n")
    print("SEQ  EVENT                    PHASE          SIGNAL    INCIDENT")
    print("---  -----------------------  -------------  --------  ----------")
    for event in events:
        state = apply_event(state, event)
        incident = state.incident.state.value if state.incident else "-"
        signal_quality = _signal_quality(state)
        print(
            f"{event.sequence:>3}  {event.kind.value:<23}  "
            f"{state.phase.value:<13}  {signal_quality:<8}  {incident}"
        )
        if delay:
            await asyncio.sleep(delay)
    print("\nRESULT")
    scores = str([evaluator.score]) if evaluator.requests else "[]"
    print(f"+- LLM eval scores    : {scores}")
    print(
        f"+- context injections : {len(port.injections)}/{port.injection_attempts} "
        "successful"
    )
    print(f"+- signal quality     : {_signal_quality(state)}")
    print(
        f"+- incident state     : {state.incident.state.value if state.incident else '-'}"
    )
    print(f"`- artifacts          : {paths.run_dir}")
    return _build_run_report(scenario, events, state, port, evaluator, paths)


def _build_run_report(
    scenario: Scenario,
    events: list[WatchdogEvent],
    state: RunState,
    port: DemoRecoveryPort,
    evaluator: DemoTiltEvaluator,
    paths: WatchdogPaths,
) -> DemoRunReport:
    repeat_count = sum(
        event.kind == EventKind.TOOL_FINISHED
        and event.payload.get("tool_name") == "bash"
        for event in events
    )
    flow = [f"observe x{repeat_count}"]
    labels = {
        EventKind.INCIDENT_SUSPECTED: "suspect",
        EventKind.INCIDENT_CONFIRMED: "confirm",
        EventKind.TILT_EVALUATED: "LLM score",
        EventKind.RECOVERY_STARTED: "recover",
        EventKind.RECOVERY_FAILED: "recovery failed",
        EventKind.RECOVERY_FINISHED: "recovered",
        EventKind.VERIFICATION_STARTED: "verify",
        EventKind.VERIFICATION_FINISHED: "close",
    }
    for event in events:
        label = labels.get(event.kind)
        if label is not None and (not flow or flow[-1] != label):
            flow.append(label)
    incident_state = state.incident.state.value if state.incident else "none"
    if state.incident is None:
        flow.extend(["no trigger", "finish"])
        outcome = "protected"
    elif state.incident.state == IncidentState.CLOSED:
        outcome = "recovered"
    elif state.incident.state == IncidentState.DEGRADED:
        outcome = "failure recorded"
    else:
        flow.append("blocked")
        outcome = "intervention blocked"
    return DemoRunReport(
        name=scenario.name,
        title=scenario.title,
        trigger=scenario.trigger,
        flow=flow,
        outcome=outcome,
        signal_quality=_signal_quality(state),
        incident_state=incident_state,
        llm_scores=[evaluator.score] if evaluator.requests else [],
        injection_attempts=port.injection_attempts,
        context_injections=len(port.injections),
        artifacts=str(paths.run_dir),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic Watchcat demonstration scenarios."
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
        help="Artifact root. Defaults to $VIBE_HOME/watchcat/demo-runs/REPORT_ID.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    if args.delay < 0:
        raise SystemExit("--delay must be non-negative")
    report_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:7]}"
    root = args.artifacts or VIBE_HOME.path / "watchcat" / "demo-runs" / report_id
    selected = (
        SCENARIOS.values() if args.scenario == "all" else [SCENARIOS[args.scenario]]
    )
    reports: list[DemoRunReport] = []
    for scenario in selected:
        paths, port, evaluator, _state = await _run_scenario(scenario, root)
        reports.append(
            await _print_trace(scenario, paths, port, evaluator, delay=args.delay)
        )
    report_path = save_demo_report(
        DemoReport(report_id=report_id, created_at=datetime.now(UTC), runs=reports)
    )
    print(f"\nPASS :: artifacts retained at {root}")
    print(f"REPORT :: {report_path}")
    print("VIEW   :: run Vibe, then /watchcat report")


if __name__ == "__main__":
    asyncio.run(_main())
