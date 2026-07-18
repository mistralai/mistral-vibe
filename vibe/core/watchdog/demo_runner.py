from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
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
from vibe.core.watchdog.demo_report import (
    DemoReport,
    DemoRunReport,
    DemoTraceEntry,
    save_demo_report,
)
from vibe.core.watchdog.detectors import RepeatedCallDetector
from vibe.core.watchdog.event_adapter import PendingWatchdogEvent
from vibe.core.watchdog.incident import IncidentEngine


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    title: str
    intent: str
    classification: str
    issue: str
    trigger: str
    repeats: int
    last_result: str
    last_repository: str
    observer_anomaly: bool
    evaluator_score: int | None
    evaluator_failure: bool
    fail_injection: bool
    next_action: str | None
    expected_incident: IncidentState | None
    expected_observer: ObserverState
    expected_attempts: int
    expected_injections: int
    mitigation: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DemoProgress:
    current: int
    total: int
    scenario: str
    state: str
    classification: str | None = None


@dataclass(frozen=True, slots=True)
class DemoExecution:
    report: DemoReport
    report_path: Path
    artifacts: Path


SCENARIOS = {
    **{
        f"threshold-{repeats}": Scenario(
            name=f"threshold-{repeats}",
            title=f"{repeats} repeated failure{'s' if repeats != 1 else ''} -> no incident",
            intent="Verify every non-triggering iteration below the threshold.",
            classification="protected",
            issue="repeated_call_below_threshold",
            trigger=f"none; {repeats}/4 exact repeated calls",
            repeats=repeats,
            last_result="1 failed",
            last_repository="unchanged",
            observer_anomaly=False,
            evaluator_score=None,
            evaluator_failure=False,
            fail_injection=False,
            next_action=None,
            expected_incident=None,
            expected_observer=ObserverState.TRUSTED,
            expected_attempts=0,
            expected_injections=0,
            mitigation=("observe", "hold below threshold"),
        )
        for repeats in range(1, 4)
    },
    "result-changed": Scenario(
        name="result-changed",
        title="Fourth call result changed -> suspected incident closed",
        intent="Reject a false positive when the result fingerprint changes.",
        classification="protected",
        issue="repeat_with_changed_result",
        trigger="4 exact calls; fourth result differs",
        repeats=4,
        last_result="0 passed",
        last_repository="unchanged",
        observer_anomaly=False,
        evaluator_score=None,
        evaluator_failure=False,
        fail_injection=False,
        next_action=None,
        expected_incident=IncidentState.CLOSED,
        expected_observer=ObserverState.TRUSTED,
        expected_attempts=0,
        expected_injections=0,
        mitigation=("suspect", "compare result fingerprint", "close false positive"),
    ),
    "repository-changed": Scenario(
        name="repository-changed",
        title="Fourth call changed repository -> suspected incident closed",
        intent="Reject a false positive when repository progress is visible.",
        classification="protected",
        issue="repeat_with_repository_progress",
        trigger="4 exact calls; fourth repository fingerprint differs",
        repeats=4,
        last_result="1 failed",
        last_repository="changed",
        observer_anomaly=False,
        evaluator_score=None,
        evaluator_failure=False,
        fail_injection=False,
        next_action=None,
        expected_incident=IncidentState.CLOSED,
        expected_observer=ObserverState.TRUSTED,
        expected_attempts=0,
        expected_injections=0,
        mitigation=(
            "suspect",
            "compare repository fingerprint",
            "close false positive",
        ),
    ),
    "recovery": Scenario(
        name="recovery",
        title="Repeated failure -> recovery -> verified progress",
        intent="Show one bounded context injection followed by a changed action.",
        classification="mitigated",
        issue="confirmed_repeated_failure",
        trigger="4 exact calls + same result + unchanged repository",
        repeats=4,
        last_result="1 failed",
        last_repository="unchanged",
        observer_anomaly=False,
        evaluator_score=None,
        evaluator_failure=False,
        fail_injection=False,
        next_action="changed",
        expected_incident=IncidentState.CLOSED,
        expected_observer=ObserverState.TRUSTED,
        expected_attempts=1,
        expected_injections=1,
        mitigation=(
            "inject bounded context",
            "observe changed action",
            "verify",
            "close",
        ),
    ),
    "signal": Scenario(
        name="signal",
        title="Observer anomaly -> LLM score -> deterministic recovery",
        intent="Show degraded signal quality using a score without selecting an action.",
        classification="mitigated",
        issue="confirmed_repeat_with_degraded_signal",
        trigger="4 exact failures + observer anomaly + LLM score 92/100",
        repeats=4,
        last_result="1 failed",
        last_repository="unchanged",
        observer_anomaly=True,
        evaluator_score=92,
        evaluator_failure=False,
        fail_injection=False,
        next_action="changed",
        expected_incident=IncidentState.CLOSED,
        expected_observer=ObserverState.TILT,
        expected_attempts=1,
        expected_injections=1,
        mitigation=("score signal confidence", "authorize", "inject context", "verify"),
    ),
    "signal-blocked": Scenario(
        name="signal-blocked",
        title="Observer anomaly -> low LLM score -> intervention blocked",
        intent="Show that deterministic policy rejects recovery below threshold.",
        classification="blocked",
        issue="low_signal_confidence",
        trigger="4 exact failures + observer anomaly + LLM score 40/100",
        repeats=4,
        last_result="1 failed",
        last_repository="unchanged",
        observer_anomaly=True,
        evaluator_score=40,
        evaluator_failure=False,
        fail_injection=False,
        next_action=None,
        expected_incident=IncidentState.CONFIRMED,
        expected_observer=ObserverState.TILT,
        expected_attempts=0,
        expected_injections=0,
        mitigation=("score signal confidence", "deny below 80", "preserve incident"),
    ),
    "signal-eval-failed": Scenario(
        name="signal-eval-failed",
        title="Signal evaluator failure -> intervention blocked",
        intent="Fail safe when the harness cannot score observation confidence.",
        classification="blocked",
        issue="signal_evaluator_failure",
        trigger="4 exact failures + observer anomaly + evaluator error",
        repeats=4,
        last_result="1 failed",
        last_repository="unchanged",
        observer_anomaly=True,
        evaluator_score=None,
        evaluator_failure=True,
        fail_injection=False,
        next_action=None,
        expected_incident=IncidentState.CONFIRMED,
        expected_observer=ObserverState.TILT,
        expected_attempts=0,
        expected_injections=0,
        mitigation=(
            "request confidence score",
            "record evaluator failure",
            "block action",
        ),
    ),
    "verification-failed": Scenario(
        name="verification-failed",
        title="Recovery followed by repeated action -> verification remains open",
        intent="Do not claim recovery when the next action is unchanged.",
        classification="blocked",
        issue="mitigation_not_verified",
        trigger="4 exact failures + same next action after injection",
        repeats=4,
        last_result="1 failed",
        last_repository="unchanged",
        observer_anomaly=False,
        evaluator_score=None,
        evaluator_failure=False,
        fail_injection=False,
        next_action="same",
        expected_incident=IncidentState.VERIFYING,
        expected_observer=ObserverState.TRUSTED,
        expected_attempts=2,
        expected_injections=2,
        mitigation=(
            "inject bounded context",
            "observe unchanged action",
            "retry bounded recovery",
            "keep verifying",
        ),
    ),
    "degraded": Scenario(
        name="degraded",
        title="Recovery adapter failure -> degraded state",
        intent="Show that failed intervention is recorded instead of reported as success.",
        classification="degraded",
        issue="recovery_adapter_failure",
        trigger="4 exact failures + recovery adapter delivery failure",
        repeats=4,
        last_result="1 failed",
        last_repository="unchanged",
        observer_anomaly=False,
        evaluator_score=None,
        evaluator_failure=False,
        fail_injection=True,
        next_action=None,
        expected_incident=IncidentState.DEGRADED,
        expected_observer=ObserverState.TRUSTED,
        expected_attempts=1,
        expected_injections=0,
        mitigation=(
            "attempt context injection",
            "record delivery failure",
            "mark degraded",
        ),
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
    def __init__(self, score: int, *, fail: bool = False) -> None:
        self.score = score
        self.fail = fail
        self.requests: list[TiltEvaluationRequest] = []

    async def evaluate(self, request: TiltEvaluationRequest) -> TiltEvaluation:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("simulated evaluator failure")
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


def _enqueue_failed_call(
    supervisor: ObserveOnlySupervisor,
    call_id: str,
    *,
    result: str = "1 failed",
    repository: str = "unchanged",
) -> None:
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
            {"result": result, "repository_fingerprint": repository},
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


def _enqueue_same_action(supervisor: ObserveOnlySupervisor) -> None:
    _enqueue_failed_call(supervisor, "repeat-verification")


def _signal_quality(state: RunState) -> str:
    return "healthy" if state.observer_state == ObserverState.TRUSTED else "degraded"


async def _run_scenario(
    scenario: Scenario, root: Path
) -> tuple[WatchdogPaths, DemoRecoveryPort, DemoTiltEvaluator, RunState]:
    run_id = f"demo-{scenario.name}"
    paths = WatchdogPaths.for_run(run_id, root=root)
    store = WatchdogStore(paths)
    port = DemoRecoveryPort(fail_injection=scenario.fail_injection)
    evaluator = DemoTiltEvaluator(
        score=scenario.evaluator_score or 0, fail=scenario.evaluator_failure
    )
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
            evaluator
            if scenario.evaluator_score is not None or scenario.evaluator_failure
            else None
        ),
    )
    await supervisor.start()
    if scenario.observer_anomaly:
        supervisor._queue.put_nowait(
            PendingWatchdogEvent(
                kind=EventKind.OBSERVER_ANOMALY,
                observed_at_monotonic=time.monotonic(),
                payload={"reason": "demo_sequence_gap"},
                critical=True,
            )
        )
    for attempt in range(1, scenario.repeats + 1):
        is_last = attempt == scenario.repeats
        _enqueue_failed_call(
            supervisor,
            f"repeat-{attempt}",
            result=scenario.last_result if is_last else "1 failed",
            repository=scenario.last_repository if is_last else "unchanged",
        )
    if scenario.next_action == "changed":
        _enqueue_changed_action(supervisor)
    elif scenario.next_action == "same":
        _enqueue_same_action(supervisor)
    try:
        await supervisor.finish()
    except RuntimeError as error:
        if not scenario.fail_injection or str(error) != "simulated delivery failure":
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
    expected = (
        scenario.expected_incident,
        scenario.expected_observer,
        scenario.expected_injections,
    )
    actual = (
        incident.state if incident is not None else None,
        state.observer_state,
        len(port.injections),
    )
    if actual != expected:
        raise RuntimeError(f"{scenario.name}: expected {expected}, got {actual}")
    if port.injection_attempts != scenario.expected_attempts:
        raise RuntimeError(
            f"{scenario.name}: expected {scenario.expected_attempts} injection attempts, "
            f"got {port.injection_attempts}"
        )
    expected_evaluations = (
        1 if scenario.evaluator_score is not None or scenario.evaluator_failure else 0
    )
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
    verbose: bool,
) -> DemoRunReport:
    events = await WatchdogStore(paths).load_events()
    state = RunState.new(run_id=events[0].run_id, session_id=events[0].session_id)
    output = print if verbose else lambda *_args, **_kwargs: None
    output(f"\nWATCHCAT DEMO :: {scenario.name.upper()}")
    output(f"+- {scenario.title}")
    output(f"`- {scenario.intent}\n")
    output("SEQ  EVENT                    PHASE          SIGNAL    INCIDENT")
    output("---  -----------------------  -------------  --------  ----------")
    for event in events:
        state = apply_event(state, event)
        incident = state.incident.state.value if state.incident else "-"
        signal_quality = _signal_quality(state)
        output(
            f"{event.sequence:>3}  {event.kind.value:<23}  "
            f"{state.phase.value:<13}  {signal_quality:<8}  {incident}"
        )
        if verbose and delay:
            await asyncio.sleep(delay)
    output("\nRESULT")
    scores = (
        str([scenario.evaluator_score])
        if evaluator.requests and scenario.evaluator_score is not None
        else "[]"
    )
    output(f"+- LLM eval scores    : {scores}")
    output(
        f"+- context injections : {len(port.injections)}/{port.injection_attempts} "
        "successful"
    )
    output(f"+- signal quality     : {_signal_quality(state)}")
    output(
        f"+- incident state     : {state.incident.state.value if state.incident else '-'}"
    )
    output(f"`- artifacts          : {paths.run_dir}")
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
        EventKind.INCIDENT_CLOSED: "close false positive",
        EventKind.TILT_EVALUATED: "LLM score",
        EventKind.TILT_EVALUATION_FAILED: "LLM score failed",
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
    if scenario.classification == "protected":
        flow.extend(["no trigger", "finish"])
        outcome = "no intervention"
    elif scenario.classification == "mitigated":
        outcome = "recovered"
    elif scenario.classification == "degraded":
        outcome = "failure recorded"
    elif state.incident is not None and state.incident.state == IncidentState.VERIFYING:
        flow.append("verification open")
        outcome = "verification open"
    else:
        flow.append("blocked")
        outcome = "intervention blocked"
    evidence: list[str] = []
    if state.incident is not None:
        for item in state.incident.evidence:
            for key in ("repeat_count", "result", "repository"):
                if (value := item.facts.get(key)) is not None:
                    rendered = f"{key}={value}"
                    if rendered not in evidence:
                        evidence.append(rendered)
    trace: list[DemoTraceEntry] = []
    trace_state = RunState.new(run_id=events[0].run_id, session_id=events[0].session_id)
    for event in events:
        trace_state = apply_event(trace_state, event)
        trace.append(
            DemoTraceEntry(
                sequence=event.sequence,
                event=event.kind.value,
                phase=trace_state.phase.value,
                signal_quality=_signal_quality(trace_state),
                incident_state=(
                    trace_state.incident.state.value if trace_state.incident else "-"
                ),
            )
        )
    return DemoRunReport(
        name=scenario.name,
        title=scenario.title,
        classification=scenario.classification,
        detector="repeated_call",
        issue=scenario.issue,
        trigger=scenario.trigger,
        evidence=evidence,
        flow=flow,
        mitigation=list(scenario.mitigation),
        outcome=outcome,
        signal_quality=_signal_quality(state),
        incident_state=incident_state,
        llm_scores=(
            [scenario.evaluator_score]
            if evaluator.requests and scenario.evaluator_score is not None
            else []
        ),
        injection_attempts=port.injection_attempts,
        context_injections=len(port.injections),
        artifacts=str(paths.run_dir),
        trace=trace,
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
        default=0.02,
        help="Seconds between trace rows (default: 0.02; use 0 for instant).",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        help="Artifact root. Defaults to $VIBE_HOME/watchcat/demo-runs/REPORT_ID.",
    )
    return parser.parse_args()


