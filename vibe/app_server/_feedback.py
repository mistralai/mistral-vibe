"""The ``feedback/*`` API: when to ask for a rating, and what the user answered.

The policy is server-owned so every client only renders the survey: sampling,
the cooldowns after showing, answering and snoozing, the telemetry and model
gates, and the minimum number of typed user messages.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import random
import time
from typing import Any, Protocol

from vibe.app_server._dispatch import DispatchResult, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.models import PublicHistoryEntry, PublicMessageEntry
from vibe.app_server.protocol import (
    EmptyResponse,
    FeedbackRecordParams,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
)
from vibe.observability.logging import logger
from vibe.utils.cache_store import CacheStore, FileSystemCacheStore

__all__ = [
    "FEEDBACK_COOLDOWN_SECONDS",
    "FEEDBACK_PROBABILITY",
    "FEEDBACK_RESPONDED_COOLDOWN_SECONDS",
    "FEEDBACK_SNOOZED_COOLDOWN_SECONDS",
    "MIN_USER_MESSAGES_FOR_FEEDBACK",
    "FeedbackController",
    "FeedbackHost",
]

FEEDBACK_PROBABILITY = 0.05
FEEDBACK_COOLDOWN_SECONDS = 3600
FEEDBACK_RESPONDED_COOLDOWN_SECONDS = 86400
FEEDBACK_SNOOZED_COOLDOWN_SECONDS = 604800
MIN_USER_MESSAGES_FOR_FEEDBACK = 3

_CACHE_SECTION = "user_feedback"
_LAST_SHOWN_KEY = "last_shown_at"
_RESPONDED_AT_KEY = "responded_at"
_SNOOZED_AT_KEY = "snoozed_at"

_RECORD_KEYS = {
    "asked": _LAST_SHOWN_KEY,
    "given": _RESPONDED_AT_KEY,
    "snoozed": _SNOOZED_AT_KEY,
}


class FeedbackHost(Protocol):
    """What ``FeedbackController`` needs of its session."""

    def require_session(self, session_id: str) -> None:
        """Raise if ``session_id`` is not this session."""
        ...

    def telemetry_active(self) -> bool:
        """Whether a rating could be reported at all."""
        ...

    def is_active_model_mistral(self) -> bool:
        """Whether the session talks to a Mistral model."""
        ...

    async def history(self) -> Sequence[PublicHistoryEntry]:
        """The session's public history."""
        ...


class FeedbackController:
    def __init__(
        self, host: FeedbackHost, cache_store: CacheStore | None = None
    ) -> None:
        self._host = host
        self._cache_store = cache_store or FileSystemCacheStore()

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        match method:
            case "feedback/shouldShow":
                response: ProtocolModel = await self._should_show(
                    validate_wire(FeedbackShouldShowParams, raw_params)
                )
            case "feedback/record":
                response = await self._record(
                    validate_wire(FeedbackRecordParams, raw_params)
                )
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def _should_show(
        self, params: FeedbackShouldShowParams
    ) -> FeedbackShouldShowResponse:
        self._host.require_session(params.session_id)
        # ``pending_user_messages`` is ignored: clients ask on ``turn/started``
        # or after the turn, when history already holds their prompt.
        return FeedbackShouldShowResponse(
            show=await self._eligible(),
            snooze_duration_seconds=FEEDBACK_SNOOZED_COOLDOWN_SECONDS,
        )

    async def _eligible(self) -> bool:
        if not self._host.telemetry_active():
            return False
        if not self._host.is_active_model_mistral():
            return False
        # Cheapest gates first: most calls stop at the draw, before any I/O.
        if random.random() >= FEEDBACK_PROBABILITY:
            return False
        cache = await asyncio.to_thread(self._cache_store.read_section, _CACHE_SECTION)
        if _within_cooldown(cache, now=time.time()):
            return False
        try:
            typed = _typed_user_messages(await self._host.history())
        except Exception:
            # The survey is best-effort: a failed history read must not fail
            # the finished turn whose client asked.
            logger.warning("Failed to count typed user messages", exc_info=True)
            return False
        return typed >= MIN_USER_MESSAGES_FOR_FEEDBACK

    async def _record(self, params: FeedbackRecordParams) -> EmptyResponse:
        self._host.require_session(params.session_id)
        await asyncio.to_thread(
            self._cache_store.write_section,
            _CACHE_SECTION,
            {_RECORD_KEYS[params.action]: int(time.time())},
        )
        return EmptyResponse()


def _typed_user_messages(history: Sequence[PublicHistoryEntry]) -> int:
    # Injected Turns never reach public history; injected context shown as a
    # message does, with the ``harness`` source.
    return sum(
        isinstance(entry, PublicMessageEntry)
        and entry.role == "user"
        and entry.source != "harness"
        for entry in history
    )


def _within_cooldown(cache: dict[str, Any], *, now: float) -> bool:
    cooldowns = (
        (_SNOOZED_AT_KEY, FEEDBACK_SNOOZED_COOLDOWN_SECONDS),
        (_RESPONDED_AT_KEY, FEEDBACK_RESPONDED_COOLDOWN_SECONDS),
        (_LAST_SHOWN_KEY, FEEDBACK_COOLDOWN_SECONDS),
    )
    return any(
        (at := _timestamp(cache.get(key))) is not None and 0 <= now - at < duration
        for key, duration in cooldowns
    )


def _timestamp(value: object) -> int | None:
    # A hand-edited or corrupted cache entry must not block the survey forever.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
