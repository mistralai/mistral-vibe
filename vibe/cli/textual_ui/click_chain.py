"""Multi-click chain window shared by the transcript and the composer."""

from __future__ import annotations

import math

from textual._context import NoActiveAppError
from textual.widget import Widget

from vibe.cli.textual_ui.replay_harness import replaying

DEFAULT_THRESHOLD = 0.5


def threshold(widget: Widget) -> float:
    # Replay presses land at machine speed, so a wall-clock window would make the
    # granularity depend on load. Hold it open and rely on the same-cell test.
    if replaying():
        return math.inf
    try:
        return widget.app.CLICK_CHAIN_TIME_THRESHOLD
    except NoActiveAppError:
        return DEFAULT_THRESHOLD