async def run_demo(
    scenario: str = "all",
    *,
    delay: float = 0,
    artifacts: Path | None = None,
    verbose: bool = False,
    progress: Callable[[DemoProgress], Awaitable[None]] | None = None,
) -> DemoExecution:
    if scenario != "all" and scenario not in SCENARIOS:
        raise ValueError(f"unknown Watchcat demo scenario: {scenario}")
    if delay < 0:
        raise ValueError("delay must be non-negative")
    report_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:7]}"
    root = artifacts or VIBE_HOME.path / "watchcat" / "demo-runs" / report_id
    selected = list(SCENARIOS.values()) if scenario == "all" else [SCENARIOS[scenario]]
    reports: list[DemoRunReport] = []
    for index, selected_scenario in enumerate(selected, start=1):
        if progress is not None:
            await progress(
                DemoProgress(index, len(selected), selected_scenario.name, "running")
            )
        paths, port, evaluator, _state = await _run_scenario(selected_scenario, root)
        run_report = await _print_trace(
            selected_scenario, paths, port, evaluator, delay=delay, verbose=verbose
        )
        reports.append(run_report)
        if progress is not None:
            await progress(
                DemoProgress(
                    index,
                    len(selected),
                    selected_scenario.name,
                    "completed",
                    run_report.classification,
                )
            )
    report = DemoReport(report_id=report_id, created_at=datetime.now(UTC), runs=reports)
    report_path = save_demo_report(report)
    return DemoExecution(report=report, report_path=report_path, artifacts=root)


async def _main() -> None:
    args = _parse_args()
    if args.delay < 0:
        raise SystemExit("--delay must be non-negative")
    execution = await run_demo(
        args.scenario, delay=args.delay, artifacts=args.artifacts, verbose=True
    )
    print(f"\nPASS :: artifacts retained at {execution.artifacts}")
    print(f"REPORT :: {execution.report_path}")
    print("VIEW   :: run Vibe, then /watchcat report")


def cli_main() -> None:
    asyncio.run(_main())
