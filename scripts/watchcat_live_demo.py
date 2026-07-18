#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from uuid import uuid4

from vibe.core.watchdog.demo_headless import run_headless_demo
from vibe.core.watchdog.demo_report import (
    DemoReport,
    render_demo_runs,
    save_demo_report,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the real Watchcat CLI canary.")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Seconds before each fixture response (default: 0.3; use 0 for instant).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.delay < 0:
        raise SystemExit("--delay must be non-negative")
    print("WATCHCAT LIVE DEMO")
    print("CLI -> programmatic runner -> AgentLoop -> tools -> Watchcat -> recovery")

    def progress(current: int, total: int, label: str) -> None:
        branch = "`--" if current == total else "+--"
        print(f"{branch} [{current}/{total}] {label}", flush=True)

    run = run_headless_demo(response_delay=args.delay, progress=progress)
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
