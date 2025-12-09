from __future__ import annotations

from collections.abc import Sequence
import mimetypes
from pathlib import Path

from vibe.core.autocompletion.path_prompt import (
    PathPromptPayload,
    PathResource,
    build_path_prompt_payload,
)

DEFAULT_MAX_EMBED_BYTES = 256 * 1024

ResourceBlock = dict[str, str | None]


def render_path_prompt(
    message: str,
    *,
    base_dir: Path,
    max_embed_bytes: int | None = DEFAULT_MAX_EMBED_BYTES,
) -> str:
    payload = build_path_prompt_payload(message, base_dir=base_dir)
    blocks = _path_prompt_to_content_blocks(payload, max_embed_bytes=max_embed_bytes)
    return _content_blocks_to_prompt_text(blocks)


def _path_prompt_to_content_blocks(
    payload: PathPromptPayload, *, max_embed_bytes: int | None = DEFAULT_MAX_EMBED_BYTES
) -> list[ResourceBlock]:
    blocks: list[ResourceBlock] = [{"type": "text", "text": payload.prompt_text}]

    for resource in payload.resources:
        match resource.kind:
            case "file":
                embedded = _try_embed_text_resource(resource, max_embed_bytes)
                if embedded:
                    blocks.append(embedded)
                else:
                    blocks.append({
                        "type": "resource_link",
                        "uri": resource.path.as_uri(),
                        "name": resource.alias,
                    })
            case "directory":
                blocks.append({
                    "type": "resource_link",
                    "uri": resource.path.as_uri(),
                    "name": resource.alias,
                })

    return blocks


def _try_embed_text_resource(
    resource: PathResource, max_embed_bytes: int | None
) -> ResourceBlock | None:
    try:
        data = resource.path.read_bytes()
    except OSError:
        return None

    if max_embed_bytes is not None and len(data) > max_embed_bytes:
        return None

    if not _is_probably_text(resource, data):
        return None

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None

    return {"type": "resource", "uri": resource.path.as_uri(), "text": text}


def _content_blocks_to_prompt_text(blocks: Sequence[ResourceBlock]) -> str:
    parts = []

    for block in blocks:
        block_text = _format_content_block(block)
        if block_text is not None:
            parts.append(block_text)

    return "\n\n".join(parts)


def _format_content_block(block: ResourceBlock) -> str | None:
    match block.get("type"):
        case "text":
            return block.get("text") or ""

        case "resource":
            block_content = block.get("text") or ""
            fence = "```"
            return f"{block.get('uri')}\n{fence}\n{block_content}\n{fence}"

        case "resource_link":
            fields = {
                "uri": block.get("uri"),
                "name": block.get("name"),
                "title": block.get("title"),
                "description": block.get("description"),
                "mimeType": block.get("mimeType"),
                "size": block.get("size"),
            }
            parts = [
                f"{k}: {v}"
                for k, v in fields.items()
                if v is not None and (v or isinstance(v, (int, float)))
            ]
            return "\n".join(parts)

        case _:
            return None


BINARY_MIME_PREFIXES = (
    "audio/",
    "image/",
    "video/",
    "application/zip",
    "application/x-zip-compressed",
)


def _is_probably_text(path: PathResource, data: bytes) -> bool:
    mime_guess, _ = mimetypes.guess_type(path.path.name)
    if mime_guess and mime_guess.startswith(BINARY_MIME_PREFIXES):
        return False

    if not data:
        return True
    if b"\x00" in data:
        return False

    DEL_CODE = 127
    NON_PRINTABLE_MAX_PROPORTION = 0.1
    NON_PRINTABLE_MAX_CODE = 31
    NON_PRINTABLE_EXCEPTIONS = [9, 10, 11, 12]
    non_text = sum(
        1
        for b in data
        if b <= NON_PRINTABLE_MAX_CODE
        and b not in NON_PRINTABLE_EXCEPTIONS
        or b == DEL_CODE
    )
    return (non_text / len(data)) < NON_PRINTABLE_MAX_PROPORTION
