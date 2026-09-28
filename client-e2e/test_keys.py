"""Tests for generic terminal key splitting."""

from __future__ import annotations

from e2e.pty.capture import _batches
from e2e.pty.keys import split_keys


def test_event_only_steps_do_not_send_empty_key_batches() -> None:
    assert _batches("") == []


def test_split_keys_preserves_escape_sequences() -> None:
    assert split_keys("a\x1b[A\x1bb\r") == ["a", "\x1b[A", "\x1bb", "\r"]


def test_split_keys_keeps_bracketed_paste_atomic() -> None:
    assert split_keys("a\x1b[200~one\ntwo\x1b[201~b") == [
        "a",
        "\x1b[200~one\ntwo\x1b[201~",
        "b",
    ]
