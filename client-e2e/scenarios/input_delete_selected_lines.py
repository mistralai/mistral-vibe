from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SELECT_LEFT = "\x1b[1;2D"
_DELETE_LINE = "\x1b[107;6u"

timeline: Timeline = ["one\ntwo\nthree\nfour", f"\x01{_SELECT_LEFT * 8}{_DELETE_LINE}"]
