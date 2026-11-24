"""The text of raw content blocks, as runtimes send them."""

from __future__ import annotations

from typing import cast


def text_block_texts(content: object) -> list[str]:
    """The texts of a raw content block list's text blocks; none from anything else."""
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in cast(list[object], content):
        if not isinstance(block, dict):
            continue
        fields = cast(dict[str, object], block)
        text = fields.get("text")
        if fields.get("type") == "text" and isinstance(text, str):
            texts.append(text)
    return texts


__all__ = ["text_block_texts"]
