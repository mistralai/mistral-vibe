from __future__ import annotations

from collections.abc import Sequence

# Cool ramp, deliberately clear of the warm Mistral palette used elsewhere in the
# chrome, so smart auto does not read as a warning or an error state.
GLOW_COLORS: tuple[str, ...] = ("#5B3FD9", "#7B5CFF", "#A78BFA", "#C4B5FD")

GLOW_INTERVAL_SECONDS = 0.08
STEPS_PER_STOP = 10
MIN_RAMP_STOPS = 2


def _blend(start: str, end: str, ratio: float) -> str:
    pairs = zip(
        (int(start[i : i + 2], 16) for i in (1, 3, 5)),
        (int(end[i : i + 2], 16) for i in (1, 3, 5)),
        strict=True,
    )
    return "#" + "".join(f"{round(a + (b - a) * ratio):02X}" for a, b in pairs)


def glow_color(
    step: int, colors: Sequence[str] = GLOW_COLORS, steps_per_stop: int = STEPS_PER_STOP
) -> str:
    # Walks the ramp and back down, blending between stops so the pulse is smooth
    # rather than stepping through four discrete shades.
    if len(colors) < MIN_RAMP_STOPS:
        return colors[0] if colors else ""

    span = (len(colors) - 1) * steps_per_stop
    position = step % (span * 2)
    if position > span:
        position = span * 2 - position

    index, offset = divmod(position, steps_per_stop)
    if index >= len(colors) - 1:
        return colors[-1]
    return _blend(colors[index], colors[index + 1], offset / steps_per_stop)
