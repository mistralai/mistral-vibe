"""A running multi-line command wraps its long lines inside the header border."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash_completed,
    bash_started,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "create the worktree"
_EFFECT_ID = "running-long-bash"

# The third line is wider than the 120-column terminal, so it must wrap.
_COMMAND = (
    "set -e\n"
    "cd /tmp/dashboard\n"
    'printf \'{"branch": "%s", "base_commit": "%s", "branch_created": true, '
    '"claimed_at": "%s"}\\n\' "$BRANCH" "$BASE" "$NOW" > "$CL/record.json"\n'
    'git -C "$WT/$NAME" log --oneline -1'
)

# Busy frames settle under replay so the running header is capturable.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
# A release step must not consume a marker of its own, so settle key by key.
settle_per_key = True
capture_steps = {1, 2}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash_started(_COMMAND, _EFFECT_ID),
    bash_completed(_EFFECT_ID, _COMMAND, stdout="done"),
    assistant_msg("Done."),
    turn_completed(),
    # Turn start, the harness queue drain, the prompt, then the running call.
    {"release": 4},
    {"release": 3},
]
