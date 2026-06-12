"""Drop malformed mouse reports before Textual parses them.

VS Code's integrated terminal can emit malformed SGR mouse reports such as
``\\x1b[<32;NaN;NaNM`` (extended mouse buttons during a focus/tab change).
Textual's mouse regex requires numeric coordinates, so these fall through and
get reissued as random characters in the input box. They can never be
legitimate user input, so we strip them before the parser sees them. Valid
mouse reports (numeric coordinates) are left untouched.
"""

from __future__ import annotations

from collections.abc import Iterable
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


def strip_malformed_mouse(data: str) -> str:
    return _MALFORMED_MOUSE.sub("", data)


class FilteringXTermParser(XTermParser):
    def feed(self, data: str) -> Iterable[Message]:
        filtered = strip_malformed_mouse(data)
        # An empty `data` is the driver's EOF signal and must reach the base
        # parser. But if a non-empty chunk was *entirely* noise, feeding the
        # resulting "" would wrongly trip EOF, so we yield nothing instead.
        if data and not filtered:
            return ()
        return super().feed(filtered)


def patch_driver_parser(driver_class: type[Driver]) -> None:
    # Replace the driver's XTermParser with our filtering subclass.
    namespace = sys.modules[driver_class.__module__].__dict__
    if "XTermParser" in namespace:
        namespace["XTermParser"] = FilteringXTermParser
