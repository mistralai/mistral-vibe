from __future__ import annotations

from vibe.core.compaction import collect_prior_user_messages
from vibe.core.types import LLMMessage, Role

_PREFIX = "Another language model started to solve this problem"


def _user(content: str, *, injected: bool = False) -> LLMMessage:
    return LLMMessage(role=Role.user, content=content, injected=injected)


def test_empty_messages() -> None:
    assert collect_prior_user_messages([], _PREFIX) == []


def test_only_non_user_messages() -> None:
    messages = [
        LLMMessage(role=Role.system, content="sys"),
        LLMMessage(role=Role.assistant, content="hi"),
    ]
    assert collect_prior_user_messages(messages, _PREFIX) == []


def test_single_user_message_preserved() -> None:
    messages = [LLMMessage(role=Role.system, content="sys"), _user("first question")]
    out = collect_prior_user_messages(messages, _PREFIX)
    assert [m.content for m in out] == ["first question"]


def test_chronological_order_preserved() -> None:
    messages = [_user("first"), _user("second"), _user("third")]
    out = collect_prior_user_messages(messages, _PREFIX)
    assert [m.content for m in out] == ["first", "second", "third"]


def test_injected_messages_filtered_out() -> None:
    messages = [
        _user("real ask"),
        _user("middleware reminder", injected=True),
        _user("follow-up"),
    ]
    out = collect_prior_user_messages(messages, _PREFIX)
    assert [m.content for m in out] == ["real ask", "follow-up"]


def test_empty_content_filtered_out() -> None:
    messages = [_user(""), _user("real")]
    out = collect_prior_user_messages(messages, _PREFIX)
    assert [m.content for m in out] == ["real"]


def test_prior_summary_filtered_out() -> None:
    # A user message starting with the summary prefix represents a previous
    # compaction summary and must not be re-injected (would stack).
    messages = [
        _user("original ask"),
        _user(f"{_PREFIX}\nold summary content"),
        _user("newer ask"),
    ]
    out = collect_prior_user_messages(messages, _PREFIX)
    assert [m.content for m in out] == ["original ask", "newer ask"]


def test_budget_drops_oldest_first() -> None:
    # max_tokens=2 → 8 char budget. Walks newest-first, so "old" gets dropped.
    messages = [
        _user("old message that is long enough to matter"),
        _user("abc"),  # 1 token, fits
        _user("def"),  # 1 token, fits
    ]
    out = collect_prior_user_messages(messages, _PREFIX, max_tokens=2)
    assert [m.content for m in out] == ["abc", "def"]


def test_spillover_message_middle_truncated() -> None:
    # newest fits whole, middle one is partially trimmed, oldest dropped.
    messages = [
        _user("OLDEST" + "x" * 10_000 + "OLDEST_END"),
        _user("MIDDLE_HEAD" + "y" * 1_000 + "MIDDLE_TAIL"),
        _user("recent"),  # ~2 tokens
    ]
    out = collect_prior_user_messages(messages, _PREFIX, max_tokens=50)
    assert len(out) == 2  # oldest dropped
    assert out[-1].content == "recent"
    middle = out[0].content
    assert middle is not None
    assert middle.startswith("MIDDLE_HEAD")
    assert middle.endswith("MIDDLE_TAIL")
    assert "[... truncated ...]" in middle


def test_fresh_message_ids() -> None:
    # Returned messages must have new message_ids — they'll live in a fresh
    # session and reusing the source ids would cause collisions.
    original = _user("hello")
    out = collect_prior_user_messages([original], _PREFIX)
    assert len(out) == 1
    assert out[0].message_id != original.message_id


def test_only_assistant_and_system_around_users() -> None:
    messages = [
        LLMMessage(role=Role.system, content="sys"),
        _user("u1"),
        LLMMessage(role=Role.assistant, content="a1"),
        _user("u2"),
        LLMMessage(role=Role.assistant, content="a2"),
    ]
    out = collect_prior_user_messages(messages, _PREFIX)
    assert [m.content for m in out] == ["u1", "u2"]
    assert all(m.role == Role.user for m in out)
