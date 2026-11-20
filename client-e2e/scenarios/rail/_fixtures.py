"""Shared rail setup: a running turn with a running subagent, two background tasks, a goal and a todo list."""

from __future__ import annotations

from e2e.app_server.events import (
    child_session_updated,
    goal,
    todo,
    todo_item,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

ESCAPE = "\x1b[27u"
RIGHT = "\x1b[C"
CTRL_X = "\x18"
LEFT = "\x1b[D"

TASKS = ["kubectl get jobs -n orkl -w", "uv run python watch_pdfs.py"]

handshake = {
    "session/start": {
        "state": {
            "session": {"goal": goal()},
            "backgroundProcesses": [
                {
                    "processId": f"process-{index}",
                    "command": command,
                    "status": "running",
                    "createdAt": "2026-08-24T17:41:00Z",
                }
                for index, command in enumerate(TASKS)
            ],
        }
    },
    "session/backgroundProcess/stop": {"processId": "process-0", "status": "stopped"},
}

_PROMPT = "run the parity suite"

SETUP: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    todo([
        todo_item("1", "Write the parity suite", "completed"),
        todo_item("2", "Run the parity suite", "in_progress"),
        todo_item("3", "File the follow-ups", "pending"),
    ]),
    child_session_updated(context_tokens=2_500),
]
