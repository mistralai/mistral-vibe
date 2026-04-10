from __future__ import annotations

from collections.abc import Sequence

from vibe.core.types import LLMMessage, Role


def merge_consecutive_user_messages(messages: Sequence[LLMMessage]) -> list[LLMMessage]:
    """Merge consecutive user messages into a single message.

    This handles cases where middleware injects messages resulting in
    consecutive user messages before sending to the API.
    """
    result: list[LLMMessage] = []
    for msg in messages:
        if result and result[-1].role == Role.user and msg.role == Role.user:
            prev_content = result[-1].content or ""
            curr_content = msg.content or ""
            merged_content = f"{prev_content}\n\n{curr_content}".strip()
            result[-1] = LLMMessage(
                role=Role.user, content=merged_content, message_id=result[-1].message_id
            )
        else:
            result.append(msg)

    return result
