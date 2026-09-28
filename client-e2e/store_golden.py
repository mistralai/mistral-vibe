"""Capture Rust CLI output for scenarios and store it as committed goldens.

Run for a specific scenario:
    uv run --no-project --with "pyte==0.8.2" --with "rich==15.0.0" python client-e2e/store_golden.py conversation/say_hi

Run for all scenarios (or use `make store_golden`):
    uv run --no-project --with "pyte==0.8.2" --with "rich==15.0.0" python client-e2e/store_golden.py --all

Goldens are written to client-e2e/goldens/<scenario>/{snapshot_*.svg,requests.json}.
Regenerate when the Rust CLI intentionally changes rendering; review the diff.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from e2e.app_server.capture import capture_scenario
from e2e.app_server.config import CLIENTS, REPLAY_BIN
from e2e.app_server.golden import normalize_snapshot, store_golden
from e2e.app_server.scenario import available_scenarios, load_scenario


def main() -> None:
    args = sys.argv[1:]
    if not args or args == ["--all"]:
        names = available_scenarios()
    else:
        names = args

    if not os.path.exists(CLIENTS["rust"][0]):
        sys.exit(f"Rust binary missing: {CLIENTS['rust'][0]} (run `make build`)")
    if not os.path.exists(REPLAY_BIN):
        sys.exit(f"Replay binary missing: {REPLAY_BIN} (run `make build`)")

    for name in names:
        scenario = load_scenario(name)
        if scenario.skip_reason:
            print(f"  skip {name}: {scenario.skip_reason}")
            continue
        scenario.capture_startup = False
        try:
            captured = capture_scenario(CLIENTS["rust"], scenario)
        except (TimeoutError, OSError) as exc:
            print(f"  FAIL {name}: {exc}")
            continue
        normalized = [normalize_snapshot(s) for s in captured.snapshots]
        path = store_golden(name, normalized, captured.requests)
        print(
            f"  stored {name}: {len(captured.snapshots)} snapshot(s), "
            f"{len(captured.requests)} request(s) -> {path}"
        )


if __name__ == "__main__":
    main()
