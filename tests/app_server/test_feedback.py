from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pytest

from vibe.app_server._feedback import (
    FEEDBACK_SNOOZED_COOLDOWN_SECONDS,
    MIN_USER_MESSAGES_FOR_FEEDBACK,
    FeedbackController,
)
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicMessageSource,
    TextContentBlock,
)
from vibe.app_server.protocol import FeedbackShouldShowResponse
from vibe.utils.cache_store import InMemoryCacheStore

_SESSION_ID = "session-1"


def _message(
    role: Literal["user", "assistant"], source: PublicMessageSource
) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=f"{role}-{source}",
        session_id=_SESSION_ID,
        created_at=0,
        updated_at=0,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role=role,
        content=[TextContentBlock(text="text")],
        source=source,
    )


def _typed(count: int) -> list[PublicHistoryEntry]:
    return [_message("user", "turn_start") for _ in range(count)]


@dataclass
class FakeFeedbackHost:
    mistral_model: bool = True
    entries: list[PublicHistoryEntry] = field(
        default_factory=lambda: _typed(MIN_USER_MESSAGES_FOR_FEEDBACK)
    )
    history_error: Exception | None = None

    def require_session(self, session_id: str) -> None:
        assert session_id == _SESSION_ID

    def telemetry_active(self) -> bool:
        return True

    def is_active_model_mistral(self) -> bool:
        return self.mistral_model

    async def history(self) -> list[PublicHistoryEntry]:
        if self.history_error is not None:
            raise self.history_error
        return self.entries


@pytest.fixture(autouse=True)
def sampling_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vibe.app_server._feedback.FEEDBACK_PROBABILITY", 1.0)


async def _should_show(
    host: FakeFeedbackHost, *, pending: int = 0
) -> FeedbackShouldShowResponse:
    controller = FeedbackController(host, InMemoryCacheStore())
    result = await controller.dispatch(
        "feedback/shouldShow",
        {"sessionId": _SESSION_ID, "pendingUserMessages": pending},
    )
    assert isinstance(result.response, FeedbackShouldShowResponse)
    return result.response


@pytest.mark.asyncio
async def test_shows_for_an_eligible_session() -> None:
    assert (await _should_show(FakeFeedbackHost())).show


@pytest.mark.asyncio
async def test_the_pending_prompt_already_in_history_is_not_counted_twice() -> None:
    host = FakeFeedbackHost(entries=_typed(MIN_USER_MESSAGES_FOR_FEEDBACK - 1))

    assert not (await _should_show(host, pending=1)).show


@pytest.mark.asyncio
async def test_counts_steered_messages_as_typed() -> None:
    entries = [*_typed(MIN_USER_MESSAGES_FOR_FEEDBACK - 1)]
    entries.append(_message("user", "turn_steer"))

    assert (await _should_show(FakeFeedbackHost(entries=entries))).show


@pytest.mark.asyncio
async def test_ignores_injected_context_and_assistant_messages() -> None:
    entries = [
        *_typed(MIN_USER_MESSAGES_FOR_FEEDBACK - 1),
        _message("user", "harness"),
        _message("assistant", "harness"),
    ]

    assert not (await _should_show(FakeFeedbackHost(entries=entries))).show


@pytest.mark.asyncio
async def test_does_not_show_for_a_non_mistral_model() -> None:
    assert not (await _should_show(FakeFeedbackHost(mistral_model=False))).show


@pytest.mark.asyncio
async def test_a_failed_history_read_hides_the_prompt_instead_of_failing() -> None:
    response = await _should_show(
        FakeFeedbackHost(history_error=RuntimeError("runtime unavailable"))
    )

    assert not response.show
    assert response.snooze_duration_seconds == FEEDBACK_SNOOZED_COOLDOWN_SECONDS
