from __future__ import annotations

from vibe.app_server.models import (
    ContentBlock,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicTurnQueue,
    SessionContentBlock,
    SessionTextContentBlock,
    TextContentBlock,
    TurnUserInputEntry,
)
from vibe.app_server.protocol import (
    TurnEnqueueParams,
    TurnQueueReplaceParams,
    TurnStartParams,
    TurnSteerParams,
)
from vibe.user_content import UserDisplayContent

SCHEDULED_LOOP_DISPLAY_BLOCK = "vibe.scheduled_loop"

LOOP_INSTRUCTIONS = (
    "<loop_instructions>\n"
    "`/loop [schedule] [prompt]` requests a recurring prompt. Interpret the "
    "schedule in natural language and use the cron tool: interval_seconds for "
    "fixed frequencies, or a five-field cron expression for calendar schedules "
    "in the machine's local timezone. Schedule the prompt rather than executing "
    "it now. Use the tool also to list or cancel schedules when requested. "
    "Ask the user if their intent is unclear.\n"
    "</loop_instructions>"
)


def scheduled_loop_display(loop_id: str, fired_at_ms: int) -> UserDisplayContent:
    # Stored on the prompt itself, so forks and resumes keep the fired-loop marker
    # that the projection-only notice loses.
    return UserDisplayContent(
        version="1",
        host="vibe",
        content=[
            {
                "type": SCHEDULED_LOOP_DISPLAY_BLOCK,
                "loopId": loop_id,
                "firedAt": fired_at_ms,
            }
        ],
    )


def is_loop_prompt(text: str) -> bool:
    words = text.split(None, 1)
    return bool(words) and words[0].casefold() == "/loop"


def prepare_loop_turn[ParamsT: TurnStartParams | TurnSteerParams](
    params: ParamsT,
) -> ParamsT:
    text = "\n".join(
        block.text for block in params.message if isinstance(block, TextContentBlock)
    )
    if not is_loop_prompt(text):
        return params
    return params.model_copy(
        update={"message": [*params.message, TextContentBlock(text=LOOP_INSTRUCTIONS)]}
    )


def prepare_loop_queue[ParamsT: TurnEnqueueParams | TurnQueueReplaceParams](
    params: ParamsT,
) -> ParamsT:
    entries = []
    for entry in params.entries:
        if isinstance(entry, TurnUserInputEntry) and _is_loop_content(entry.content):
            entry = entry.model_copy(
                update={
                    "content": [
                        *entry.content,
                        SessionTextContentBlock(text=LOOP_INSTRUCTIONS),
                    ]
                }
            )
        entries.append(entry)
    return params.model_copy(update={"entries": entries})


def project_loop_preview(preview: str) -> str:
    if not is_loop_prompt(preview):
        return preview
    instructions = LOOP_INSTRUCTIONS.replace("\n", " ")
    # Harness joins blocks with newlines, flattens them, then truncates at 200 chars.
    start = preview.rfind(" <")
    while start != -1:
        candidate = preview[start + 1 :]
        if candidate == instructions or (
            candidate.endswith("…") and instructions.startswith(candidate[:-1])
        ):
            return preview[:start].rstrip()
        start = preview.rfind(" <", 0, start)
    return preview


def project_loop_message(entry: PublicHistoryEntry) -> PublicHistoryEntry:
    if not isinstance(entry, PublicMessageEntry) or entry.role != "user":
        return entry
    return entry.model_copy(update={"content": _without_instructions(entry.content)})


def project_loop_queue(queue: PublicTurnQueue) -> PublicTurnQueue:
    return queue.model_copy(
        update={
            "items": [
                item.model_copy(
                    update={
                        "entries": [
                            entry.model_copy(
                                update={"content": _without_instructions(entry.content)}
                            )
                            if isinstance(entry, TurnUserInputEntry)
                            else entry
                            for entry in item.entries
                        ]
                    }
                )
                for item in queue.items
            ]
        }
    )


def _is_loop_content[BlockT: ContentBlock | SessionContentBlock](
    content: list[BlockT],
) -> bool:
    return is_loop_prompt(
        "\n".join(
            block.text
            for block in content
            if isinstance(block, TextContentBlock | SessionTextContentBlock)
        )
    )


def _without_instructions[BlockT: ContentBlock | SessionContentBlock](
    content: list[BlockT],
) -> list[BlockT]:
    if not _is_loop_content(content):
        return content
    return [
        block
        for block in content
        if not (
            isinstance(block, TextContentBlock | SessionTextContentBlock)
            and block.text == LOOP_INSTRUCTIONS
        )
    ]
