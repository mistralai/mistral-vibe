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
# The loading hint's turn-elapsed counter (`Generating… (0s …` or `(0s)` alone)
# is wall-clock, so a slower host renders `12s` or `1m5s` where a fast one
# renders `0s`. Collapse it to a fixed-width `#s` so the golden is host-independent.
_ELAPSED = re.compile(r"(?<=… \()(?:\d+h)?(?:\d+m)?\d+s(?=[ )])")
# Mask the update dialog's `<client version> → <latest>` digits so goldens
# survive a version bump; the `semver → semver` lookahead keeps fixture
# versions, IPs, and the pinned latest intact. The mask keeps the width, so a
# digit-count change or a row wrap still needs one regen.
_UPDATE_CURRENT = re.compile(r"\d+\.\d+\.\d+(?= → \d+\.\d+\.\d+)")
_VIBE_DIR = str(VIBE_DIR)
_VIBE_DIR_PLACEHOLDER = "<vibe-dir>"
# macOS canonicalizes `/tmp` under `/private`; scenario-pinned `/tmp` paths must match Linux.
_PRIVATE_TMP = "/private/tmp/"

_SWITCHES = frozenset({"session/agent/update", "session/approvalPolicy/update"})
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
    return _coalesce_switches([
        _normalize_request(request, replacement_keys) for request in requests
    ])


def _coalesce_switches(requests: list[Request]) -> list[Request]:
    """Keep only the target a burst of Shift+Tab settles on, not every hop it took."""
    # A switch in flight is chased when it answers, so the number of hops depends
    # on whether a reply lands between two keys of the burst. The target does not.
    coalesced: list[Request] = []
    for request in requests:
        if (
            request.method in _SWITCHES
            and coalesced
            and coalesced[-1].method == request.method
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
    # `session/start` names the harness's checkout; goldens must not. A
    # worktree run's `session/resume` or `session/continue` names the settled
    # worktree, which is machine-local the same way.
    if request.method in {
        "session/start",
        "session/resume",
        "session/continue",
    } and isinstance(params.get("agentConfig"), dict):
        params["agentConfig"]["cwd"] = "<cwd>"
    # The footer's branch read names the checkout the same way.
    if request.method == "workspace/git/checkouts":
        params["repoLocalPaths"] = ["<cwd>" for _ in params.get("repoLocalPaths", [])]
        if params.get("sessionCwd") is not None:
            params["sessionCwd"] = "<cwd>"
    # The startup config read names the checkout too.
    if request.method == "config/read" and isinstance(params.get("cwd"), str):
        params["cwd"] = "<cwd>"
    # The trusted-folder add view starts on the checkout and resolves against it.
    if request.method.startswith("workspace/trust/folders/") and isinstance(
        cwd := params.get("cwd"), str
    ):
        params["cwd"] = "<cwd>"
        path = params.get("path")
        if isinstance(path, str) and path.startswith(cwd):
            params["path"] = "<cwd>" + path[len(cwd) :]
    # `initialize` names the build's version; goldens must survive a bump.
    if (
        request.method == "initialize"
        and isinstance(params.get("clientInfo"), dict)
        and "version" in params["clientInfo"]
    ):
        params["clientInfo"]["version"] = "<client-version>"
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
        # A null id is a semantic choice (e.g. fork the whole session), not a generated value.
        return {
            key: "<client-message-id>"
            if key in _CLIENT_MESSAGE_ID_KEYS and item is not None
            else _normalize_request_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_request_value(item) for item in value]
    if isinstance(value, str) and value.startswith(_PRIVATE_TMP):
        return value.removeprefix("/private")
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


def _collapse_elapsed(cells: list[Cell]) -> list[Cell]:
    text = "".join(cell.character for cell in cells)
    if (match := _ELAPSED.search(text)) is None:
        return cells
    start, end = match.span()
    counter = [replace(cells[start], character="#"), cells[end - 1]]
    normalized = cells[:start] + counter + cells[end:]
    padding = replace(cells[-1], character=" ", hyperlink=None)
    return (normalized + [padding] * len(cells))[: len(cells)]


def _normalized_cells(
    snapshot: Snapshot, keep_braille: bool = False
) -> list[list[Cell]]:
    normalized_rows = [
        _collapse_elapsed(_normalize_vibe_dir(list(snapshot_row)))
        for snapshot_row in snapshot.cells
    ]
    normalized: list[list[Cell]] = []
    for cell_row in normalized_rows:
        grid_row = "".join(cell.character for cell in cell_row)
        version_columns = _version_columns(grid_row)
        row: list[Cell] = []
        for x, cell in enumerate(cell_row):
            if not keep_braille and _BRAILLE.match(cell.character):
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
            elif x in version_columns:
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


def _version_columns(row: str) -> set[int]:
    return {
        column
        for match in _UPDATE_CURRENT.finditer(row)
        for column in range(match.start(), match.end())
        if row[column].isdigit()
    }
