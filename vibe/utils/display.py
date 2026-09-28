from __future__ import annotations

from vibe.utils.session_id import shorten_session_id


def rewind_fork_display(old_session_id: str, new_session_id: str) -> str:
    short_old = shorten_session_id(old_session_id)
    short_new = shorten_session_id(new_session_id)
    return (
        "Forked to a new session.\n"
        f"session: {short_old} (before rewind) → {short_new} (after rewind)"
    )


def branch_display(old_session_id: str, new_session_id: str) -> str:
    short_old = shorten_session_id(old_session_id)
    short_new = shorten_session_id(new_session_id)
    return (
        "Branched to a new session.\n"
        f"session: {short_old} → {short_new}\n"
        f"Resume the copy with: vibe --resume {short_new}"
    )
