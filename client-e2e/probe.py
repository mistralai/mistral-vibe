"""Probe an ad-hoc scenario for client parity and optionally promote it."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from e2e.app_server.capture import capture_scenario
from e2e.app_server.config import CLIENTS, SCENARIO_DIR
from e2e.app_server.parity import captures_match
from e2e.app_server.report import report_hyperlink, write_report
from e2e.app_server.scenario import load_scenario_path


def _promote(source: str, name: str) -> Path:
    destination = SCENARIO_DIR / f"{name}.py"
    if destination.exists():
        sys.exit(f"refusing to overwrite existing scenario: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="ad-hoc Rust-vs-Python parity probe")
    parser.add_argument("scenario", help="path to a throwaway scenario .py")
    parser.add_argument(
        "--promote", metavar="NAME", help="copy a divergent probe to scenarios/NAME.py"
    )
    args = parser.parse_args()

    for command in CLIENTS.values():
        if not os.path.exists(command[0]):
            sys.exit(f"CLI missing: {command[0]} (run `make build` / `uv sync`)")

    scenario = load_scenario_path(args.scenario)
    print(
        f"probing {scenario.name!r} with {len(scenario.event_batches())} event batches"
    )
    captured = {
        client: capture_scenario(command, scenario)
        for client, command in CLIENTS.items()
    }
    captures = {client: result.snapshots for client, result in captured.items()}
    matched = captures_match(captures)
    report = write_report(scenario.name, captures)
    print(f"result: {'MATCH' if matched else 'DIVERGES'}")
    print(f"report: {report_hyperlink(report)}")

    if args.promote:
        if matched:
            sys.exit("not promoting: clients render identically (nothing to pin)")
        print(f"promoted -> {_promote(args.scenario, args.promote)}")


if __name__ == "__main__":
    main()
