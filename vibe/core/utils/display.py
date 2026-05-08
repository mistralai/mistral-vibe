from __future__ import annotations

from vibe.core.session.session_id import shorten_session_id


def compact_reduction_display(
    old_tokens: int | None,
    new_tokens: int | None,
    *,
    old_session_id: str | None = None,
    new_session_id: str | None = None,
) -> str:

    message = "Compaction complete"
    if old_tokens is not None and new_tokens is not None:
        reduction = old_tokens - new_tokens
        reduction_pct = (reduction / old_tokens * 100) if old_tokens > 0 else 0
        message = (
            f"{message}: {old_tokens:,} → "
            f"{new_tokens:,} tokens ({-reduction_pct:+#0.2g}%)"
        )

    if old_session_id is not None and new_session_id is not None:
        short_old = shorten_session_id(old_session_id)
        short_new = shorten_session_id(new_session_id)
        message = (
            f"{message}\n"
            f"session: {short_old} (before compaction) → {short_new} (after compaction)"
        )

    return message
