from __future__ import annotations

from vibe.core.agent_loop import AgentLoop
from vibe.core.feedback import record_feedback_asked, should_show_feedback
from vibe.core.types import Role


class FeedbackBarManager:
    """Decides whether to show the feedback bar and records when feedback is given."""

    def should_show(self, agent_loop: AgentLoop) -> bool:
        user_message_count = (
            sum(m.role == Role.user and not m.injected for m in agent_loop.messages)
            + 1  # +1 for the message the user just sent
        )
        return should_show_feedback(
            telemetry_active=agent_loop.telemetry_client.is_active(),
            is_mistral_model=agent_loop.config.is_active_model_mistral(),
            user_message_count=user_message_count,
        )

    def record_feedback_asked(self) -> None:
        record_feedback_asked()
