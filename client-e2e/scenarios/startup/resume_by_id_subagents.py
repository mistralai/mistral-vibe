"""`--resume <id>` on a session that spawned subagents lists the kept child, opens its transcript, and drops the stopped one."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import (
    assistant_msg,
    child_entry,
    child_history_state,
    child_session,
)
from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"
_KEPT_ID, _STOPPED_ID = "child-session", "child-session-2"
_CHILD_RESULT = "Found the grouping code in transcript/grouping.rs."


_resumed = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/resume"]
_resumed["state"]["childSessions"] = [
    child_session("idle", child_session_id=_KEPT_ID, name="explore-1"),
    child_session("archived", child_session_id=_STOPPED_ID, name="explore-2"),
]

client_args = ("--resume", _SAVED)
handshake = {
    "session/resume": _resumed,
    f"session/read#{_KEPT_ID}": child_history_state(
        _KEPT_ID,
        [child_entry(assistant_msg(_CHILD_RESULT)["params"]["entry"], _KEPT_ID)],
    ),
}
request_methods = frozenset({"session/resume", "session/read"})

timeline: Timeline = [
    "\x18",  # Ctrl+X — focus the rail on its agents item, seeded by the resume
    "\r",  # Enter — open the subagent list on the Main row
    "\x1b[B",  # Down — highlight the kept child row
    "\r",  # Enter — open its read-only view
]

screen_contains = ("Viewing Explore 1 read-only", _CHILD_RESULT)
screen_excludes = (
    "Explore 2",
    "Failed to resume session",
    "Loading subagent transcript…",
)
