"""Compare Vibe terminal snapshots after masking known volatile UI cells."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import re
from typing import Any, NamedTuple

from e2e.app_server.config import VIBE_DIR
from e2e.app_server.scenario import Request
from e2e.pty.screen import Cell, Snapshot

_BRAILLE = re.compile(r"[\u2800-\u28ff]")
# The pulse spinner alternates filled/hollow squares on an unsynced 100 ms clock,
# so canonicalize the hollow frame to the filled one before comparing.
_PULSE_HOLLOW, _PULSE_FILLED = "\u25a1", "\u25a0"
_PID = re.compile(r"(?<=PID )\d+")
_FOOTER_PID = re.compile(r"PID \d+\](?: +(?=\d))?")
# The loading hint's turn-elapsed counter (`Generating… (0s …`) is wall-clock,
# so a slower host crosses a second boundary and renders `1s` where a fast one
# renders `0s`. Mask its digits (like PID) so the golden is host-independent.
_ELAPSED = re.compile(r"(?<=… \()(?:\d+h)?(?:\d+m)?\d+s(?= )")
_SCROLLBAR_THUMB = re.compile(r"[\u2581-\u2587]")
_VIBE_DIR = str(VIBE_DIR)
_VIBE_DIR_PLACEHOLDER = "<vibe-dir>"
_ANSI_HEX = {
    "black": "000000",
    "red": "cd0000",
    "green": "00cd00",
    "brown": "cdcd00",
    "blue": "0000ee",
    "magenta": "cd00cd",
    "cyan": "00cdcd",
    "white": "e5e5e5",
    "brightblack": "7f7f7f",
    "brightred": "ff0000",
    "brightgreen": "00ff00",
    "brightbrown": "ffff00",
    "brightblue": "5c5cff",
    "brightmagenta": "ff00ff",
    "brightcyan": "00ffff",
    "brightwhite": "ffffff",
}
# Custom truecolor hex values the Rust CLI emits verbatim, but which the
# Python CLI (Textual) hands to pyte, which snaps them to the nearest ANSI
# 256-color cube value. Mapping the Rust value to pyte's rounded value makes
# `canon()` treat both sides as equal. Each pair was confirmed against the
# client-e2e parity diff for the default (gruvbox) theme + its yellow spinner.
_TRUECOLOR_ANSI_EQUIVALENTS = {
    # Loading-spinner yellow (LOADING_GRADIENT[0]).
    "ffd800": "ffd700",
    # gruvbox: background / foreground / primary / secondary / accents.
    "282828": "262626",
    "fbf1c7": "ffffd7",
    "85a598": "87af87",
    "a89a85": "af8787",
    "b7bb26": "afaf00",
    "d0d26f": "d7d75f",
    "fa4934": "ff5f5f",
    "fc8679": "ff8787",
    "fd8019": "ff8700",
    # Neutral grays the themes interpolate, snapped to ANSI 8-bit gray steps.
    "a9a9a9": "a8a8a8",
    "b5b5b5": "b2b2b2",
    "babab5": "b2b2b2",
    "bdb6b6": "b2b2b2",
    # Selection backgrounds the Rust theme composites; pyte rounds the blend.
    "363627": "5f5f00",
    "3d2b29": "5f0000",
    # ORANGE constant (MistralColors.ORANGE) on the original single-entry map.
    "ff8205": "ff8700",
}


def canon(color: str) -> str:
    """Canonicalize a pyte color name to its ANSI hex value."""
    value = _ANSI_HEX.get(color.lower(), color.lower())
    return _TRUECOLOR_ANSI_EQUIVALENTS.get(value, value)


class _Normalized(NamedTuple):
    """One snapshot's masked cells and rows, derived once and shared by comparators."""

    cells: list[list[Cell]]
    rows: list[str]


def _normalized(snapshot: Snapshot) -> _Normalized:
    cells = _normalized_cells(snapshot)
    rows: list[str] = []
    for index, row in enumerate(cells):
        text = "".join(cell.character for cell in row)
        for match in reversed(list(_FOOTER_PID.finditer(snapshot.rows[index]))):
            text = f"{text[: match.start()]}PID #] {text[match.end() :]}"
        rows.append(text.rstrip())
    return _Normalized(cells, rows)


def normalize(snapshot: Snapshot) -> list[str]:
    """Flatten normalized Vibe cells into terminal rows."""
    return _normalized(snapshot).rows


