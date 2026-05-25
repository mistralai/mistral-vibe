from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import SplitResult, urlsplit

from acp.helpers import ContentBlock

from vibe.core.session.title_format import MentionSegment, TextSegment, TitleSegment

_LINE_FRAGMENT_RE = re.compile(r"^L(\d+)(?:-L(\d+))?$")


def acp_blocks_to_title_segments(blocks: list[ContentBlock]) -> list[TitleSegment]:
    segments: list[TitleSegment] = []
    for block in blocks:
        if block.field_meta and block.field_meta.get("automatic"):
            continue
        if (segment := _block_to_segment(block)) is not None:
            segments.append(segment)
    return segments


def _block_to_segment(block: ContentBlock) -> TitleSegment | None:
    match block.type:
        case "text":
            return TextSegment(text=block.text)
        case "resource":
            base, start, end = _parse_line_range_fragment(block.resource.uri)
            name = _basename_from_uri(base)
            if not name:
                return None
            return MentionSegment(name=name, start_line=start, end_line=end)
        case "resource_link":
            base, start, end = _parse_line_range_fragment(block.uri)
            name = block.name or _basename_from_uri(base)
            if not name:
                return None
            return MentionSegment(name=name, start_line=start, end_line=end)
        case _:
            return None


def _safe_urlsplit(uri: str) -> SplitResult | None:
    try:
        return urlsplit(uri)
    except ValueError:
        return None


def _parse_line_range_fragment(uri: str) -> tuple[str, int | None, int | None]:
    parts = _safe_urlsplit(uri)
    if parts is None or not parts.fragment:
        return uri, None, None
    match = _LINE_FRAGMENT_RE.match(parts.fragment)
    if match is None:
        return uri, None, None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) is not None else None
    base_uri = parts._replace(fragment="").geturl()
    return base_uri, start, end


def _basename_from_uri(uri: str) -> str:
    parts = _safe_urlsplit(uri)
    if parts is None:
        return ""
    path = parts.path if parts.scheme else uri
    return Path(path).name
