#!/usr/bin/env python3
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from vibe.core.watchdog.demo_headless import run_headless_demo
from vibe.core.watchdog.demo_report import (
    DemoReport,
    render_demo_runs,
    save_demo_report,
)


def main() -> None:
    print("WATCHCAT LIVE DEMO")
    print("CLI -> programmatic runner -> AgentLoop -> tools -> Watchcat -> recovery")
    run = run_headless_demo()
    report = DemoReport(
        report_id=f"headless-{uuid4().hex[:10]}",
        created_at=datetime.now(UTC),
        runs=[run],
    )
    report_path = save_demo_report(report)
    print()
    print(render_demo_runs([run]))
    print()
    print(f"PASS :: report={report_path}")


if __name__ == "__main__":
    main()
