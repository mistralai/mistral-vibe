from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
import os
import re
import sys
from typing import Any

from textual.theme import Theme

try:
    import select
    import termios

    _UNIX_AVAILABLE = True
except ImportError:
    select = None  # type: ignore[assignment]
    termios: Any = None
    _UNIX_AVAILABLE = False

TERMINAL_THEME_NAME = "terminal"

_LUMINANCE_THRESHOLD = 0.5
_RGB_16BIT_LEN = 4
_RGB_8BIT_LEN = 2

# OSC codes for terminal colors
_OSC_FOREGROUND = "10"
_OSC_BACKGROUND = "11"

# ANSI color indices (0-15) mapped to field names
_ANSI_COLORS = (
    "black",
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "white",
    "bright_black",
    "bright_red",
    "bright_green",
    "bright_yellow",
    "bright_blue",
    "bright_magenta",
    "bright_cyan",
    "bright_white",
)

# DA1 (Primary Device Attributes) query - used to detect unsupported terminals
# Terminals respond to DA1 even if they don't support OSC color queries
_DA1_QUERY = b"\x1b[c"
_DA1_RESPONSE_PREFIX = b"\x1b[?"

_OSC_RESPONSE_RE = re.compile(
    rb"\x1b\](10|11|4;\d+);rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)(?:\x1b\\|\x07)"
)


@dataclass
class TerminalColors:
    foreground: str | None = None
    background: str | None = None
    black: str | None = None
    red: str | None = None
    green: str | None = None
    yellow: str | None = None
    blue: str | None = None
    magenta: str | None = None
    cyan: str | None = None
    white: str | None = None
    bright_black: str | None = None
    bright_red: str | None = None
    bright_green: str | None = None
    bright_yellow: str | None = None
    bright_blue: str | None = None
    bright_magenta: str | None = None
    bright_cyan: str | None = None
    bright_white: str | None = None

    def is_complete(self) -> bool:
        return all(getattr(self, f.name) is not None for f in fields(self))


def _build_osc_query(code: str) -> bytes:
    """Build an OSC query: ESC ] <code> ; ? ST"""
    return f"\x1b]{code};?\x1b\\".encode()


def _build_color_queries() -> tuple[bytes, dict[bytes, str]]:
    """Build all OSC color queries and the mapping from OSC codes to field names."""
    queries = bytearray()
    osc_to_field: dict[bytes, str] = {}

    # Foreground and background
    queries.extend(_build_osc_query(_OSC_FOREGROUND))
    queries.extend(_build_osc_query(_OSC_BACKGROUND))
    osc_to_field[_OSC_FOREGROUND.encode()] = "foreground"
    osc_to_field[_OSC_BACKGROUND.encode()] = "background"

    # ANSI colors 0-15
    for i, name in enumerate(_ANSI_COLORS):
        code = f"4;{i}"
        queries.extend(_build_osc_query(code))
        osc_to_field[code.encode()] = name

    return bytes(queries), osc_to_field


_COLOR_QUERIES, _OSC_TO_FIELD = _build_color_queries()


@contextmanager
def _raw_mode(fd: int) -> Iterator[None]:
    """Context manager to temporarily set terminal to raw mode."""
    assert termios is not None  # Only called on Unix so typing doesn't freak out
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        yield
        return

    try:
        new_settings = termios.tcgetattr(fd)
        new_settings[3] &= ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(fd, termios.TCSADRAIN, new_settings)
        yield
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except termios.error:
            pass


def _parse_rgb(r_hex: bytes, g_hex: bytes, b_hex: bytes) -> str | None:
    """Parse RGB hex values to a #rrggbb string.

    Terminals return either 16-bit (4 hex chars) or 8-bit (2 hex chars) per channel.
    """
    try:
        if len(r_hex) == _RGB_16BIT_LEN:
            r, g, b = int(r_hex[:2], 16), int(g_hex[:2], 16), int(b_hex[:2], 16)
        elif len(r_hex) == _RGB_8BIT_LEN:
            r, g, b = int(r_hex, 16), int(g_hex, 16), int(b_hex, 16)
        else:
            return None
        return f"#{r:02x}{g:02x}{b:02x}"
    except ValueError:
        return None


def _read_responses(fd: int, timeout: float = 1.0) -> bytes:
    """Read terminal responses until DA1 response or timeout.

    Uses the DA1 trick: we send color queries followed by DA1. Since terminals
    respond in order, receiving the DA1 response means all color responses
    (if any) have been received.
    """
    assert select is not None  # Only called on Unix so typing doesn't freak out
    response = bytearray()
    while True:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        response.extend(chunk)
        # DA1 response received - we have all the color responses
        if _DA1_RESPONSE_PREFIX in response:
            break
    return bytes(response)


def _parse_osc_responses(response: bytes) -> TerminalColors:
    colors = TerminalColors()
    for match in _OSC_RESPONSE_RE.finditer(response):
        osc_code, r_hex, g_hex, b_hex = match.groups()
        field = _OSC_TO_FIELD.get(osc_code)
        if field and (color := _parse_rgb(r_hex, g_hex, b_hex)):
            setattr(colors, field, color)
    return colors


def _query_terminal_colors() -> TerminalColors:
    if not _UNIX_AVAILABLE:
        return TerminalColors()

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return TerminalColors()

    fd = sys.stdin.fileno()

    try:
        with _raw_mode(fd):
            os.write(sys.stdout.fileno(), _COLOR_QUERIES + _DA1_QUERY)
            response = _read_responses(fd)
            return _parse_osc_responses(response)
    except OSError:
        return TerminalColors()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _adjust_brightness(hex_color: str, factor: float) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex(
        min(255, max(0, int(r * factor))),
        min(255, max(0, int(g * factor))),
        min(255, max(0, int(b * factor))),
    )


def _blend(c1: str, c2: str, ratio: float = 0.5) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(
        int(r1 * (1 - ratio) + r2 * ratio),
        int(g1 * (1 - ratio) + g2 * ratio),
        int(b1 * (1 - ratio) + b2 * ratio),
    )


def _luminance(hex_color: str) -> float:
    """Calculate perceived luminance (0-1) using ITU-R BT.601 coefficients."""
    r, g, b = _hex_to_rgb(hex_color)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def capture_terminal_theme() -> Theme | None:
    colors = _query_terminal_colors()

    if not colors.background or not colors.foreground:
        return None

    is_dark = _luminance(colors.background) < _LUMINANCE_THRESHOLD
    fg = colors.foreground
    bg = colors.background

    surface = _adjust_brightness(bg, 1.15 if is_dark else 0.95)
    panel = _blend(bg, surface)

    return Theme(
        name=TERMINAL_THEME_NAME,
        primary=colors.blue or fg,
        secondary=colors.cyan or fg,
        warning=colors.yellow or fg,
        error=colors.red or fg,
        success=colors.green or fg,
        accent=colors.magenta or fg,
        foreground=fg,
        background=bg,
        surface=surface,
        panel=panel,
        dark=is_dark,
    )
