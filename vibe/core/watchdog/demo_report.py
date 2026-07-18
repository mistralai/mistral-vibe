from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from vibe.core.paths._vibe_home import VIBE_HOME


class DemoRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    title: str
    trigger: str
    flow: list[str]
    outcome: str
    signal_quality: str
    incident_state: str
    llm_scores: list[int]
    injection_attempts: int
    context_injections: int
    artifacts: str


class DemoReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_id: str
    created_at: datetime
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
    return DemoReport.model_validate_json(reports[-1].read_text(encoding="utf-8"))


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
