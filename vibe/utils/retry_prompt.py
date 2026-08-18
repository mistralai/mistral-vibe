from __future__ import annotations

from vibe.utils import VIBE_WARNING_TAG


def build_retry_prompt(additional_instructions: str) -> str:
    message = (
        "The previous model stream ended before reaching its end. Continue the "
        "response exactly where it stopped without repeating text already produced. "
        "If no response text was produced, answer the pending user request normally."
    )
    if instructions := additional_instructions.strip():
        message += (
            "\n\nFollow these additional instructions from the user while "
            f"continuing:\n{instructions}"
        )
    return f"<{VIBE_WARNING_TAG}>{message}</{VIBE_WARNING_TAG}>"
