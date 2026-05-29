from __future__ import annotations

from vibe.core.session.session_id import shorten_session_id


def compact_complete_display(
    old_session_id: str | None = None, new_session_id: str | None = None
) -> str:

    message = "Compaction completed."
    if old_session_id is not None and new_session_id is not None:
        short_old = shorten_session_id(old_session_id)
        short_new = shorten_session_id(new_session_id)
        message = (
            f"{message}\n"
            f"session: {short_old} (before compaction) → {short_new} (after compaction)"
        )

    return message
