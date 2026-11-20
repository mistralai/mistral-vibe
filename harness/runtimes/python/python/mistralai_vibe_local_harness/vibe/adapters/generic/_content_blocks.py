"""Content block helpers shared by the OpenAI-compatible chat adapters."""

from __future__ import annotations

from typing import Any


def split_content_blocks(content: str | list[Any]) -> tuple[str | None, str | None]:
    """Split OpenAI-style content blocks into (text, reasoning).

    Text blocks go to the text side and ``thinking`` blocks (Mistral-style, with
    nested text chunks) to the reasoning side. Blocks carrying no text (images,
    unknown types) are dropped. Parts are concatenated without separator: they
    are fragments of a single message or streaming delta.
    """
    if isinstance(content, str):
        return content or None, None

    text_parts: list[str] = []
    thinking_parts: list[str] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "thinking":
            inner_blocks = block.get("thinking") or []
            if isinstance(inner_blocks, str):
                thinking_parts.append(inner_blocks)
                continue
            for inner in inner_blocks:
                if isinstance(inner, dict) and isinstance(inner.get("text"), str):
                    thinking_parts.append(inner["text"])
                elif isinstance(inner, str):
                    thinking_parts.append(inner)
        elif isinstance(block.get("text"), str):
            text_parts.append(block["text"])

    return "".join(text_parts) or None, "".join(thinking_parts) or None
