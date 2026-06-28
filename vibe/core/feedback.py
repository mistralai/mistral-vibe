from __future__ import annotations

import random
import time

from vibe.core.cache_store import VibeCodeCacheStore

FEEDBACK_PROBABILITY = 0.2
FEEDBACK_COOLDOWN_SECONDS = 3600
MIN_USER_MESSAGES_FOR_FEEDBACK = 3
_CACHE_SECTION = "user_feedback"
_LAST_SHOWN_KEY = "last_shown_at"


def should_show_feedback(
    *,
    telemetry_active: bool,
    is_mistral_model: bool,
    user_message_count: int,
    cache_store: VibeCodeCacheStore,
) -> bool:
    if not telemetry_active:
        return False
    if not is_mistral_model:
        return False
    if user_message_count < MIN_USER_MESSAGES_FOR_FEEDBACK:
        return False

    last_ts = cache_store.read_section(_CACHE_SECTION).get(_LAST_SHOWN_KEY, 0)
    if not isinstance(last_ts, int):
        return False

    return (
        time.time() - last_ts >= FEEDBACK_COOLDOWN_SECONDS
        and random.random() <= FEEDBACK_PROBABILITY
    )


def record_feedback_asked(cache_store: VibeCodeCacheStore) -> None:
    cache_store.write_section(_CACHE_SECTION, {_LAST_SHOWN_KEY: int(time.time())})
