from __future__ import annotations


def compact_reduction_display(old_tokens: int | None, new_tokens: int | None) -> str:
    if old_tokens is None or new_tokens is None:
        return "Compaction complete"

    reduction = old_tokens - new_tokens
    reduction_pct = (reduction / old_tokens * 100) if old_tokens > 0 else 0
    return (
        f"Compaction complete: {old_tokens:,} → "
        f"{new_tokens:,} tokens ({-reduction_pct:+#0.2g}%)"
    )