def _text_rows(
    reference: _Normalized, other: _Normalized
) -> list[tuple[int, str, str]]:
    reference_rows = reference.rows
    other_rows = other.rows
    differences: list[tuple[int, str, str]] = []
    for index in range(max(len(reference_rows), len(other_rows))):
        reference_row = reference_rows[index] if index < len(reference_rows) else ""
        other_row = other_rows[index] if index < len(other_rows) else ""
        if reference_row == other_row or _same_truncated_footer(
            reference_row, other_row
        ):
            continue
        differences.append((index, reference_row, other_row))
    return differences


def _same_truncated_footer(reference_row: str, other_row: str) -> bool:
    """Whether a footer only differs by the tail a wider PID pushed off the edge."""
    return "PID #]" in reference_row and (
        reference_row.startswith(other_row) or other_row.startswith(reference_row)
    )


def _color_cells(
    reference: _Normalized, other: _Normalized
) -> list[tuple[int, int, Cell, Cell]]:
    differences: list[tuple[int, int, Cell, Cell]] = []
    for y, x, reference_cell, other_cell in _shared_cells(reference, other):
        if reference_cell.character != other_cell.character:
            continue
        foreground_differs = bool(reference_cell.character.strip()) and canon(
            reference_cell.foreground
        ) != canon(other_cell.foreground)
        background_differs = canon(reference_cell.background) != canon(
            other_cell.background
        )
        if foreground_differs or background_differs:
            differences.append((y, x, reference_cell, other_cell))
    return differences


def attr_cells(
    reference: Snapshot, other: Snapshot
) -> list[tuple[int, int, Cell, Cell]]:
    """Return visible text-attribute differences."""
    return _attr_cells(_normalized(reference), _normalized(other))


def _attr_cells(
    reference: _Normalized, other: _Normalized
) -> list[tuple[int, int, Cell, Cell]]:
    differences: list[tuple[int, int, Cell, Cell]] = []
    for y, x, reference_cell, other_cell in _shared_cells(reference, other):
        if (
            reference_cell.character != other_cell.character
            or not reference_cell.character.strip()
        ):
            continue
        if reference_cell.attrs != other_cell.attrs:
            differences.append((y, x, reference_cell, other_cell))
    return differences


def hyperlink_cells(
    reference: Snapshot, other: Snapshot
) -> list[tuple[int, int, Cell, Cell]]:
    """Return visible OSC 8 hyperlink-target differences."""
    return _hyperlink_cells(_normalized(reference), _normalized(other))


def _hyperlink_cells(
    reference: _Normalized, other: _Normalized
) -> list[tuple[int, int, Cell, Cell]]:
    differences: list[tuple[int, int, Cell, Cell]] = []
    for y, x, reference_cell, other_cell in _shared_cells(reference, other):
        same_visible_glyph = (
            reference_cell.character == other_cell.character
            and reference_cell.character.strip()
        )
        if same_visible_glyph and reference_cell.hyperlink != other_cell.hyperlink:
            differences.append((y, x, reference_cell, other_cell))
    return differences


def snapshots_match(reference: Snapshot, other: Snapshot) -> bool:
    """Return whether two snapshots have identical visible semantics."""
    normalized_reference = _normalized(reference)
    normalized_other = _normalized(other)
    return not any(
        differences(normalized_reference, normalized_other)
        for differences in (_text_rows, _color_cells, _attr_cells, _hyperlink_cells)
    )


def requests_match(captures: Mapping[str, Sequence[Request]]) -> bool:
    """Return whether every client made the same semantic RPC requests."""
    requests = iter(captures.values())
    reference = normalize_requests(next(requests, ()))
    return all(reference == normalize_requests(other) for other in requests)


_AGENT_UPDATE = "session/agent/update"
_QUEUE_REPLACE = "session/turn/queue/replace"
_CLIENT_MESSAGE_ID_KEYS = frozenset({
    "clientUserMessageId",
    "entryId",
    "idempotencyKey",
    "operationId",
})


def normalize_requests(requests: Sequence[Request]) -> list[Request]:
    """Mask transport-generated values before comparing semantic RPC requests."""
    replacement_keys: dict[str, str] = {}
    return _coalesce_agent_switches([
        _normalize_request(request, replacement_keys) for request in requests
    ])


def _coalesce_agent_switches(requests: list[Request]) -> list[Request]:
    """Keep only the agent a burst of Shift+Tab settles on, not every hop it took."""
    # A switch in flight is chased when it answers, so the number of hops depends
    # on whether a reply lands between two keys of the burst. The target does not.
    coalesced: list[Request] = []
    for request in requests:
        if (
            request.method == _AGENT_UPDATE
            and coalesced
            and coalesced[-1].method == _AGENT_UPDATE
        ):
            coalesced[-1] = request
            continue
        coalesced.append(request)
    return coalesced


