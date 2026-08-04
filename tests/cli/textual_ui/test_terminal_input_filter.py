from __future__ import annotations

import pytest
from textual import events

from vibe.cli.textual_ui import terminal_input_filter
from vibe.cli.textual_ui.terminal_input_filter import (
    _ENABLE_SGR_MOUSE,
    FilteringXTermParser,
    filter_input,
    patch_driver_parser,
    strip_malformed_mouse,
    strip_terminal_reports,
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


# OSC colour reports the terminal sends back in reply to Rich/Textual's colour
# queries. They must never reach the input box.
COLOR_REPORTS = [
    "\x1b]11;rgb:1e1e/1e1e/2e2e\x1b\\",  # background, ST terminator
    "\x1b]10;rgb:ffff/ffff/ffff\x07",  # foreground, BEL terminator
    "\x1b]12;rgb:c0c0/c0c0/c0c0\x1b\\",  # cursor colour
]


@pytest.mark.parametrize("seq", COLOR_REPORTS)
def test_strip_removes_osc_color_reports(seq: str) -> None:
    assert strip_terminal_reports(seq) == ""


def test_strip_removes_repeated_color_reports_keeping_real_key() -> None:
    report = "\x1b]11;rgb:1e1e/1e1e/2e2e\x1b\\"
    assert filter_input(report * 5 + "\x1b[A") == "\x1b[A"


@pytest.mark.parametrize("seq", PRESERVED)
def test_color_report_filter_preserves_real_input(seq: str) -> None:
    assert strip_terminal_reports(seq) == seq


def test_parser_emits_no_keys_for_color_report() -> None:
    seq = "\x1b]11;rgb:1e1e/1e1e/2e2e\x1b\\"
    tokens = list(FilteringXTermParser().feed(seq))
    assert [t for t in tokens if isinstance(t, events.Key) and t.character] == []


@pytest.mark.parametrize("split_at", range(1, len(COLOR_REPORTS[0])))
def test_parser_buffers_color_report_across_input_chunks(split_at: int) -> None:
    report = COLOR_REPORTS[0]
    parser = FilteringXTermParser()

    assert list(parser.feed(report[:split_at])) == []
    tokens = list(parser.feed(report[split_at:] + "\x1b[A"))

    assert [t for t in tokens if isinstance(t, events.Key) and t.character] == []
    assert any(isinstance(t, events.Key) and t.key == "up" for t in tokens)


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


# A legacy X10 mouse report. After tolerant decoding a high-column coordinate
# byte becomes U+FFFD, but the `ESC [ M` introducer is intact.
X10_MOUSE_REPORT = "\x1b[M@\ufffdC"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    written: list[str] = []

    def record(sequence: str) -> bool:
        written.append(sequence)
        return True

    monkeypatch.setattr(terminal_input_filter, "_write_to_terminal", record)
    return written


def test_x10_report_re_requests_sgr_mouse(sent: list[str]) -> None:
    FilteringXTermParser().feed(X10_MOUSE_REPORT)
    assert sent == [_ENABLE_SGR_MOUSE]


def test_sgr_re_request_sent_only_once(sent: list[str]) -> None:
    parser = FilteringXTermParser()
    parser.feed(X10_MOUSE_REPORT)
    parser.feed(X10_MOUSE_REPORT)
    assert sent == [_ENABLE_SGR_MOUSE]


def test_sgr_report_rearms_re_request(sent: list[str]) -> None:
    parser = FilteringXTermParser()
    parser.feed(X10_MOUSE_REPORT)
    # Terminal switched to SGR; a later reattach must trigger a fresh request.
    parser.feed("\x1b[<0;5;5M")
    parser.feed(X10_MOUSE_REPORT)
    assert sent == [_ENABLE_SGR_MOUSE, _ENABLE_SGR_MOUSE]


def test_no_sgr_re_request_for_ordinary_input(sent: list[str]) -> None:
    FilteringXTermParser().feed("\x1b[A")
    assert sent == []


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
