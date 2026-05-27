from __future__ import annotations

from unittest.mock import MagicMock

from vibe.core.agent_loop import AgentLoop
from vibe.core.types import LLMMessage, Role


def _call(*messages: LLMMessage) -> LLMMessage | None:
    fake_self = MagicMock(spec=["messages"])
    fake_self.messages = list(messages)
    return AgentLoop._last_user_message(fake_self)


def test_returns_last_user_message() -> None:
    m1 = LLMMessage(role=Role.user, content="first")
    m2 = LLMMessage(role=Role.assistant, content="reply")
    m3 = LLMMessage(role=Role.user, content="second")
    result = _call(m1, m2, m3)
    assert result is m3


def test_returns_none_when_no_user_messages() -> None:
    assert (
        _call(
            LLMMessage(role=Role.system, content="system prompt"),
            LLMMessage(role=Role.assistant, content="hello"),
        )
        is None
    )


def test_returns_none_for_empty_messages() -> None:
    assert _call() is None


def test_skips_injected_user_messages() -> None:
    real = LLMMessage(role=Role.user, content="real")
    injected = LLMMessage(role=Role.user, content="reminder", injected=True)
    assert _call(real, injected) is real


def test_returns_user_message_with_empty_content() -> None:
    empty = LLMMessage(role=Role.user, content="")
    assert _call(empty) is empty
