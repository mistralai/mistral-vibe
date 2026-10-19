"""`d` arms a delete that moving, Esc, or the current session never carry out."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"
_UP = "\x1b[A"
_DOWN = "\x1b[B"

# The picker preselects the current session (…0001), which cannot be deleted;
# Up moves onto the saved one (…0003). No step sends `session/delete`.
request_methods = {"session/delete"}
timeline: Timeline = [
    "/resume",
    "\r",
    # On the current session `d` only explains it cannot be deleted.
    "d",
    # Moving away drops that feedback; `d` then asks for confirmation.
    _UP,
    "d",
    # Moving away drops the pending confirmation too.
    _DOWN,
    _UP,
    "d",
    # Enter does not resume a row waiting for its second `d`.
    "\r",
    # The first Esc only drops the confirmation; the second closes the picker.
    _ESCAPE,
    _ESCAPE,
]

capture_startup = False
settle_per_key = True
screen_contains = ("Resume cancelled.",)
