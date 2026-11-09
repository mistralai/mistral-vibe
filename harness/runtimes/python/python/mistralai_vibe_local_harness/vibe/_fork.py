"""History selection and immutable data captured for a session fork."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from mistralai_vibe_local_harness.vibe._errors import HarnessForkEntryNotFoundError
from mistralai_vibe_local_harness.vibe._projection import SKIPPED_USER_MESSAGE_OUTCOME
from mistralai_vibe_local_harness.vibe._storage import (
    InteropHistoryMessageV1,
    PluginLockV1,
    SessionMetadataV1,
    UnifiedInteropSourceV1,
    checkpoint_messages,
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
    history: list[InteropHistoryMessageV1],
    entry_id: str | None,
    *,
    include_entry: bool,
    public_entries: Sequence[Mapping[str, object]] = (),
) -> list[InteropHistoryMessageV1]:
    if entry_id is None:
        return list(history)

    try:
        anchor = history_user_anchor(history, entry_id)
    except ValueError as exc:
        skipped = skipped_user_anchor(history, entry_id, public_entries)
        if skipped is None:
            raise HarnessForkEntryNotFoundError(entry_id) from exc
        # A skipped message has no answer: keeping it keeps nothing more.
        return list(history[:skipped])
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


def active_turn_start(checkpoint: dict[str, JsonValue]) -> int | None:
    """Where the active Turn's start message sits in the checkpoint's context.

    The Core puts a Turn's start message, after the context delivered with it, in
    a new Turn group. ``None`` when an in-Turn compaction merged it into the
    summary group.
    """
    messages = [
        cast(dict[str, JsonValue], item)
        for item in checkpoint_messages(checkpoint)
        if isinstance(item, dict)
    ]
    turn = checkpoint.get("turn")
    phase = turn.get("phase") if isinstance(turn, dict) else None
    pending = phase.get("pending") if isinstance(phase, dict) else None
    if isinstance(pending, dict) and pending.get("hook") == "pre_agent_turn":
        # The start message waits for its pre_agent hooks outside the context.
        return len(messages)
    indexes = [item.get("turn_index") for item in messages]
    latest = next((index for index in reversed(indexes) if index is not None), None)
    compactions = checkpoint.get("compaction_count")
    if latest is None or (latest == 0 and compactions != 0):
        return None
    group_start = next(
        (
            position + 1
            for position in range(len(indexes) - 1, -1, -1)
            if indexes[position] is not None and indexes[position] != latest
        ),
        0,
    )
    return next(
        (
            position
            for position in range(group_start, len(messages))
            if messages[position].get("source") == "history"
            and _is_turn_message(messages[position].get("message"))
        ),
        None,
    )


def _is_turn_message(message: JsonValue) -> bool:
    return (
        isinstance(message, dict)
        and message.get("role") == "user"
        and not message.get("context")
    )


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


def skipped_user_anchor(
    history: Sequence[InteropHistoryMessageV1],
    entry_id: str,
    public_entries: Sequence[Mapping[str, object]],
) -> int | None:
    """Where a user message a pre_agent hook blocked would sit in ``history``.

    The message is published, marked skipped, but its Turn is skipped and the
    Core history holds no trace of it. It sits right before the next user
    message the Core kept, or at the end when none follows. ``None`` for any
    other entry, and when a user message the Core keeps without its id (a loop
    prompt) lies between: the cut cannot be placed before it, and the Core
    would keep a turn the user no longer sees.
    """
    public_index = next(
        (
            index
            for index, entry in enumerate(public_entries)
            if entry.get("id") == entry_id and _is_user_entry(entry)
        ),
        None,
    )
    if (
        public_index is None
        or public_entries[public_index].get("outcome") != SKIPPED_USER_MESSAGE_OUTCOME
    ):
        return None
    for entry in public_entries[public_index + 1 :]:
        if not _is_user_entry(entry):
            continue
        if entry.get("outcome") == SKIPPED_USER_MESSAGE_OUTCOME:
            continue
        later_id = entry.get("id")
        if not isinstance(later_id, str):
            return None
        try:
            return history_user_anchor(history, later_id)
        except ValueError:
            return None
    return len(history)


def _is_user_entry(entry: Mapping[str, object]) -> bool:
    return entry.get("type") == "message" and entry.get("role") == "user"


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
    "active_turn_start",
    "history_for_fork",
    "history_user_anchor",
    "imported_entry_id",
    "skipped_user_anchor",
]
