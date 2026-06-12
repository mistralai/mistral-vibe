from __future__ import annotations

import pytest
from textual import events

from vibe.cli.textual_ui.terminal_input_filter import (
    FilteringXTermParser,
    patch_driver_parser,
    strip_malformed_mouse,
)

NOISE = [
    "\x1b[<32;NaN;NaNM",  # malformed SGR mouse (extended button on focus change)
    "\x1b[<35;NaN;NaNm",
]

PRESERVED = [
    "\x1b[A",  # arrow up
    "\x1b[15~",  # F5
    "\x1b[97u",  # plain kitty key 'a'
    "\x1b[<0;5;5M",  # valid mouse down
    "\x1b[<0;5;5m",  # valid mouse up
    "\x1b[<128;10;10M",  # valid extended-button mouse
    "\x1b[<0;-1;-1M",  # negative SGR-Pixels coords (Textual handles these)
    "\x1b[24;80R",  # cursor position report
    "\x1b[I",  # focus in
]


@pytest.mark.parametrize("seq", NOISE)
def test_strip_removes_malformed_mouse(seq: str) -> None:
    assert strip_malformed_mouse(seq) == ""


@pytest.mark.parametrize("seq", PRESERVED)
def test_strip_preserves_real_input(seq: str) -> None:
    assert strip_malformed_mouse(seq) == seq


def test_strip_keeps_real_key_after_noise() -> None:
    assert strip_malformed_mouse("\x1b[<32;NaN;NaNM\x1b[A") == "\x1b[A"


@pytest.mark.parametrize("seq", NOISE)
def test_parser_emits_no_keys_for_noise(seq: str) -> None:
    tokens = list(FilteringXTermParser().feed(seq))
    assert [t for t in tokens if isinstance(t, events.Key) and t.character] == []


def test_parser_still_emits_key_for_arrow() -> None:
    tokens = list(FilteringXTermParser().feed("\x1b[A"))
    assert any(isinstance(t, events.Key) and t.key == "up" for t in tokens)


def test_all_noise_chunk_does_not_trip_eof() -> None:
    parser = FilteringXTermParser()
    assert list(parser.feed("\x1b[<32;NaN;NaNM")) == []
    # The parser must still be alive for the next real chunk.
    assert any(
        isinstance(t, events.Key) and t.key == "up" for t in parser.feed("\x1b[A")
    )


def test_patch_driver_parser_rebinds_module_global() -> None:
    import sys

    from textual.drivers.linux_driver import LinuxDriver

    namespace = sys.modules[LinuxDriver.__module__].__dict__
    original = namespace["XTermParser"]
    try:
        patch_driver_parser(LinuxDriver)
        assert namespace["XTermParser"] is FilteringXTermParser
    finally:
        namespace["XTermParser"] = original
