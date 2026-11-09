# Twin: mistralai.vibe.harness.runtime.completion.profile.bounds. Keep both copies identical below this line.
"""Named constants and the compaction threshold rule.

The threshold has a floor (`minimum`) so compaction has something to work with, and a
ceiling (`maximum`) that leaves room for the summary request; the ceiling is also the
summary request's budget. With nothing explicit, the threshold is
`min(DEFAULT_THRESHOLD_CAP, window - DEFAULT_THRESHOLD_GAP)`, moved into the range. An
explicit threshold outside the range is refused, never moved.
"""

from dataclasses import dataclass
from fractions import Fraction
from math import ceil, floor

from .types import ProfileError

THRESHOLD_MIN_CAP = 50_000
THRESHOLD_MIN_WINDOW_SHARE = Fraction(1, 2)
THRESHOLD_MIN_FLOOR = 10_000
SUMMARY_HEADROOM_CAP = 24_000
SUMMARY_HEADROOM_WINDOW_SHARE = Fraction(1, 4)
DEFAULT_THRESHOLD_GAP = 60_000
DEFAULT_THRESHOLD_CAP = 256_000
# The window when nobody knows it; using it raises a `window_guessed` warning.
DEFAULT_CONTEXT_WINDOW = 262_144
# The smallest window whose range is not empty (minimum <= maximum).
MINIMUM_CONTEXT_WINDOW = 13_334
# `max_tokens` for a format that requires one when the model's max output is unknown.
REQUIRED_MAX_TOKENS_FALLBACK = 32_768


@dataclass(frozen=True, slots=True)
class ThresholdBounds:
    """The range a compaction threshold must fall in, and the default inside it."""

    minimum: int
    maximum: int
    default: int


def threshold_bounds(context_window: int) -> ThresholdBounds:
    """Return the threshold range and default for a window, or reject the window."""
    minimum = max(
        min(THRESHOLD_MIN_CAP, floor(context_window * THRESHOLD_MIN_WINDOW_SHARE)),
        THRESHOLD_MIN_FLOOR,
    )
    headroom = min(
        SUMMARY_HEADROOM_CAP, ceil(context_window * SUMMARY_HEADROOM_WINDOW_SHARE)
    )
    maximum = context_window - headroom
    if maximum < minimum:
        raise ProfileError(
            f"a context window of {context_window} tokens is too small; "
            f"the smallest is {MINIMUM_CONTEXT_WINDOW}"
        )
    preferred = min(DEFAULT_THRESHOLD_CAP, context_window - DEFAULT_THRESHOLD_GAP)
    return ThresholdBounds(
        minimum=minimum, maximum=maximum, default=_clamp(preferred, minimum, maximum)
    )


def check_threshold(value: int, bounds: ThresholdBounds, context_window: int) -> int:
    """Return an explicit threshold that lies in its range, or refuse it."""
    if bounds.minimum <= value <= bounds.maximum:
        return value
    raise ProfileError(
        f"compaction threshold {value} is outside {bounds.minimum}..{bounds.maximum} "
        f"for a {context_window}-token context window; set a threshold in that range "
        "or leave it unset to use the model's default"
    )


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))
