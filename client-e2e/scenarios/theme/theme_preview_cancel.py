"""Esc after previewing another theme restores the original one and writes nothing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

capture_startup = False
request_methods = {"config/reload"}
# Down previews atom-one-dark; Esc must bring ansi-dark back.
timeline: Timeline = ["/theme", "\r", "\x1b[B", _ESCAPE]
