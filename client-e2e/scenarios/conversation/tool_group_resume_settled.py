"""A resumed history ending on tool calls settles its folded group instead of spinning."""

from __future__ import annotations

from e2e.app_server.events import bash, user_msg
from e2e.app_server.scenario import AppServerEvent, Timeline

_RESUMED_SESSION = "00000000-0000-4000-8000-000000000004"


def _history_entry(event: AppServerEvent) -> dict[str, object]:
    """Repoint a builder's entry at the resumed session's static history."""
    entry = event["params"]["entry"]
    entry["sessionId"] = _RESUMED_SESSION
    entry["turnId"] = None
    return entry


# The persisted history ends on the tool call (the CLI was killed mid-turn), so
# the trailing group has no later entry to finalize it: both clients must still
# settle it once the resume rebuild mounts the history.
_HISTORY = [
    _history_entry(user_msg("run the flaky command")),
    _history_entry(bash("echo ok", "ok")),
]

handshake = {
    "session/resume": {"state": {"history": _HISTORY}},
    "session/read": {"state": {"history": _HISTORY}},
}

client_args = ("--continue",)
capture_startup = False

_SETTLED_HEADER = "⏵ Ran commands"
screen_contains = {"rust": (_SETTLED_HEADER,), "python": (_SETTLED_HEADER,)}
screen_excludes = {"rust": ("Running commands",), "python": ("Running commands",)}


timeline: Timeline = ["x"]
