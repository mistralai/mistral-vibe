"""The one reading of a shell tool's timeout settings, shared by the config schema,
the agent-profile layer and the unified harness backend. It imports nothing from
the config package, so each of them can use it.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Final

LONGEST_SHELL_TIMEOUT_SECONDS: Final = 2**53 - 1
"""The longest shell timeout a session starts with: Core's bash timeouts are JSON
safe integers, at most 2**53 - 1, so a longer one would fail session start without
naming the setting."""

VIBE_MAX_SHELL_TIMEOUT_SECONDS: Final = 300
"""Vibe's own shell maximum, used when only a `default_timeout` at or below it is set."""


def shell_timeout_seconds(name: str, key: str, value: object) -> int:
    """A shell timeout setting as whole seconds: a whole number, or quoted text of
    one, which the tool's own config reads as a number too. Anything else is a
    configuration error: the unified harness takes whole seconds above 0, a
    fraction is not rounded, and a value above the longest Core takes is not lowered.

    Numbers and text are read exactly, as decimals, never through a float, so the
    value Core gets is the one in the config.
    """
    number: Decimal | None = None
    if isinstance(value, int | float) and not isinstance(value, bool):
        number = Decimal(value)
    elif isinstance(value, str):
        try:
            number = Decimal(value)
        except InvalidOperation:
            number = None
    if number is None or not number.is_finite():
        raise ValueError(
            f"tools.{name}: {key} {value} is not a number of seconds. Set a finite"
            " number of seconds, or remove it."
        )
    # Both bounds are checked before the fraction, so text such as "1e999999999"
    # is refused here rather than worked out in full.
    if number <= 0:
        raise ValueError(
            f"tools.{name}: {key} {value} is not above 0 seconds. Set a number of"
            " seconds above 0."
        )
    if number > LONGEST_SHELL_TIMEOUT_SECONDS:
        raise ValueError(
            f"tools.{name}: {key} {value} is above {LONGEST_SHELL_TIMEOUT_SECONDS}"
            " seconds, the longest shell timeout. Set a smaller number of seconds."
        )
    if number != number.to_integral_value():
        raise ValueError(
            f"tools.{name}: {key} {value} is not a whole number of seconds. Set"
            " a whole number of seconds."
        )
    return int(number)


def shell_max_timeout(default: int | None, maximum: int | None) -> int | None:
    """The maximum a shell runs under: the one set, else Vibe's own for a default within it."""
    if maximum is not None or default is None:
        return maximum
    if default > VIBE_MAX_SHELL_TIMEOUT_SECONDS:
        return None
    return VIBE_MAX_SHELL_TIMEOUT_SECONDS


def check_shell_timeout_pair(name: str, settings: Mapping[str, object]) -> None:
    """Refuse a shell tool's ``max_timeout_seconds`` set alone, a ``default_timeout``
    set alone above Vibe's own maximum, or a default above the maximum.
    """
    seconds: dict[str, int] = {}
    for key in ("default_timeout", "max_timeout_seconds"):
        value = settings.get(key)
        if value is None:
            continue
        seconds[key] = shell_timeout_seconds(name, key, value)
    default = seconds.get("default_timeout")
    maximum = shell_max_timeout(default, seconds.get("max_timeout_seconds"))
    if (default is None) != (maximum is None):
        set_alone, missing = (
            ("default_timeout", "max_timeout_seconds")
            if default is not None
            else ("max_timeout_seconds", "default_timeout")
        )
        raise ValueError(
            f"tools.{name}: {set_alone} is set without {missing}. Set both, or neither."
        )
    if default is not None and maximum is not None and default > maximum:
        raise ValueError(
            f"tools.{name}: default_timeout {default} is above"
            f" max_timeout_seconds {maximum}. Lower default_timeout or raise"
            " max_timeout_seconds."
        )
