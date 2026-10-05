"""User-invoked skills: a leading ``/name`` command, or ``/name`` mentions.

The Unified adapter appends each invoked skill's rendered body to the user
message and records the names in a reserved ``userDisplayContent`` block. The
helpers here resolve the typed names and recognize those appended bodies again,
so public projections can present the literal prompt followed by one settled
``skill`` effect per invoked skill.
"""

from __future__ import annotations

from collections.abc import Sequence
import re

from pydantic import JsonValue

from vibe.core.tools.builtins.skill import already_loaded_message, skill_content_marker
from vibe.user_content import UserDisplayContent

SKILL_INVOCATION_DISPLAY_BLOCK = "vibe.skill_invocation"

_SKILL_CONTENT_CLOSING = "</skill_content>"
_SKILL_CONTENT_MARKER_PREFIX = skill_content_marker("").removesuffix('">')
_ALREADY_LOADED_NAME_SENTINEL = "__vibe_skill_name__"
_ALREADY_LOADED_NAME_PREFIX, _ALREADY_LOADED_NAME_SUFFIX = already_loaded_message(
    _ALREADY_LOADED_NAME_SENTINEL
).split(_ALREADY_LOADED_NAME_SENTINEL, maxsplit=1)
_SKILL_MENTION = re.compile(r"(?<!\S)/(\S+)")
_MENTION_TRAILING_PUNCTUATION = ".,;:!?)]}'\"`"
# Skill names are lowercase (plugin skills add a ``plugin:`` namespace), so a
# path such as ``/usr/bin`` or ``/Users`` never names a skill.
_MENTIONED_NAME = re.compile(r"[a-z0-9][a-z0-9._:-]*")
# Code is quoted, not addressed to Vibe: mentions inside it invoke nothing. A
# removed span leaves a non-space mark, so it never opens a new mention boundary.
_FENCED_CODE = re.compile(
    r"^[ \t]*(`{3,}|~{3,}).*?(?:^[ \t]*\1|\Z)", re.DOTALL | re.MULTILINE
)
_INLINE_CODE = re.compile(r"(`+)[^`\n]*?\1")
_REMOVED_CODE = "\x00"


