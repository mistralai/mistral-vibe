from __future__ import annotations

from e2e.app_server.scenario import Timeline

_WHEEL_DOWN = "\x1b[<65;60;26M"
_WHEEL_UP = "\x1b[<64;60;26M"


timeline: Timeline = ["/", _WHEEL_DOWN * 3, _WHEEL_UP, _WHEEL_UP * 5]
