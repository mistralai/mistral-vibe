from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# A case-variant /Retry with extra instructions: the command word matches
# case-insensitively and the guidance must reach the injected turn/start prompt
# (Python `parse_command` splits on whitespace; `build_retry_prompt` appends).
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi the", generation_status="in_progress"),
    turn_failed(code="backend_error", message="Upstream 502"),
    "/Retry keep it brief\r",
    turn_started(),
    assistant_msg("Hi there."),
    turn_completed(),
]
