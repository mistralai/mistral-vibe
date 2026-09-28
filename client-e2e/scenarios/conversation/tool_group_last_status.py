"""The folded group icon reflects the last call's status, not the worst."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    bash_failed,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "try the flaky commands"

# Turn one ends on a failed call, so its group settles to the error glyph even
# though the earlier calls succeeded; turn two ends on a success, so its group
# settles to the success glyph even though its first call failed.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("echo ok", "ok"),
    bash("cat missing.txt"),
    bash_failed("flake --seed 1"),
    assistant_msg("The last one failed."),
    turn_completed(),
    "next\r",
    turn_started(),
    user_msg("next"),
    bash_failed("flake --seed 2"),
    bash("echo recovered", "recovered"),
    assistant_msg("The last one succeeded."),
    turn_completed(),
]
