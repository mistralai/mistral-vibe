from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Literal


class FileStateError(Exception):
    """Base error for checkpoint operations."""


class TurnStateError(FileStateError):
    """Raised on turn-lifecycle misuse (recording or deciding out of turn order)."""


class OpaqueReason(StrEnum):
    MISSING = auto()
    BINARY_OR_UNDECODABLE = auto()


@dataclass(frozen=True, slots=True)
class FileState:
    data: bytes | None

    @property
    def exists(self) -> bool:
        return self.data is not None

    @property
    def is_binary(self) -> bool:
        return self.data is not None and b"\x00" in self.data

    @classmethod
    def absent(cls) -> FileState:
        return cls(data=None)

    @classmethod
    def from_text(cls, text: str) -> FileState:
        return cls(data=text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class Region:
    """One line hunk. Indices are half-open ``[start, start + len(lines))`` into
    the keepends-split lines of each side of the edit that produced it.
    """

    baseline_start: int
    baseline_lines: tuple[str, ...]
    current_start: int
    current_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpaqueChange:
    """A whole-file unit for files that cannot be line-diffed (binary,
    undecodable, or deleted); keeps both sides' bytes so a revert can restore.
    """

    reason: OpaqueReason
    baseline: FileState
    current: FileState


@dataclass(frozen=True, slots=True)
class AgentTurn:
    turn_id: int


@dataclass(frozen=True, slots=True)
class ManualEdit:
    """A change the user made by hand. ``index`` is its 1-based slot."""

    index: int


Owner = AgentTurn | ManualEdit


class Decision(StrEnum):
    PENDING = auto()
    KEEP = auto()
    REVERT = auto()


@dataclass(frozen=True, slots=True)
class RegionId:
    """Stable identity of a hunk: ``version_index`` is the seq of the producing
    edit (immutable as the log grows) and ``ordinal`` its position in that diff.
    """

    version_index: int
    ordinal: int


@dataclass(frozen=True, slots=True)
class TurnRegion:
    region_id: RegionId
    owner: Owner
    change: Region | OpaqueChange
    decision: Decision
    depends_on: tuple[RegionId, ...]


HunkSide = Literal["additions", "deletions"]


@dataclass(frozen=True, slots=True)
class HunkAnchor:
    """One pending hunk located in a *rendered diff's* coordinate space, so a
    caller can pin an accept/revert control to it. ``line`` is 0-based and points
    at the block's last line — on the current side for additions/edits or the
    baseline side for pure deletions — so the control renders right after the
    whole change rather than inside a multi-line hunk. ``regions`` are the pending
    hunks the control decides together (its target).
    """

    side: HunkSide
    line: int
    regions: tuple[RegionId, ...]
