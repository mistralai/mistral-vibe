"""Shared rail setup: a running turn with a running subagent, two background tasks, a goal and a todo list."""

from __future__ import annotations

import atexit
import base64
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, REPLAY_NOW_MS
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
DOWN = "\x1b[B"

# 38 lines: a window shows the last 10; color codes and a redrawn progress line are cleaned.
_OUTPUT = "".join(f"scanning invoices/batch-{index:02}.pdf\n" for index in range(1, 37))
_OUTPUT += "\x1b[32mindexed 36 PDFs\x1b[0m\nprogress 10%\rprogress 100%\n"
_OUTPUT_BYTES = _OUTPUT.encode()

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
    "session/backgroundProcess/output": {
        "availability": "available",
        "processId": "process-1",
        "outputBase64": base64.b64encode(_OUTPUT_BYTES).decode(),
        "outputStartCursor": 0,
        "nextCursor": len(_OUTPUT_BYTES),
        "bytesAvailable": len(_OUTPUT_BYTES),
        "hasMore": False,
        "truncatedBefore": False,
        "isFinal": True,
    },
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


def used_home(days_ago: int) -> str:
    """A `VIBE_HOME` whose cache says the rail was last used `days_ago` days before the replay clock."""
    home = tempfile.mkdtemp(prefix="e2e_rail_used_")
    atexit.register(shutil.rmtree, home, True)
    Path(home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
    last_used = REPLAY_NOW_MS // 1000 - days_ago * 24 * 60 * 60
    Path(home, "cache.toml").write_text(f"[status_rail]\nlast_used = {last_used}\n")
    return home
