"""History selection and immutable data captured for a session fork."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from mistralai_vibe_local_harness.vibe._errors import HarnessForkEntryNotFoundError
from mistralai_vibe_local_harness.vibe._storage import (
    InteropHistoryMessageV1,
    PluginLockV1,
    SessionMetadataV1,
    UnifiedInteropSourceV1,
)

_IMPORTED_ENTRY_ID_PREFIX = "imported-"


@dataclass(frozen=True, slots=True)
class SessionForkSnapshot:
    """Completed history and inherited Runtime state needed to create a fork."""

    source: UnifiedInteropSourceV1
    history: tuple[InteropHistoryMessageV1, ...]
    session_metadata: SessionMetadataV1
    plugin_lock: PluginLockV1


def history_for_fork(
    history: list[InteropHistoryMessageV1], entry_id: str | None, *, include_entry: bool
) -> list[InteropHistoryMessageV1]:
    if entry_id is None:
        return list(history)

    try:
        anchor = history_user_anchor(history, entry_id)
    except ValueError as exc:
        raise HarnessForkEntryNotFoundError(entry_id) from exc
    if not include_entry:
        return list(history[:anchor])

    end = next(
        (
            index
            for index, message in enumerate(history[anchor + 1 :], start=anchor + 1)
            if message.role == "user"
        ),
        len(history),
    )
    return list(history[:end])


def history_user_anchor(
    history: Sequence[InteropHistoryMessageV1], entry_id: str
) -> int:
    anchor = next(
        (
            index
            for index, message in enumerate(history)
            if message.role == "user"
            and any(
                part.meta is not None
                and part.meta.get("vibe_client_message_id") == entry_id
                for part in message.content
            )
        ),
        None,
    )
    if anchor is None:
        anchor = _imported_history_anchor(history, entry_id)
    if anchor is None:
        raise ValueError(f"Cannot find user entry: {entry_id}")
    return anchor


def _imported_history_anchor(
    history: Sequence[InteropHistoryMessageV1], entry_id: str
) -> int | None:
    """Resolve an id minted for imported history back to its history position.

    Imported history is fed to Core without its Runtime metadata, so the client
    message ids the lookup above wants do not survive the import: `_public_state`
    numbers those entries by position instead. Reading that numbering backwards
    is what lets a forked session be rewound onto a turn it inherited rather
    than one it recorded itself.
    """
    suffix = entry_id.removeprefix(_IMPORTED_ENTRY_ID_PREFIX)
    if suffix == entry_id or not suffix.isdigit():
        return None
    index = int(suffix) - 1
    if not 0 <= index < len(history) or imported_entry_id(index) != entry_id:
        return None
    return index if history[index].role == "user" else None


def imported_entry_id(index: int) -> str:
    return f"{_IMPORTED_ENTRY_ID_PREFIX}{index + 1}"


__all__ = [
    "SessionForkSnapshot",
    "history_for_fork",
    "history_user_anchor",
    "imported_entry_id",
]
