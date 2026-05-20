from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from vibe.core.session.title_format import MentionSegment, TextSegment, TitleSegment


@dataclass(frozen=True, slots=True)
class PathResource:
    path: Path
    alias: str
    kind: Literal["file", "folder"]


@dataclass(frozen=True, slots=True)
class PathPromptPayload:
    display_text: str
    prompt_text: str
    resources: list[PathResource]
    all_resources: list[PathResource]


def build_path_prompt_payload(
    message: str, *, base_dir: Path | None = None
) -> PathPromptPayload:
    if not message:
        return PathPromptPayload(message, message, [], [])

    resolved_base = (base_dir or Path.cwd()).resolve()
    prompt_parts: list[str] = []
    resources: list[PathResource] = []
    pos = 0

    while pos < len(message):
        if _is_path_anchor(message, pos):
            candidate, new_pos = _extract_candidate(message, pos + 1)
            if candidate and (resource := _to_resource(candidate, resolved_base)):
                resources.append(resource)
                prompt_parts.append(candidate)
                pos = new_pos
                continue

        prompt_parts.append(message[pos])
        pos += 1

    prompt_text = "".join(prompt_parts)
    unique_resources = _dedupe_resources(resources)
    return PathPromptPayload(message, prompt_text, unique_resources, resources)


def _is_path_anchor(message: str, pos: int) -> bool:
    if message[pos] != "@":
        return False
    if pos == 0:
        return True
    return not (message[pos - 1].isalnum() or message[pos - 1] == "_")


def _extract_candidate(message: str, start: int) -> tuple[str | None, int]:
    if start >= len(message):
        return None, start

    quote = message[start]
    if quote in {"'", '"'}:
        end_quote = message.find(quote, start + 1)
        if end_quote == -1:
            return None, start
        return message[start + 1 : end_quote], end_quote + 1

    end = start
    while end < len(message) and _is_path_char(message[end]):
        end += 1

    if end == start:
        return None, start

    return message[start:end], end


def _is_path_char(char: str) -> bool:
    return char.isalnum() or char in "._/\\-()[]{}"


def _to_resource(candidate: str, base_dir: Path) -> PathResource | None:
    if not candidate:
        return None

    candidate_path = Path(candidate)
    resolved = (
        candidate_path if candidate_path.is_absolute() else base_dir / candidate_path
    )
    resolved = resolved.resolve()

    if not resolved.exists():
        return None

    kind = "folder" if resolved.is_dir() else "file"
    return PathResource(path=resolved, alias=candidate, kind=kind)


def _dedupe_resources(resources: list[PathResource]) -> list[PathResource]:
    seen: set[Path] = set()
    unique: list[PathResource] = []
    for resource in resources:
        if resource.path in seen:
            continue
        seen.add(resource.path)
        unique.append(resource)
    return unique


def build_title_segments(
    message: str, *, base_dir: Path | None = None
) -> list[TitleSegment]:
    if not message:
        return []

    resolved_base = (base_dir or Path.cwd()).resolve()
    segments: list[TitleSegment] = []
    text_buf: list[str] = []
    pos = 0

    def flush_text() -> None:
        if text_buf:
            segments.append(TextSegment(text="".join(text_buf)))
            text_buf.clear()

    while pos < len(message):
        if _is_path_anchor(message, pos):
            candidate, new_pos = _extract_candidate(message, pos + 1)
            if candidate and (resource := _to_resource(candidate, resolved_base)):
                flush_text()
                segments.append(MentionSegment(name=resource.path.name))
                pos = new_pos
                continue

        text_buf.append(message[pos])
        pos += 1

    flush_text()
    return segments
