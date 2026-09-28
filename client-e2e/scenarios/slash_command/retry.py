from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The retry scenario: a turn fails with a retryable backend_error, then /retry
# continues it. The failed turn arms can_retry; the /retry command fires.
# The retried turn's first assistant entry removes the transient error row and
# /retry echo, and continues the interrupted answer's row (Python
# `_resolve_retry_presentation`).
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi the", generation_status="in_progress"),
    turn_failed(code="backend_error", message="Upstream 502"),
    "/retry\r",
    turn_started(),
    assistant_msg("Hi there."),
    turn_completed(),
]
