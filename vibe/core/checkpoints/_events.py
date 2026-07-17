from __future__ import annotations

from dataclasses import dataclass, field

from vibe.core.checkpoints.models import (
    FileState,
    OpaqueChange,
    Owner,
    Region,
    RegionId,
)


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
    # Memoized hunk decomposition (line regions, or one opaque whole-file unit),
    # filled lazily by ``History._changes_of``. An edit's ``before``/``after`` are
    # fixed at construction, so this is a pure function of the edit and is shared
    # across every History built over the same log — a fresh History per read
    # would otherwise recompute every edit's SequenceMatcher diff. Excluded from
    # equality/repr: it is a derived cache, not identity.
    _changes: list[tuple[int, Region | OpaqueChange]] | None = field(
        default=None, compare=False, repr=False
    )


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
