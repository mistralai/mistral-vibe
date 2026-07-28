"""Harden Textual's input path against malformed terminal bytes.

Three defenses, applied by patching the platform driver's module globals. Each
works around a Textual upstream limitation; remove the corresponding defense once
the linked fix ships.

1. Drop malformed SGR mouse reports (e.g. ``\\x1b[<32;NaN;NaNM``). VS Code's
   integrated terminal emits these when xterm.js geometry is briefly invalid
   during a focus/tab change. Textual's mouse regex requires numeric
   coordinates, so the report falls through to the keystroke fallback and gets
   typed into the focused input. We strip the ``\\x1b[<...M/m`` envelope when its
   parameters are non-numeric; valid reports (including negative SGR-Pixels
   coordinates) are left untouched.
   Upstream: https://github.com/Textualize/textual/issues/6573

2. Drop OSC colour *reports* such as ``\\x1b]11;rgb:1e1e/1e1e/2e2e\\x1b\\``.
   These are the terminal's replies to the OSC 10/11/12 colour queries
   Rich/Textual issue while rendering; they arrive on stdin but are never user
   input, so Textual otherwise spills them into the focused input box.

3. Re-request SGR mouse mode when a legacy X10 report arrives. On a terminal
   reattach (tmux reattach / VS Code window reload) the terminal reverts to the
   default X10 mouse encoding (``ESC [ M`` + raw ``column + 32`` bytes). Textual
   only parses SGR reports (``ESC [ <``); it consumes X10 reports and drops them,
   so the mouse silently stops tracking. Seeing an X10 report, we re-enable SGR
   mouse mode so the terminal switches back and tracking recovers.
   Upstream: https://github.com/Textualize/textual/issues/6668
"""

from __future__ import annotations

from collections.abc import Iterable
import os
import re
import sys

from textual._xterm_parser import XTermParser
from textual.driver import Driver
from textual.message import Message

# SGR mouse reports whose payload is not numeric (e.g. `NaN`). The negative
# lookahead allows digits, `;`, and `-` so that valid reports — including the
# negative coordinates Textual handles for SGR-Pixels — are left untouched, and
# only non-numeric junk like `NaN` is stripped.
_MALFORMED_MOUSE = re.compile(r"\x1b\[<(?![-0-9;]*[Mm])[^Mm]*[Mm]")

# OSC colour reports: ``ESC ] <ps> ; rgb:<hex>/<hex>/<hex> <ST>`` where the
# string terminator is BEL (``\x07``) or ``ESC \``.
_OSC_COLOR_REPORT = re.compile(r"\x1b\][0-9]{1,3};rgb:[0-9a-fA-F/]+(?:\x07|\x1b\\)")
_OSC_COLOR_PAYLOAD = frozenset("0123456789abcdefABCDEF/")
_MAX_OSC_PARAMETER_DIGITS = 3

_X10_MOUSE_INTRODUCER = "\x1b[M"
_SGR_MOUSE_INTRODUCER = "\x1b[<"
# Same sequence emitted by textual's driver in initial negotiation
_ENABLE_SGR_MOUSE = "\x1b[?1000h\x1b[?1003h\x1b[?1015h\x1b[?1006h"


def strip_malformed_mouse(data: str) -> str:
    return _MALFORMED_MOUSE.sub("", data)


def strip_terminal_reports(data: str) -> str:
    return _OSC_COLOR_REPORT.sub("", data)


def filter_input(data: str) -> str:
    return strip_terminal_reports(strip_malformed_mouse(data))


def _write_to_terminal(sequence: str) -> bool:
    """Write sequence of ascii bytes to terminal, return success boolean"""
    stream = sys.__stderr__
    if stream is None:
        return False
    try:
        os.write(stream.fileno(), sequence.encode("ascii"))
    except (OSError, ValueError):
        return False
    return True


def _is_partial_color_report(payload: str) -> bool:
    digit_count = 0
    while digit_count < len(payload) and payload[digit_count].isdigit():
        digit_count += 1
    if digit_count == 0 or digit_count > _MAX_OSC_PARAMETER_DIGITS:
        return not payload
    if digit_count == len(payload):
        return True

    expected = ";rgb:"
    remainder = payload[digit_count:]
    shared_length = min(len(remainder), len(expected))
    if remainder[:shared_length] != expected[:shared_length]:
        return False
    if len(remainder) <= len(expected):
        return True

    color = remainder[len(expected) :]
    if color.endswith("\x1b"):
        color = color[:-1]
    return bool(color) and all(char in _OSC_COLOR_PAYLOAD for char in color)


def _split_partial_color_report(data: str) -> tuple[str, str]:
    start = data.rfind("\x1b]")
    if start >= 0 and _is_partial_color_report(data[start + 2 :]):
        return data[:start], data[start:]
    if data.endswith("\x1b"):
        return data[:-1], "\x1b"
    return data, ""


class FilteringXTermParser(XTermParser):
    def __init__(self, debug: bool = False) -> None:
        super().__init__(debug)
        self._sgr_mouse_requested = False
        self._pending_color_report = ""

    def feed(self, data: str) -> Iterable[Message]:
        if not data:
            self._pending_color_report = ""
            return super().feed(data)

        self._maybe_restore_sgr_mouse(data)
        data = self._pending_color_report + data
        self._pending_color_report = ""
        filtered = filter_input(data)
        filtered, self._pending_color_report = _split_partial_color_report(filtered)
        # An empty `data` is the driver's EOF signal and must reach the base
        # parser. But if a non-empty chunk was *entirely* noise, feeding the
        # resulting "" would wrongly trip EOF, so we yield nothing instead.
        if not filtered:
            return ()
        return super().feed(filtered)

    def _maybe_restore_sgr_mouse(self, data: str) -> None:
        # SGR reports mean the terminal is already in SGR mode; re-arm so the
        # next reattach can trigger another request.
        if _SGR_MOUSE_INTRODUCER in data:
            self._sgr_mouse_requested = False
            return
        if self._sgr_mouse_requested or _X10_MOUSE_INTRODUCER not in data:
            return
        if _write_to_terminal(_ENABLE_SGR_MOUSE):
            self._sgr_mouse_requested = True


def patch_driver_parser(driver_class: type[Driver]) -> None:
    # Replace the driver's XTermParser with our filtering subclass.
    namespace = sys.modules[driver_class.__module__].__dict__
    if "XTermParser" in namespace:
        namespace["XTermParser"] = FilteringXTermParser
