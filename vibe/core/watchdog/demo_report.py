from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from vibe.core.paths._vibe_home import VIBE_HOME


class DemoTraceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int
    event: str
    phase: str
    signal_quality: str
    incident_state: str


class DemoRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    title: str
    classification: str
    detector: str
    issue: str
    trigger: str
    evidence: list[str]
    flow: list[str]
    mitigation: list[str]
    outcome: str
    signal_quality: str
    incident_state: str
    llm_scores: list[int]
    injection_attempts: int
    context_injections: int
    artifacts: str
    trace: list[DemoTraceEntry]


class DemoReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    created_at: datetime
    status: str = "pass"
    runs: list[DemoRunReport]


def demo_report_dir() -> Path:
    return VIBE_HOME.path / "watchcat" / "demo-reports"


def save_demo_report(report: DemoReport) -> Path:
    directory = demo_report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.report_id}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def load_latest_demo_report() -> DemoReport | None:
    directory = demo_report_dir()
    if not directory.exists():
        return None
    reports = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        return None
    for path in reversed(reports):
        try:
            return DemoReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            continue
    return None


def render_demo_report(report: DemoReport) -> str:
    lines = [
        "## Watchcat Demo Report",
        "",
        "```text",
        f"WATCHCAT DEMO  {report.report_id}",
        f"RUNS {len(report.runs)}",
    ]
    for index, run in enumerate(report.runs, start=1):
        scores = ", ".join(str(score) for score in run.llm_scores) or "n/a"
        flow = " -> ".join(run.flow)
        lines.extend([
            "",
            f"[{index}/{len(report.runs)}] {run.name.upper()} :: {run.outcome.upper()}",
            f"+- case    : {run.title}",
            f"+- class   : {run.classification}",
            f"+- detector: {run.detector}",
            f"+- issue   : {run.issue}",
            f"+- trigger : {run.trigger}",
            f"+- flow    : {flow}",
            f"+- signal  : {run.signal_quality}",
            f"+- LLM     : {scores}",
            f"+- inject  : {run.context_injections}/{run.injection_attempts} successful",
            f"+- incident: {run.incident_state}",
            f"`- artifact: {run.artifacts}",
        ])
    lines.extend(["```", "", "Re-run: `uv run python scripts/watchcat_demo.py`"])
    return "\n".join(lines)


def render_demo_summary(report: DemoReport) -> str:
    counts: dict[str, int] = {}
    for run in report.runs:
        counts[run.classification] = counts.get(run.classification, 0) + 1
    lines = [
        f"WATCHCAT DEMO :: {report.report_id}",
        f"status: {report.status.upper()}",
        f"runs: {len(report.runs)}",
        "",
        "CLASSIFICATION      RUNS",
        "------------------  ----",
    ]
    for classification in ("protected", "mitigated", "blocked", "degraded"):
        lines.append(f"{classification:<18}  {counts.get(classification, 0):>4}")
    lines.extend(["", "RUN                  CLASS       RESULT"])
    lines.append("-------------------  ----------  --------------------")
    for run in report.runs:
        lines.append(f"{run.name[:19]:<19}  {run.classification:<10}  {run.outcome}")
    return "\n".join(lines)


def render_demo_runs(runs: list[DemoRunReport]) -> str:
    if not runs:
        return "No runs in this classification."
    sections: list[str] = []
    for run in runs:
        scores = ", ".join(str(score) for score in run.llm_scores) or "n/a"
        evidence = " | ".join(run.evidence) or "none"
        mitigation = " -> ".join(run.mitigation) or "none"
        sections.extend([
            f"{run.name.upper()} :: {run.outcome.upper()}",
            f"+- issue      : {run.issue}",
            f"+- detector   : {run.detector}",
            f"+- trigger    : {run.trigger}",
            f"+- evidence   : {evidence}",
            f"+- mitigation : {mitigation}",
            f"+- signal/LLM : {run.signal_quality} / {scores}",
            f"+- injection  : {run.context_injections}/{run.injection_attempts} successful",
            f"`- artifact   : {run.artifacts}",
            "",
            "SEQ  EVENT                    PHASE          SIGNAL    INCIDENT",
            "---  -----------------------  -------------  --------  ----------",
        ])
        for entry in run.trace:
            sections.append(
                f"{entry.sequence:>3}  {entry.event:<23}  {entry.phase:<13}  "
                f"{entry.signal_quality:<8}  {entry.incident_state}"
            )
        sections.extend(["", "=" * 72, ""])
    return "\n".join(sections).rstrip()
