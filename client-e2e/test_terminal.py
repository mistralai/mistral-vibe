"""Tests for generic terminal emulation."""

from __future__ import annotations

from e2e.pty.capture import _ClipboardCapture
from e2e.pty.screen import Attr, Terminal
import pyte

ROWS, COLUMNS = 40, 120


def test_dimmed_text_is_retained_without_changing_the_grid() -> None:
    terminal = Terminal(ROWS, COLUMNS)
    terminal.feed(b"before \x1b[2mdim\x1b[22m after")
    snapshot = terminal.snapshot("dimmed")

    assert snapshot.rows == ("before dim after",) + ("",) * 39
    assert Attr.DIM in snapshot.cells[0][7].attrs
    assert Attr.DIM not in snapshot.cells[0][6].attrs


def test_truecolor_sgr_does_not_set_dimmed_text() -> None:
    terminal = Terminal(ROWS, COLUMNS)
    terminal.feed(b"\x1b[38;2;255;130;5morange")

    assert Attr.DIM not in terminal.snapshot("truecolor").cells[0][0].attrs


def test_osc8_target_is_retained_across_feed_boundaries() -> None:
    terminal = Terminal(ROWS, COLUMNS)
    terminal.feed(b"\x1b]8;id=docs;https://docs.")
    terminal.feed(b"example\x1b\\docs\x1b]8;;\x1b\\!")
    snapshot = terminal.snapshot("linked")

    assert snapshot.rows == ("docs!",) + ("",) * 39
    assert snapshot.cells[0][0].hyperlink == "https://docs.example"
    assert snapshot.cells[0][3].hyperlink == "https://docs.example"
    assert snapshot.cells[0][4].hyperlink is None


def test_osc8_bell_terminator_is_retained() -> None:
    terminal = Terminal(ROWS, COLUMNS)
    terminal.feed(b"\x1b]8;;https://docs.example\x07docs\x1b]8;;\x07")

    assert terminal.snapshot("linked").cells[0][0].hyperlink == "https://docs.example"


def test_osc52_clipboard_is_retained_across_feed_boundaries() -> None:
    clipboard = _ClipboardCapture()
    clipboard.feed(b"before\x1b]52;c;Y29weSB")
    clipboard.feed(b"tZQ==\x07after")

    assert clipboard.text == "copy me"


def test_metadata_tracking_preserves_pyte_rendering() -> None:
    chunks = (
        b"\x1b[2J\x1b[Hplain ",
        b"\x1b[2mdim\x1b[22m\x1b]8;;https://docs.example\x1b\\ link\x1b]8;;\x1b\\",
        b"\x1b[1;7H\x1b[4h+\x1b[4l\x1b[2P\x1b[2;1Hsecond\r\n",
    )
    terminal = Terminal(ROWS, COLUMNS)
    screen = pyte.Screen(COLUMNS, ROWS)
    stream = pyte.ByteStream(screen)

    for chunk in chunks:
        terminal.feed(chunk)
        stream.feed(chunk)

    assert terminal.snapshot("tracked").rows == tuple(
        row.rstrip() for row in screen.display
    )
