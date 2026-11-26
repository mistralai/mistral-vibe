"""SGR mouse reports a scenario types: presses, drags and wheel steps."""

from __future__ import annotations


def click(column: int, row: int) -> str:
    """A left press and release at a 1-based terminal `column` and `row`, as SGR mouse reports."""
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


def wheel_down(column: int, row: int) -> str:
    """One wheel step down at a 1-based terminal `column` and `row`."""
    return f"\x1b[<65;{column};{row}M"


def drag(start: tuple[int, int], end: tuple[int, int]) -> str:
    """A left press at `start`, a move to `end` and a release there, each a 1-based `(column, row)`."""
    (x0, y0), (x1, y1) = start, end
    return f"\x1b[<0;{x0};{y0}M\x1b[<32;{x1};{y1}M\x1b[<0;{x1};{y1}m"