def _normalize_request(request: Request, replacement_keys: dict[str, str]) -> Request:
    params = _normalize_request_value(request.params)
    if key := _distinct_replacement_key(request):
        params["idempotencyKey"] = replacement_keys.setdefault(
            key, f"<replacement-idempotency-key-{len(replacement_keys) + 1}>"
        )
    return Request(request.method, params)


def _distinct_replacement_key(request: Request) -> str | None:
    if request.method != _QUEUE_REPLACE:
        return None
    key = request.params.get("idempotencyKey")
    entries = request.params.get("entries")
    if not isinstance(key, str) or not isinstance(entries, list) or not entries:
        return None
    entry = entries[0]
    if not isinstance(entry, Mapping) or key == entry.get("entryId"):
        return None
    return key


def _normalize_request_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<client-message-id>"
            if key in _CLIENT_MESSAGE_ID_KEYS
            else _normalize_request_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_request_value(item) for item in value]
    return value


def _normalize_vibe_dir(cells: list[Cell]) -> list[Cell]:
    text = "".join(cell.character for cell in cells)
    start = text.find(_VIBE_DIR)
    if start < 0:
        return cells
    end = start + len(_VIBE_DIR)
    style = cells[start]
    placeholder = [
        replace(style, character=character, hyperlink=None)
        for character in _VIBE_DIR_PLACEHOLDER
    ]
    normalized = cells[:start] + placeholder + cells[end:]
    padding = replace(cells[-1], character=" ", hyperlink=None)
    return (normalized + [padding] * len(cells))[: len(cells)]


def _normalized_cells(snapshot: Snapshot) -> list[list[Cell]]:
    normalized_rows = [
        _normalize_vibe_dir(list(snapshot_row)) for snapshot_row in snapshot.cells
    ]
    scrollbar = any(
        _SCROLLBAR_THUMB.match(row[-1].character) for row in normalized_rows
    )
    scrollbar_cols = {
        x
        for normalized_row in normalized_rows
        for x, cell in enumerate(normalized_row)
        if _SCROLLBAR_THUMB.match(cell.character)
    }
    normalized: list[list[Cell]] = []
    for cell_row in normalized_rows:
        grid_row = "".join(cell.character for cell in cell_row)
        pid_columns = _pid_columns(grid_row) | _elapsed_columns(grid_row)
        row: list[Cell] = []
        # Mask whole scrollbar columns so blank thumb cells cannot vary by host.
        for x, cell in enumerate(cell_row):
            volatile = (
                _BRAILLE.match(cell.character)
                or (
                    scrollbar
                    and x == len(cell_row) - 1
                    and (
                        cell.character == " " or _SCROLLBAR_THUMB.match(cell.character)
                    )
                )
                or (
                    x in scrollbar_cols
                    and (
                        cell.character == " " or _SCROLLBAR_THUMB.match(cell.character)
                    )
                )
            )
            if volatile:
                row.append(Cell(" ", "default", "default", frozenset()))
            elif cell.character == _PULSE_HOLLOW:
                row.append(
                    Cell(
                        _PULSE_FILLED,
                        cell.foreground,
                        cell.background,
                        cell.attrs,
                        cell.hyperlink,
                    )
                )
            elif x in pid_columns:
                row.append(
                    Cell(
                        "#",
                        cell.foreground,
                        cell.background,
                        cell.attrs,
                        cell.hyperlink,
                    )
                )
            else:
                row.append(cell)
        normalized.append(row)
    return normalized


def _pid_columns(row: str) -> set[int]:
    return {
        column
        for match in _PID.finditer(row)
        for column in range(match.start(), match.end())
    }


def _elapsed_columns(row: str) -> set[int]:
    return {
        column
        for match in _ELAPSED.finditer(row)
        for column in range(match.start(), match.end())
        if row[column].isdigit()
    }


def _shared_cells(
    reference: _Normalized, other: _Normalized
) -> list[tuple[int, int, Cell, Cell]]:
    reference_cells = reference.cells
    other_cells = other.cells
    return [
        (y, x, reference_cell, other_cell)
        for y, (reference_row, other_row) in enumerate(
            zip(reference_cells, other_cells, strict=True)
        )
        for x, (reference_cell, other_cell) in enumerate(
            zip(reference_row, other_row, strict=True)
        )
    ]
