"""Tests for the reasoning-only (thought-stage) hang fix.

Regression test for the bug where the agent loop would silently stop after
the model emitted a reasoning/thinking block with no text content and no tool
calls.  The fix re-invokes the model so the thinking context is used to
produce an actual response.

Issue: "Agent frequently stops responding after displaying the Thought stage"
"""

from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent_loop import AgentLoop
from vibe.core.types import AssistantEvent, LLMMessage, ReasoningEvent, Role


def make_config():
    return build_test_vibe_config(
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
        include_model_info=False,
        include_commit_signature=False,
        enabled_tools=[],
        tools={},
    )


# ---------------------------------------------------------------------------
# Unit tests for _is_reasoning_only_message helper
# ---------------------------------------------------------------------------


class TestIsReasoningOnlyMessage:
    def test_reasoning_and_content_is_not_reasoning_only(self):
        msg = LLMMessage(
            role=Role.assistant, content="Hello!", reasoning_content="Let me think..."
        )
        assert AgentLoop._is_reasoning_only_message(msg) is False

    def test_content_only_is_not_reasoning_only(self):
        msg = LLMMessage(role=Role.assistant, content="Hello!")
        assert AgentLoop._is_reasoning_only_message(msg) is False

    def test_empty_content_no_reasoning_is_not_reasoning_only(self):
        msg = LLMMessage(role=Role.assistant, content="")
        assert AgentLoop._is_reasoning_only_message(msg) is False

    def test_reasoning_with_empty_content_is_reasoning_only(self):
        """The classic stall case: model emits a thinking block but no reply."""
        msg = LLMMessage(
            role=Role.assistant,
            content="",  # empty string counts as falsy
            reasoning_content="Let me think...",
        )
        assert AgentLoop._is_reasoning_only_message(msg) is True

    def test_reasoning_with_none_content_is_reasoning_only(self):
        msg = LLMMessage(
            role=Role.assistant, content=None, reasoning_content="Some deep thoughts"
        )
        assert AgentLoop._is_reasoning_only_message(msg) is True

    def test_no_reasoning_no_content_is_not_reasoning_only(self):
        """Empty turn without reasoning is a different (separate) edge case."""
        msg = LLMMessage(role=Role.assistant, content=None)
        assert AgentLoop._is_reasoning_only_message(msg) is False


# ---------------------------------------------------------------------------
# Integration tests: end-to-end retry behaviour through AgentLoop.act()
# ---------------------------------------------------------------------------


class TestReasoningOnlyRetry:
    """Verify that a reasoning-only first response triggers a retry and the
    agent ultimately surfaces the second response to the user.
    """

    @pytest.mark.asyncio
    async def test_reasoning_only_then_content_retries_and_returns_content(self):
        """Model emits reasoning only on turn 1, content on turn 2.
        The agent should retry transparently and surface the content.
        """
        backend = FakeBackend([
            # Turn 1: reasoning only — triggers the stall
            [mock_llm_chunk(content="", reasoning_content="Let me think...")],
            # Turn 2: real response
            [mock_llm_chunk(content="Here is my answer.", reasoning_content=None)],
        ])
        agent = build_test_agent_loop(
            config=make_config(), backend=backend, enable_streaming=True
        )

        events = [e async for e in agent.act("Do something complex")]

        # The ReasoningEvent from turn 1 must be visible in the event stream.
        reasoning_events = [e for e in events if isinstance(e, ReasoningEvent)]
        assert len(reasoning_events) >= 1
        assert any("Let me think" in e.content for e in reasoning_events)

        # The AssistantEvent from turn 2 must be visible.
        assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
        assert any("Here is my answer" in e.content for e in assistant_events)

        # Two assistant messages should be in history: reasoning-only + real reply.
        assistant_messages = [m for m in agent.messages if m.role == Role.assistant]
        assert len(assistant_messages) == 2
        assert assistant_messages[0].reasoning_content == "Let me think..."
        assert not assistant_messages[0].content
        assert assistant_messages[1].content == "Here is my answer."

    @pytest.mark.asyncio
    async def test_normal_response_no_extra_llm_call(self):
        """A normal response (content + reasoning) should not trigger any retry."""
        backend = FakeBackend([
            [mock_llm_chunk(content="Done!", reasoning_content="Quick thought.")]
        ])
        agent = build_test_agent_loop(
            config=make_config(), backend=backend, enable_streaming=True
        )

        events = [e async for e in agent.act("Simple task")]

        assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
        assert any("Done!" in e.content for e in assistant_events)

        # Only one assistant message — no retry loop was entered.
        assistant_messages = [m for m in agent.messages if m.role == Role.assistant]
        assert len(assistant_messages) == 1

    @pytest.mark.asyncio
    async def test_exceeding_retry_budget_surfaces_error_message(self):
        """When the model keeps returning reasoning-only responses beyond the
        retry limit, the agent must surface a user-visible error rather than
        hanging or crashing.
        """
        # Build MAX+1 reasoning-only turns so we exhaust the retry budget.
        max_retries = AgentLoop._MAX_REASONING_ONLY_RETRIES
        reasoning_only_stream = [
            mock_llm_chunk(content="", reasoning_content=f"Thought {i}")
            for i in range(max_retries + 2)  # more than enough to exceed budget
        ]
        # Wrap each chunk as its own stream (one per backend call).
        backend = FakeBackend([[chunk] for chunk in reasoning_only_stream])
        agent = build_test_agent_loop(
            config=make_config(), backend=backend, enable_streaming=True
        )

        events = [e async for e in agent.act("This should trigger the error path")]

        # An AssistantEvent containing the warning must be present.
        assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
        error_events = [
            e for e in assistant_events if "⚠️" in e.content or "retries" in e.content
        ]
        assert len(error_events) >= 1, (
            "Expected a user-visible error event after exhausting retry budget, "
            f"got assistant events: {[e.content for e in assistant_events]}"
        )

    @pytest.mark.asyncio
    async def test_reasoning_only_non_streaming_retries_and_returns_content(self):
        """Same retry logic must work in non-streaming mode."""
        backend = FakeBackend([
            [mock_llm_chunk(content="", reasoning_content="Thinking...")],
            [mock_llm_chunk(content="Non-streaming answer.")],
        ])
        agent = build_test_agent_loop(
            config=make_config(), backend=backend, enable_streaming=False
        )

        events = [e async for e in agent.act("Non-streaming test")]

        assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
        assert any("Non-streaming answer" in e.content for e in assistant_events)
