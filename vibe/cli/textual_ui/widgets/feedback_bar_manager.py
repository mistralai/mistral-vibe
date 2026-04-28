from __future__ import annotations

import random
import time

from vibe.cli.cache import read_cache, write_cache
from vibe.core.agent_loop import AgentLoop
from vibe.core.paths import CACHE_FILE
from vibe.core.types import Role

FEEDBACK_PROBABILITY = 0.2
FEEDBACK_COOLDOWN_SECONDS = 3600
_CACHE_SECTION = "user_feedback"
_LAST_SHOWN_KEY = "last_shown_at"
MIN_USER_MESSAGES_FOR_FEEDBACK = 3


class FeedbackBarManager:
    """Decides whether to show the feedback bar and records when feedback is given."""

    def should_show(self, agent_loop: AgentLoop) -> bool:
        if not agent_loop.telemetry_client.is_active():
            return False

        if not agent_loop.config.is_active_model_mistral():
            return False

        if (
            sum(m.role == Role.user and not m.injected for m in agent_loop.messages)
            + 1  # +1 for the message the user just sent
            < MIN_USER_MESSAGES_FOR_FEEDBACK
        ):
            return False

        last_ts = (
            read_cache(CACHE_FILE.path).get(_CACHE_SECTION, {}).get(_LAST_SHOWN_KEY, 0)
        )
        if not isinstance(last_ts, int):
            return False

        return (
            time.time() - last_ts >= FEEDBACK_COOLDOWN_SECONDS
            and random.random() <= FEEDBACK_PROBABILITY
        )

    def record_feedback_asked(self) -> None:
        write_cache(
            CACHE_FILE.path, _CACHE_SECTION, {_LAST_SHOWN_KEY: int(time.time())}
        )
