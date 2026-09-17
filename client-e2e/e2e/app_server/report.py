"""Write Vibe parity reports for terminal snapshots and RPC requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from difflib import unified_diff
import json
import os
from pathlib import Path

from e2e.app_server.config import REPORT_DIR
from e2e.app_server.parity import (
    attr_cells,
    canon,
    color_cells,
    hyperlink_cells,
    normalize,
    normalize_requests,
    text_rows,
)
from e2e.app_server.scenario import Request
from e2e.pty.screen import Cell, Snapshot

_REPORTS_ENABLED = os.environ.get("VIBE_E2E_REPORTS", "1") != "0"


def _maybe_write(path: Path, content: str) -> str:
    """Write content to path unless reports are disabled (e.g. CI)."""
    if not _REPORTS_ENABLED:
        return "(reports disabled)"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return os.fspath(path)


def render(snapshot: Snapshot) -> str:
    """Render a normalized snapshot as numbered rows."""
    return "\n".join(
        f"{index:2d}|{row}" for index, row in enumerate(normalize(snapshot))
    )


def write_report(scenario: str, captures: Mapping[str, Sequence[Snapshot]]) -> str:
    """Write cross-client differences and captures for one scenario."""
    path = REPORT_DIR / f"{scenario}.txt"
    capture_items = list(captures.items())
    reference = capture_items[0]
    blocks = [_diff_block(scenario, reference, other) for other in capture_items[1:]]
    for client, snapshots in capture_items:
        blocks.extend(
            f"===== {scenario}:{client}:{snapshot.label} =====\n{render(snapshot)}"
            for snapshot in snapshots
        )
    return _maybe_write(path, "\n\n".join(blocks) + "\n")


def write_request_report(
    scenario: str, captures: Mapping[str, Sequence[Request]]
) -> str:
    """Write a readable semantic client-to-server RPC diff for one scenario."""
    path = REPORT_DIR / f"{scenario}.requests.txt"
    rendered = {
        client: _render_requests(normalize_requests(requests))
        for client, requests in captures.items()
    }
    items = list(rendered.items())
    reference_client, reference = items[0]
    blocks = [
        _request_diff(reference_client, reference, client, requests)
        for client, requests in items[1:]
    ]
    blocks.extend(
        f"===== {scenario}:{client}:requests =====\n{requests}"
        for client, requests in items
    )
    return _maybe_write(path, "\n\n".join(blocks) + "\n")


def report_hyperlink(path: str) -> str:
    """Build a styled OSC 8 link to a report."""
    url = f"vscode://file/{os.path.abspath(path)}"
    label = f"\x1b[4;96m{url}\x1b[0m"
    return f"\x1b]8;;{url}\x1b\\{label}\x1b]8;;\x1b\\"


def _render_requests(requests: Sequence[Request]) -> str:
    return json.dumps(
        [asdict(request) for request in requests], indent=2, sort_keys=True
    )


def _request_diff(reference: str, left: str, other: str, right: str) -> str:
    diff = "\n".join(
        unified_diff(
            left.splitlines(),
            right.splitlines(),
            fromfile=reference,
            tofile=other,
            lineterm="",
        )
    )
    return f"--- request diff: {reference} vs {other} ---\n{diff or 'identical'}"


def _diff_block(
    scenario: str,
    reference: tuple[str, Sequence[Snapshot]],
    other: tuple[str, Sequence[Snapshot]],
) -> str:
    reference_client, reference_snapshots = reference
    other_client, other_snapshots = other
    lines = [f"--- diff: {reference_client} vs {other_client} ---"]
    pairs = zip(reference_snapshots, other_snapshots, strict=True)
    for reference_snapshot, other_snapshot in pairs:
        lines.append(f"[{scenario}:{reference_snapshot.label}]")
        args = (reference_client, reference_snapshot, other_client, other_snapshot)
        lines.append(_text_diff(*args))
        lines.append(_color_diff(*args))
        lines.append(_attr_diff(*args))
        lines.append(_hyperlink_diff(*args))
    return "\n".join(lines)


def _text_diff(
    reference: str, reference_snapshot: Snapshot, other: str, other_snapshot: Snapshot
) -> str:
    differences = text_rows(reference_snapshot, other_snapshot)
    if not differences:
        return "text: identical"
    lines: list[str] = []
    for index, reference_row, other_row in differences:
        lines.append(f"  {index:2d}| {reference:<7}: {reference_row}")
        lines.append(f"      {other:<7}: {other_row}")
    return "text:\n" + "\n".join(lines)


def _color_diff(
    reference: str, reference_snapshot: Snapshot, other: str, other_snapshot: Snapshot
) -> str:
    differences = color_cells(reference_snapshot, other_snapshot)
    if not differences:
        return "colors: identical"
    lines = [
        f"  {y:2d}:{x:3d} {reference_cell.character!r}: "
        f"{reference} fg={canon(reference_cell.foreground)} "
        f"bg={canon(reference_cell.background)}  "
        f"{other} fg={canon(other_cell.foreground)} bg={canon(other_cell.background)}"
        for y, x, reference_cell, other_cell in differences
    ]
    return "colors:\n" + "\n".join(lines)


def _attr_diff(
    reference: str, reference_snapshot: Snapshot, other: str, other_snapshot: Snapshot
) -> str:
    differences = attr_cells(reference_snapshot, other_snapshot)
    if not differences:
        return "attrs: identical"
    lines = [
        f"  {y:2d}:{x:3d} {reference_cell.character!r}: "
        f"{reference} {_attrs(reference_cell)}  {other} {_attrs(other_cell)}"
        for y, x, reference_cell, other_cell in differences
    ]
    return "attrs:\n" + "\n".join(lines)


def _hyperlink_diff(
    reference: str, reference_snapshot: Snapshot, other: str, other_snapshot: Snapshot
) -> str:
    differences = hyperlink_cells(reference_snapshot, other_snapshot)
    if not differences:
        return "hyperlinks: identical"
    lines = [
        f"  {y:2d}:{x:3d} {reference_cell.character!r}: "
        f"{reference} {reference_cell.hyperlink or '-'}  "
        f"{other} {other_cell.hyperlink or '-'}"
        for y, x, reference_cell, other_cell in differences
    ]
    return "hyperlinks:\n" + "\n".join(lines)


def _attrs(cell: Cell) -> str:
    return ", ".join(sorted(attr.value for attr in cell.attrs)) or "-"
