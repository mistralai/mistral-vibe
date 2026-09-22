from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "line23"
capture_startup = False
capture_steps = {2}
screen_contains = {"python": ("line22",), "rust": ("line22",)}

_WHEEL_UP_OVER_COMPOSER = "\x1b[<64;60;30M"
_CUT = "\x18"

timeline: Timeline = [
    "\n".join(f"line{index:02d}" for index in range(24)),
    _WHEEL_UP_OVER_COMPOSER,
    _CUT,
]
