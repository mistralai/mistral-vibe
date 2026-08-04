from __future__ import annotations

from dataclasses import dataclass, field

from vibe.core.checkpoints.models import FileState, Owner, RegionId


@dataclass(slots=True)
class _TurnMark:
    """A turn boundary. ``pre`` holds each touched file's pre-edit state,
    populated live as the turn records edits.
    """

    seq: int
    turn_id: int
    pre: dict[str, FileState] = field(default_factory=dict)


@dataclass(slots=True)
class _Edit:
    """One file's ``before -> after`` change with its ``owner``. ``deps`` maps each
    hunk ordinal to the earlier hunks it was built on, fixed at append time.
    """

    seq: int
    owner: Owner
    path: str
    before: FileState
    after: FileState
    deps: dict[int, tuple[RegionId, ...]]


@dataclass(slots=True)
class _Decide:
    """A keep/revert on a hunk. Reverting dependents is derived at read, not stored."""

    seq: int
    path: str
    hunk: RegionId
    keep: bool


_Event = _TurnMark | _Edit | _Decide


def _rid_key(rid: RegionId) -> tuple[int, int]:
    return (rid.version_index, rid.ordinal)
