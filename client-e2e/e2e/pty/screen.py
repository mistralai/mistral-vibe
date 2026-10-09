"""Emulate terminal output while retaining visible OSC 8 and dim metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property, lru_cache
from itertools import product
from typing import NamedTuple, cast
import unicodedata

import pyte
from pyte import modes
from wcwidth import wcwidth

_OSC8_START = b"\x1b]8;"
_SGR_DIM = 2
_SGR_RESET_DIM = {0, 22}
_SGR_EXTENDED_COLOR = {38, 48, 58}
_SGR_TRUECOLOR = 2
_VS16 = "\ufe0f"
# Marks pyte treats as unprintable and drops, including the keycap U+20E3.
_EMOJI_MARK_CATEGORIES = {"Mn", "Me", "Cf"}


def _emoji_mark_end(data: str, index: int) -> int:
    while index < len(data) and _is_emoji_mark(data[index]):
        index += 1
    return index


def _is_emoji_mark(char: str) -> bool:
    return wcwidth(char) == 0 and unicodedata.category(char) in _EMOJI_MARK_CATEGORIES


class Attr(StrEnum):
    """A visible text attribute."""

    BOLD = "bold"
    DIM = "dim"
    ITALICS = "italics"
    UNDERSCORE = "underscore"
    STRIKETHROUGH = "strikethrough"
    BLINK = "blink"
    REVERSE = "reverse"


# Attributes read straight off the pyte char; DIM and REVERSE are derived by `_cell`.
_PLAIN_ATTRS = (
    Attr.BOLD,
    Attr.ITALICS,
    Attr.UNDERSCORE,
    Attr.STRIKETHROUGH,
    Attr.BLINK,
)

# Every combination of the plain attribute flags, so `_cell` never rebuilds a set.
_PLAIN_ATTR_SETS = {
    flags: frozenset(attr for attr, on in zip(_PLAIN_ATTRS, flags, strict=True) if on)
    for flags in product((False, True), repeat=len(_PLAIN_ATTRS))
}


@dataclass(frozen=True)
class Cell:
    """One rendered terminal cell."""

    character: str
    foreground: str
    background: str
    attrs: frozenset[Attr]
    hyperlink: str | None = None


@dataclass(frozen=True)
class Snapshot:
    """An immutable terminal grid captured at a labeled point."""

    label: str
    cells: tuple[tuple[Cell, ...], ...]
    clipboard: str | None = None
    title: str = ""
    cursor: tuple[int, int] | None = None
    """Terminal cursor as ``(row, column)``, where IME and dead-key previews draw."""

    @cached_property
    def rows(self) -> tuple[str, ...]:
        """Derive rendered text rows from the authoritative cells."""
        return tuple(
            "".join(cell.character for cell in row).rstrip() for row in self.cells
        )


class _TrackedChar(NamedTuple):
    data: str = " "
    fg: str = "default"
    bg: str = "default"
    bold: bool = False
    italics: bool = False
    underscore: bool = False
    strikethrough: bool = False
    reverse: bool = False
    blink: bool = False
    dimmed: bool = False
    hyperlink: str | None = None


# Keyed on the char, a hashable NamedTuple, so the grid's few distinct styles pay once.
@lru_cache(maxsize=16384)
def _cell(character: pyte.screens.Char) -> Cell:
    reverse = character.reverse
    foreground, background = (
        (character.bg, character.fg) if reverse else (character.fg, character.bg)
    )
    attrs = _PLAIN_ATTR_SETS[
        (
            character.bold,
            character.italics,
            character.underscore,
            character.strikethrough,
            character.blink,
        )
    ]
    # A swap cannot express reverse when both colors resolve the same (ANSI defaults).
    if reverse and foreground == background:
        attrs |= {Attr.REVERSE}
    tracked = isinstance(character, _TrackedChar)
    if tracked and character.dimmed:
        attrs |= {Attr.DIM}
    hyperlink = character.hyperlink if tracked else None
    return Cell(character.data, foreground, background, attrs, hyperlink)


class _TrackedScreen(pyte.Screen):
    """A pyte screen whose characters retain dim and OSC 8 state."""

    @property
    def default_char(self) -> pyte.screens.Char:
        return cast(pyte.screens.Char, _TrackedChar(reverse=modes.DECSCNM in self.mode))

    def reset(self) -> None:
        super().reset()
        self.cursor.attrs = self.default_char

    def draw(self, data: str) -> None:
        # pyte stops at VS16 and drops the rest of the run; terminals keep the text that follows.
        index = self._take_leading_emoji_marks(data)
        while index < len(data):
            vs = data.find(_VS16, index)
            if vs < 0:
                super().draw(data[index:])
                return
            if vs > index:
                super().draw(data[index:vs])
            end = _emoji_mark_end(data, vs + 1)
            self._widen_previous_glyph(data[vs:end])
            index = end

    def _take_leading_emoji_marks(self, data: str) -> int:
        end = _emoji_mark_end(data, 0)
        if not end:
            return 0
        marks = data[:end]
        if _VS16 in marks:
            self._widen_previous_glyph(marks)
        else:
            self._attach_emoji_marks(marks)
        return end

    def _attach_emoji_marks(self, marks: str) -> None:
        x = self.cursor.x
        if not x:
            return
        line = self.buffer[self.cursor.y]
        target = x - 1
        # A continuation stub is empty; the mark belongs on the base glyph before it.
        if not line[target].data and target:
            target -= 1
        if not line[target].data:
            return
        line[target] = line[target]._replace(data=line[target].data + marks)

    def _widen_previous_glyph(self, tail: str) -> None:
        x = self.cursor.x
        if not x or x >= self.columns:
            return
        line = self.buffer[self.cursor.y]
        previous = line[x - 1]
        if not previous.data or _VS16 in previous.data:
            return
        line[x - 1] = previous._replace(data=previous.data + tail)
        line[x] = self.cursor.attrs._replace(data="")
        self.cursor.x = x + 1

    def select_graphic_rendition(self, *attrs: int) -> None:
        dimmed = self._cursor_attrs().dimmed
        params = attrs or (0,)
        index = 0
        while index < len(params):
            attr = params[index]
            if attr in _SGR_RESET_DIM:
                dimmed = False
            elif attr == _SGR_DIM:
                dimmed = True
            if attr in _SGR_EXTENDED_COLOR and index + 1 < len(params):
                index += 5 if params[index + 1] == _SGR_TRUECOLOR else 3
            else:
                index += 1
        super().select_graphic_rendition(*attrs)
        self.cursor.attrs = cast(
            pyte.screens.Char, self._cursor_attrs()._replace(dimmed=dimmed)
        )

    def set_hyperlink(self, target: str | None) -> None:
        self.cursor.attrs = cast(
            pyte.screens.Char, self._cursor_attrs()._replace(hyperlink=target)
        )

    def _cursor_attrs(self) -> _TrackedChar:
        attrs = self.cursor.attrs
        assert isinstance(attrs, _TrackedChar)
        return attrs


class Terminal:
    """A pyte emulator with explicit terminal dimensions."""

    def __init__(self, rows: int, columns: int) -> None:
        self._rows = rows
        self._columns = columns
        self._screen = _TrackedScreen(columns, rows)
        self._stream = pyte.ByteStream(self._screen)
        self._pending = bytearray()

    def feed(self, data: bytes) -> None:
        self._pending.extend(data)
        while self._pending:
            start = self._pending.find(_OSC8_START)
            if start == -1:
                keep = _osc8_prefix_length(self._pending)
                content = self._pending[:-keep] if keep else self._pending
                if content:
                    self._stream.feed(bytes(content))
                    del self._pending[: len(content)]
                return
            if start:
                self._stream.feed(bytes(self._pending[:start]))
                del self._pending[:start]
            end = _osc_terminator(self._pending)
            if end is None:
                return
            payload = self._pending[len(_OSC8_START) : end]
            if (separator := payload.find(b";")) != -1:
                target = payload[separator + 1 :].decode("utf-8", "replace")
                self._screen.set_hyperlink(target or None)
            terminator_length = _osc_terminator_length(self._pending, end)
            del self._pending[: end + terminator_length]

    def resize(self, rows: int, columns: int) -> None:
        """Resize the emulated grid with the PTY."""
        self._screen.resize(lines=rows, columns=columns)
        self._rows = rows
        self._columns = columns

    def snapshot(self, label: str, clipboard: str | None = None) -> Snapshot:
        screen = self._screen
        cells = tuple(
            tuple(_cell(screen.buffer[y][x]) for x in range(self._columns))
            for y in range(self._rows)
        )
        cursor = (screen.cursor.y, screen.cursor.x)
        return Snapshot(label, cells, clipboard, screen.title, cursor)


def _osc8_prefix_length(data: bytearray) -> int:
    for size in range(min(len(data), len(_OSC8_START) - 1), 0, -1):
        if data[-size:] == _OSC8_START[:size]:
            return size
    return 0


def _osc_terminator(data: bytearray) -> int | None:
    bell = data.find(b"\x07", len(_OSC8_START))
    string_terminator = data.find(b"\x1b\\", len(_OSC8_START))
    if bell == -1:
        return None if string_terminator == -1 else string_terminator
    return bell if string_terminator == -1 else min(bell, string_terminator)


def _osc_terminator_length(data: bytearray, index: int) -> int:
    return 2 if data[index : index + 2] == b"\x1b\\" else 1
