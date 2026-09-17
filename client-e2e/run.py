"""Run one committed scenario against every Vibe client and write a report."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from e2e.app_server.capture import capture_scenario
from e2e.app_server.config import CLIENTS
from e2e.app_server.report import write_report
from e2e.app_server.scenario import load_scenario


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "startup/startup"
    scenario = load_scenario(name)
    event_count = sum(len(batch) for batch in scenario.event_batches())
    print(f"running scenario {scenario.name!r} with {event_count} events")
    captures = {}
    for client, command in CLIENTS.items():
        captured = capture_scenario(command, scenario)
        captures[client] = captured.snapshots
        print(f"[{client}] captured {len(captured.snapshots)} snapshot(s)")
        if captured.snapshots[-1].clipboard is not None:
            print(f"[{client}] clipboard: {captured.snapshots[-1].clipboard!r}")
    print(f"wrote {write_report(name, captures)}")


if __name__ == "__main__":
    main()
