from __future__ import annotations

from vibe.core.session.title_policy import DEFAULT_TITLE_POLICY, TitlePolicy


def test_tail_is_the_transcript_budget_minus_the_head() -> None:
    policy = TitlePolicy(max_transcript_chars=6000, head_transcript_chars=1500)

    assert policy.tail_transcript_chars == 4500


def test_default_policy_is_internally_consistent() -> None:
    assert (
        DEFAULT_TITLE_POLICY.head_transcript_chars
        + DEFAULT_TITLE_POLICY.tail_transcript_chars
        == DEFAULT_TITLE_POLICY.max_transcript_chars
    )
