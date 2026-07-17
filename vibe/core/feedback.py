from __future__ import annotations

import random
import time
from typing import Any

from vibe.core.cache_store import VibeCodeCacheStore

FEEDBACK_PROBABILITY = 0.2
FEEDBACK_COOLDOWN_SECONDS = 3600
FEEDBACK_RESPONDED_COOLDOWN_SECONDS = 86400
FEEDBACK_SNOOZED_COOLDOWN_SECONDS = 604800
MIN_USER_MESSAGES_FOR_FEEDBACK = 3
_CACHE_SECTION = "user_feedback"
_LAST_SHOWN_KEY = "last_shown_at"
_RESPONDED_AT_KEY = "responded_at"
_SNOOZED_AT_KEY = "snoozed_at"


def _is_within_cooldown(cache_data: dict[str, Any]) -> bool:
    now = time.time()

    snoozed_ts = cache_data.get(_SNOOZED_AT_KEY)
    if (
        isinstance(snoozed_ts, int)
        and now - snoozed_ts < FEEDBACK_SNOOZED_COOLDOWN_SECONDS
    ):
        return True

    responded_ts = cache_data.get(_RESPONDED_AT_KEY)
    if (
        isinstance(responded_ts, int)
        and now - responded_ts < FEEDBACK_RESPONDED_COOLDOWN_SECONDS
    ):
        return True

    last_shown_ts = cache_data.get(_LAST_SHOWN_KEY, 0)
    return (
        isinstance(last_shown_ts, int)
        and last_shown_ts > 0
        and now - last_shown_ts < FEEDBACK_COOLDOWN_SECONDS
    )


def should_show_feedback(
    *,
    telemetry_active: bool,
    is_mistral_model: bool,
    user_message_count: int,
    cache_store: VibeCodeCacheStore,
) -> bool:
    if not telemetry_active or not is_mistral_model:
        return False
    if user_message_count < MIN_USER_MESSAGES_FOR_FEEDBACK:
        return False

    cache_data = cache_store.read_section(_CACHE_SECTION)
    if _is_within_cooldown(cache_data):
        return False

    return random.random() <= FEEDBACK_PROBABILITY


def record_feedback_asked(cache_store: VibeCodeCacheStore) -> None:
    cache_store.write_section(_CACHE_SECTION, {_LAST_SHOWN_KEY: int(time.time())})


def record_feedback_given(cache_store: VibeCodeCacheStore) -> None:
    cache_store.write_section(_CACHE_SECTION, {_RESPONDED_AT_KEY: int(time.time())})


def record_feedback_snoozed(cache_store: VibeCodeCacheStore) -> None:
    cache_store.write_section(_CACHE_SECTION, {_SNOOZED_AT_KEY: int(time.time())})
