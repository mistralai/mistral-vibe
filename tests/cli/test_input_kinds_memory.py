from __future__ import annotations

from vibe.cli.commands import CommandRegistry
from vibe.cli.textual_ui.widgets.chat_input.input_kinds import (
    EmptyMemory,
    Memory,
    Prompt,
    classify,
)


def _classify(value: str):
    return classify(value, commands=CommandRegistry(), expand_skill=lambda _: None)


def test_hash_prefix_is_memory():
    result = _classify("# remember this")
    assert isinstance(result, Memory)
    assert result.note == "remember this"


def test_bare_hash_is_empty_memory():
    assert isinstance(_classify("#"), EmptyMemory)
    assert isinstance(_classify("#   "), EmptyMemory)


def test_plain_text_is_prompt():
    assert isinstance(_classify("hello"), Prompt)
