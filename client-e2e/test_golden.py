"""Assert Rust CLI output matches committed golden SVG snapshots.

A scenario with no golden gets one generated from the current capture and
passes with a warning, so a new scenario is covered from its first run. The
generated golden is uncommitted until reviewed.

On failure, live SVGs are saved under ``goldens/_live/<scenario>/`` next to
the committed goldens so a simple ``diff`` reveals the platform-specific
difference. CI artifact globs already cover ``goldens/**/*.svg`` so the live
SVGs are uploaded automatically — no separate store step needed.
"""

from __future__ import annotations

import difflib
import os
import warnings

from e2e.app_server.capture import capture_scenario
from e2e.app_server.config import CLIENTS, REPLAY_BIN
from e2e.app_server.golden import (
    golden_exists,
    golden_path,
    load_golden_requests,
    load_golden_svgs,
    normalize_snapshot,
    store_golden,
)
from e2e.app_server.parity import normalize_requests
from e2e.app_server.scenario import available_scenarios, load_scenario
from e2e.app_server.svg import to_svg
import pytest

_LIVE_DIR = golden_path("_live")


@pytest.mark.parametrize("name", available_scenarios())
def test_rust_matches_golden(name: str) -> None:
    """Rust capture must match the committed golden for SVGs and requests."""
    scenario = load_scenario(name)
    if scenario.skip_reason:
        pytest.skip(scenario.skip_reason)
    scenario.capture_startup = False
    if not os.path.exists(CLIENTS["rust"][0]) or not os.path.exists(REPLAY_BIN):
        pytest.skip("Rust binary missing (run `make build`)")

    captured = capture_scenario(CLIENTS["rust"], scenario)
    if not golden_exists(name):
        if not captured.snapshots and not captured.requests:
            pytest.fail(
                f"{name}: captured nothing, refusing to store an empty golden",
                pytrace=False,
            )
        store_golden(
            name, [normalize_snapshot(s) for s in captured.snapshots], captured.requests
        )
        warnings.warn(
            f"{name}: no golden, one was generated — review it before committing",
            stacklevel=2,
        )
        return

    golden_svgs = load_golden_svgs(name)
    golden_requests = load_golden_requests(name)

    if normalize_requests(captured.requests) != golden_requests:
        _save_live_svgs(name, captured.snapshots)
        pytest.fail(f"{name}: app-server request mismatch", pytrace=False)

    if len(captured.snapshots) != len(golden_svgs):
        _save_live_svgs(name, captured.snapshots)
        pytest.fail(
            f"{name}: {len(captured.snapshots)} snapshots vs "
            f"{len(golden_svgs)} goldens",
            pytrace=False,
        )

    for i, (snap, golden_svg) in enumerate(
        zip(captured.snapshots, golden_svgs, strict=True)
    ):
        norm = normalize_snapshot(snap)
        live = to_svg(norm, f"{name} [{snap.label}]")
        if live != golden_svg:
            _save_live_svgs(name, captured.snapshots)
            diff = next(
                difflib.unified_diff(
                    golden_svg.splitlines(keepends=True),
                    live.splitlines(keepends=True),
                    f"golden/{i}",
                    f"live/{i}",
                    n=1,
                ),
                None,
            )
            pytest.fail(
                f"{name}: snapshot {i} ({snap.label}) SVG mismatch"
                + (f"\n{diff}" if diff else ""),
                pytrace=False,
            )


def _save_live_svgs(name: str, snapshots: list) -> None:
    """Write live SVGs for a failing scenario under goldens/_live/<scenario>/.

    The committed goldens stay untouched. The live captures land next to them
    so a single ``diff goldens/<scenario>/snapshot_*.svg goldens/_live/<scenario>/``
    reveals the platform-specific difference.
    """
    out_dir = _LIVE_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, snap in enumerate(snapshots):
        slug = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in snap.label)
        norm = normalize_snapshot(snap)
        svg = to_svg(norm, f"{name} [{snap.label}]")
        (out_dir / f"snapshot_{i:02d}_{slug}.svg").write_text(svg)
