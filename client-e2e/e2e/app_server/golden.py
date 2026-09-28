"""Store and load Rust golden snapshots (SVG + requests JSON) for e2e scenarios."""

from __future__ import annotations

import json
from pathlib import Path

from e2e.app_server.config import GOLDEN_DIR
from e2e.app_server.parity import _normalized_cells, normalize_requests
from e2e.app_server.scenario import Request
from e2e.app_server.svg import to_svg
from e2e.pty.screen import Snapshot


def normalize_snapshot(snap: Snapshot) -> Snapshot:
    """Mask volatile cells (PID digits, spinner, scrollbar) for stable SVG comparison."""
    cells = _normalized_cells(snap)
    return Snapshot(label=snap.label, cells=tuple(tuple(row) for row in cells))


def golden_path(scenario: str) -> Path:
    """Return the golden directory for a scenario."""
    return GOLDEN_DIR / scenario


def golden_exists(scenario: str) -> bool:
    return (golden_path(scenario) / "requests.json").exists()


def store_golden(
    scenario: str, snapshots: list[Snapshot], requests: list[Request]
) -> Path:
    """Write SVG per snapshot and normalized requests as golden files."""
    directory = golden_path(scenario)
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("snapshot_*.svg"):
        old.unlink()
    for i, snap in enumerate(snapshots):
        slug = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in snap.label)
        svg = to_svg(snap, f"{scenario} [{snap.label}]")
        (directory / f"snapshot_{i:02d}_{slug}.svg").write_text(svg)
    (directory / "requests.json").write_text(
        json.dumps(
            [
                {"method": r.method, "params": dict(r.params)}
                for r in normalize_requests(requests)
            ],
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
    return directory


def load_golden_svgs(scenario: str) -> list[str]:
    """Load committed golden SVG strings for a scenario, sorted by index."""
    directory = golden_path(scenario)
    return [p.read_text() for p in sorted(directory.glob("snapshot_*.svg"))]


def load_golden_requests(scenario: str) -> list[Request]:
    """Load committed golden requests for a scenario."""
    return [
        Request(method=r["method"], params=r["params"])
        for r in json.loads((golden_path(scenario) / "requests.json").read_text())
    ]
