"""What another session's message and a handed task look like to the model,
and how public history shows them.

The model reads who sent a message from a ``<session_message/>`` block ahead
of it; clients read the same from a ``vibe.session_message`` display marker,
and public history drops the block. A task one session hands another carries a
``<started_by_session>`` block telling the model whom to report back to;
public history and the session preview drop it.
"""

from __future__ import annotations

from html import escape
import re
from typing import Final

from pydantic import JsonValue

from vibe.app_server.models import (
    ContentBlock,
    PublicHistoryEntry,
    PublicMessageEntry,
    SessionContentBlock,
    SessionTextContentBlock,
    TextContentBlock,
)
from vibe.app_server.protocol import SessionMessageSender
from vibe.user_content import UserDisplayContent

SESSION_MESSAGE_DISPLAY_BLOCK: Final = "vibe.session_message"

_SESSION_MESSAGE_HEADER = re.compile(
    r'<session_message from_session_id="[^"]*"(?: from_title="[^"]*")?/>'
)
_HANDED_TASK = re.compile(
    r'<started_by_session session_id="[^"]*">\n.*\n</started_by_session>', re.DOTALL
)
_HANDED_TASK_PREVIEW_START: Final = " <started_by_session "
_SESSION_MESSAGE_HEADER_START: Final = "<session_message from_session_id="
# Leaves the message itself in a session preview the Harness cuts short.
_MAX_HEADER_TITLE_CHARS: Final = 80


def session_message_header(sender: SessionMessageSender) -> str:
    title = (
        f' from_title="{escape(_shortened(sender.title))}"'
        if sender.title is not None
        else ""
    )
    return f'<session_message from_session_id="{escape(sender.session_id)}"{title}/>'


def _shortened(title: str) -> str:
    if len(title) <= _MAX_HEADER_TITLE_CHARS:
        return title
    return title[: _MAX_HEADER_TITLE_CHARS - 1].rstrip() + "…"


def session_message_display(sender: SessionMessageSender) -> UserDisplayContent:
    marker: dict[str, JsonValue] = {
        "type": SESSION_MESSAGE_DISPLAY_BLOCK,
        "sessionId": sender.session_id,
    }
    if sender.title is not None:
        marker["title"] = sender.title
    return UserDisplayContent(version="1", host="vibe", content=[marker])


def handed_task_text(started_by_session_id: str) -> str:
    return (
        f'<started_by_session session_id="{escape(started_by_session_id)}">\n'
        "Another session started this one and handed it the task above. When "
        "the task is done, or when you need an answer only that session has, "
        'report back with `send_to_session` and `delivery: "start_turn"`, '
        "leaving `session_id` out: it goes to the session that started this "
        "one. Send a short, self-contained summary of the outcome, not a "
        "transcript. Do not report back on anything else.\n"
        "</started_by_session>"
    )


def project_session_message(entry: PublicHistoryEntry) -> PublicHistoryEntry:
    if not isinstance(entry, PublicMessageEntry) or entry.role != "user":
        return entry
    content = public_session_message_content(
        entry.content, is_session_message(entry.user_display_content)
    )
    if content is entry.content:
        return entry
    return entry.model_copy(update={"content": content})


def is_session_message(display: UserDisplayContent | None) -> bool:
    return display is not None and any(
        block.get("type") == SESSION_MESSAGE_DISPLAY_BLOCK for block in display.content
    )


def public_session_message_content[BlockT: ContentBlock | SessionContentBlock](
    content: list[BlockT], from_session: bool
) -> list[BlockT]:
    """A message another session sent shows as it was sent: without the block
    naming its sender, nor the file and skill blocks prompt preparation
    appends. A handed task drops the report-back block the adapter adds.
    """
    if (
        from_session
        and len(content) > 1
        and _is_text(content[0], _SESSION_MESSAGE_HEADER)
    ):
        return content[1:2]
    if len(content) <= 1:
        return content
    kept = [block for block in content if not _is_text(block, _HANDED_TASK)]
    return content if len(kept) == len(content) else kept


def _is_text(
    block: ContentBlock | SessionContentBlock, pattern: re.Pattern[str]
) -> bool:
    return isinstance(block, TextContentBlock | SessionTextContentBlock) and bool(
        pattern.fullmatch(block.text)
    )


def project_session_message_preview(preview: str) -> str:
    """The Harness flattens a message's blocks into one line, then cuts it.

    The preview carries no display marker, so a user who typed the header sees
    it stripped here, though history keeps it.
    """
    if header := _SESSION_MESSAGE_HEADER.match(preview):
        preview = preview[header.end() :].lstrip()
    elif preview.startswith(_SESSION_MESSAGE_HEADER_START):
        return ""
    start = preview.find(_HANDED_TASK_PREVIEW_START)
    return preview if start == -1 else preview[:start].rstrip()
