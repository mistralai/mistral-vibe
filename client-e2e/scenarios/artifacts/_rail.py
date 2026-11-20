"""The PR chip on the idle rail: Ctrl+X then `p` opens the PR window, or a click on the chip."""

from __future__ import annotations

from e2e.app_server.config import COLUMNS, ROWS

CTRL_X = "\x18"
ESCAPE = "\x1b[27u"
OPEN_PRS = [CTRL_X, "p"]
# The rail sits right above the input box and the bar; SGR rows are 1-indexed.
RAIL_ROW = ROWS - 6
FOOTER = "/test/workdir".ljust(COLUMNS - len("ctx        0/600k")) + "ctx        0/600k"


def chip_column(chip: str) -> int:
    """The 1-indexed column of the chip's middle; the rail ends one cell before the edge."""
    return COLUMNS - len(chip) + len(chip) // 2


def hover_chip(chip: str) -> str:
    return f"\x1b[<35;{chip_column(chip)};{RAIL_ROW}M"


def click_chip(chip: str, offset: int = 0) -> str:
    column = chip_column(chip) + offset
    return f"\x1b[<0;{column};{RAIL_ROW}M\x1b[<0;{column};{RAIL_ROW}m"
