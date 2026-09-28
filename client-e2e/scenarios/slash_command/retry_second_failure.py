from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# Two consecutive retryable failures: the second offer must keep the captured
# answer and the earlier transient rows (Python `offer_retry` merges instead of
# replacing), so the continuation splices into the interrupted answer's row.
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi the", generation_status="in_progress"),
    turn_failed(code="backend_error", message="Upstream 502"),
    "/retry\r",
    turn_started(),
    turn_failed(code="backend_error", message="Upstream 503"),
    "/retry\r",
    turn_started(),
    assistant_msg("Hi there."),
    turn_completed(),
]