def leading_skill_name(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped[1:].split(None, 1)
    return parts[0].casefold() if parts else None


def mentioned_skill_names(text: str) -> list[str]:
    """Names of the ``/name`` mentions outside code, in order, without
    repeats. A leading ``/name`` is the slash command, not a mention.
    """
    prose = _INLINE_CODE.sub(_REMOVED_CODE, _FENCED_CODE.sub(_REMOVED_CODE, text))
    command_at = len(prose) - len(prose.lstrip())
    names: list[str] = []
    for match in _SKILL_MENTION.finditer(prose):
        if match.start() == command_at:
            continue
        name = match.group(1).rstrip(_MENTION_TRAILING_PUNCTUATION)
        if _MENTIONED_NAME.fullmatch(name) and name not in names:
            names.append(name)
    return names


def invoked_skill_names(text: str) -> list[str]:
    """Casefolded names the prompt invokes, the leading ``/name`` first."""
    names = [name] if (name := leading_skill_name(text)) is not None else []
    names.extend(name for name in mentioned_skill_names(text) if name not in names)
    return names


def with_skill_invocation_display(
    display: UserDisplayContent | None, names: Sequence[str]
) -> UserDisplayContent | None:
    markers: list[dict[str, JsonValue]] = [
        {"type": SKILL_INVOCATION_DISPLAY_BLOCK, "name": name} for name in names
    ]
    if not markers:
        return display
    if display is None:
        return UserDisplayContent(version="1", host="vibe", content=markers)
    return display.model_copy(update={"content": [*display.content, *markers]})


def skill_invocation_display_names(display: UserDisplayContent | None) -> list[str]:
    if display is None:
        return []
    return [
        name
        for block in display.content
        if block.get("type") == SKILL_INVOCATION_DISPLAY_BLOCK
        and isinstance(name := block.get("name"), str)
        and name
    ]


def without_skill_invocation_display(
    display: UserDisplayContent | None,
) -> UserDisplayContent | None:
    if display is None:
        return None
    content = [
        block
        for block in display.content
        if block.get("type") != SKILL_INVOCATION_DISPLAY_BLOCK
    ]
    return display.model_copy(update={"content": content}) if content else None


def invoked_skill_payloads(
    texts: Sequence[str | None], display_names: Sequence[str]
) -> list[tuple[int, str]]:
    """Locate adapter-appended skill bodies among a message's content blocks.

    ``texts`` holds each block's text, ``None`` for non-text blocks. A block
    counts as a payload when it is a rendered body or an already-loaded note for
    a skill the display provenance names (any name before provenance existed)
    and the remaining visible text still invokes that skill. Returns
    ``(block index, skill name)`` pairs in content order.
    """
    candidates: list[tuple[int, str]] = []
    claimed: set[str] = set()
    for index in range(len(texts) - 1, -1, -1):
        text = texts[index]
        if text is None:
            continue
        payload_name = _skill_content_name(text) or _already_loaded_skill_name(text)
        if payload_name is None:
            continue
        name = _provenance_name(payload_name, display_names)
        if name is None or name.casefold() in claimed:
            continue
        claimed.add(name.casefold())
        candidates.append((index, name))
    if not candidates:
        return []
    indices = {index for index, _ in candidates}
    visible = "\n\n".join(
        text
        for index, text in enumerate(texts)
        if text is not None and index not in indices
    )
    invoked = invoked_skill_names(visible)
    return sorted(
        (index, name) for index, name in candidates if name.casefold() in invoked
    )


def public_session_preview(preview: str) -> str:
    """Remove adapter-appended skill payloads from a Harness session preview."""
    names = invoked_skill_names(preview)
    if not names:
        return preview
    searchable = preview.removesuffix("…").rstrip()
    starts = [
        start
        for name in names
        if (start := _skill_content_start(searchable, name)) is not None
        or (start := _truncated_skill_marker_start(searchable, name)) is not None
    ]
    if not starts:
        return preview
    return preview[: min(starts)].rstrip()


def _provenance_name(payload_name: str, display_names: Sequence[str]) -> str | None:
    if not display_names:
        return payload_name
    return next(
        (name for name in display_names if name.casefold() == payload_name.casefold()),
        None,
    )


def _already_loaded_skill_name(text: str) -> str | None:
    if not text.startswith(_ALREADY_LOADED_NAME_PREFIX) or not text.endswith(
        _ALREADY_LOADED_NAME_SUFFIX
    ):
        return None
    name_end = len(text) - len(_ALREADY_LOADED_NAME_SUFFIX)
    return text[len(_ALREADY_LOADED_NAME_PREFIX) : name_end] or None


def _skill_content_name(text: str) -> str | None:
    if not text.startswith(_SKILL_CONTENT_MARKER_PREFIX) or not text.rstrip().endswith(
        _SKILL_CONTENT_CLOSING
    ):
        return None
    name_start = len(_SKILL_CONTENT_MARKER_PREFIX)
    name_end = text.find('">', name_start)
    if name_end < 0:
        return None
    return text[name_start:name_end] or None


def _starts_with_skill_heading(content: str, name: str) -> bool:
    heading = f"# Skill: {name}"
    return content.startswith(heading) or heading.startswith(content)


def _truncated_skill_marker_start(text: str, name: str) -> int | None:
    marker_at = text.rfind("<skill_content")
    if marker_at < 0:
        return None
    marker = skill_content_marker(name).casefold()
    if marker.startswith(text[marker_at:].casefold()):
        return marker_at
    return None


def _skill_content_start(text: str, name: str) -> int | None:
    stack: list[int | None] = []
    rendered_roots: list[int] = []
    completed_roots: dict[int, int] = {}
    cursor = 0
    while cursor < len(text):
        marker_at = text.find(_SKILL_CONTENT_MARKER_PREFIX, cursor)
        closing_at = text.find(_SKILL_CONTENT_CLOSING, cursor)
        if marker_at >= 0 and (closing_at < 0 or marker_at < closing_at):
            name_start = marker_at + len(_SKILL_CONTENT_MARKER_PREFIX)
            name_end = text.find('">', name_start)
            if name_end < 0:
                break
            opened_name = text[name_start:name_end]
            parent_root = stack[-1] if stack else None
            is_rendered = opened_name.casefold() == name.casefold() and (
                _starts_with_skill_heading(text[name_end + 2 :].lstrip(), opened_name)
            )
            root = parent_root
            if is_rendered and root is None:
                root = marker_at
                rendered_roots.append(root)
            stack.append(root)
            cursor = name_end + 2
            continue
        if closing_at < 0:
            break
        if stack:
            root = stack.pop()
            if root is not None and root not in stack:
                completed_roots[root] = closing_at + len(_SKILL_CONTENT_CLOSING)
        cursor = closing_at + len(_SKILL_CONTENT_CLOSING)

    trailing = [
        root
        for root in rendered_roots
        if completed_roots.get(root) == len(text.rstrip())
    ]
    if trailing:
        return trailing[-1]
    unmatched = [root for root in rendered_roots if root in stack]
    if unmatched:
        return rendered_roots[0]
    if text.rstrip().endswith(_SKILL_CONTENT_CLOSING) and rendered_roots:
        return rendered_roots[0]
    return next((root for root in rendered_roots if root in completed_roots), None)


__all__ = [
    "SKILL_INVOCATION_DISPLAY_BLOCK",
    "invoked_skill_names",
    "invoked_skill_payloads",
    "leading_skill_name",
    "mentioned_skill_names",
    "public_session_preview",
    "skill_invocation_display_names",
    "with_skill_invocation_display",
    "without_skill_invocation_display",
]
