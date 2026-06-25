from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from vibe.core.feedback import record_feedback_asked, should_show_feedback
from vibe.core.types import Role

if TYPE_CHECKING:
    from vibe.core.cache_store import VibeCodeCacheStore


class _FeedbackTelemetry(Protocol):
    def is_active(self) -> bool: ...


class _FeedbackConfig(Protocol):
    def is_active_model_mistral(self) -> bool: ...


class _FeedbackMessage(Protocol):
    @property
    def role(self) -> str: ...

    @property
    def injected(self) -> bool: ...


class _FeedbackSource(Protocol):
    @property
    def messages(self) -> Sequence[_FeedbackMessage]: ...

    @property
    def telemetry_client(self) -> _FeedbackTelemetry: ...

    @property
    def config(self) -> _FeedbackConfig: ...

    @property
    def cache_store(self) -> VibeCodeCacheStore: ...


class FeedbackBarManager:
    """Decides whether to show the feedback bar and records when feedback is given."""

    def should_show(self, agent_loop: _FeedbackSource) -> bool:
        user_message_count = (
            sum(m.role == Role.user and not m.injected for m in agent_loop.messages)
            + 1  # +1 for the message the user just sent
        )
        return should_show_feedback(
            telemetry_active=agent_loop.telemetry_client.is_active(),
            is_mistral_model=agent_loop.config.is_active_model_mistral(),
            user_message_count=user_message_count,
            cache_store=agent_loop.cache_store,
        )

    def record_feedback_asked(self, agent_loop: _FeedbackSource) -> None:
        record_feedback_asked(agent_loop.cache_store)
