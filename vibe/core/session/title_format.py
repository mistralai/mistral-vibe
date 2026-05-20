from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re

MAX_TITLE_LENGTH = 50
ELLIPSIS = "…"

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class TextSegment:
    text: str


@dataclass(frozen=True, slots=True)
class MentionSegment:
    name: str
    start_line: int | None = None
    end_line: int | None = None


TitleSegment = TextSegment | MentionSegment


def format_session_title(segments: Sequence[TitleSegment]) -> str:
    parts = [_render_segment(s) for s in segments]
    joined = "".join(parts)
    collapsed = _WHITESPACE_RE.sub(" ", joined).strip()

    if not collapsed:
        return ""

    if len(collapsed) > MAX_TITLE_LENGTH:
        return collapsed[:MAX_TITLE_LENGTH] + ELLIPSIS

    return collapsed


def _render_segment(segment: TitleSegment) -> str:
    match segment:
        case TextSegment(text=text):
            return text
        case MentionSegment(name=name, start_line=start, end_line=end):
            if start is not None and end is not None:
                return f"@{name}:{start}-{end}"
            if start is not None:
                return f"@{name}:{start}"
            return f"@{name}"
