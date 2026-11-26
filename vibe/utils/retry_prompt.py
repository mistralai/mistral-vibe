from __future__ import annotations

from vibe.utils import VIBE_WARNING_TAG


def build_retry_prompt(additional_instructions: str) -> str:
    message = (
        "Your previous response was interrupted before it finished, and its "
        "unfinished output was discarded. Continue working on the pending user "
        "request from where the work stands, and write your reply in full."
    )
    if instructions := additional_instructions.strip():
        message += (
            f"\n\nFollow these additional instructions from the user:\n{instructions}"
        )
    return f"<{VIBE_WARNING_TAG}>{message}</{VIBE_WARNING_TAG}>"
