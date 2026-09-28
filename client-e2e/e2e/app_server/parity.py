"""Mask volatile Vibe terminal cells and transport-generated request values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import re
from typing import Any

from e2e.app_server.config import VIBE_DIR
from e2e.app_server.scenario import Request
from e2e.pty.screen import Cell, Snapshot

_BRAILLE = re.compile(r"[\u2800-\u28ff]")
# The pulse spinner alternates filled/hollow squares on an unsynced 100 ms clock,
# so canonicalize the hollow frame to the filled one before comparing.
_PULSE_HOLLOW, _PULSE_FILLED = "\u25a1", "\u25a0"
_PID = re.compile(r"(?<=PID )\d+")
# The loading hint's turn-elapsed counter (`Generating… (0s …`) is wall-clock,
# so a slower host crosses a second boundary and renders `1s` where a fast one
# renders `0s`. Mask its digits (like PID) so the golden is host-independent.
_ELAPSED = re.compile(r"(?<=… \()(?:\d+h)?(?:\d+m)?\d+s(?= )")
_SCROLLBAR_THUMB = re.compile(r"[\u2581-\u2587]")
_VIBE_DIR = str(VIBE_DIR)
_VIBE_DIR_PLACEHOLDER = "<vibe-dir>"

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
