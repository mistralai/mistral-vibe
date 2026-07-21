from __future__ import annotations

from vibe.core.types import AgentStats, LLMChunk, LLMMessage, RateLimitInfo, Role


def test_rate_limit_info_frozen_fields():
    info = RateLimitInfo(limit_tokens=500_000, remaining_tokens=330_000)
    assert info.limit_tokens == 500_000
    assert info.remaining_tokens == 330_000


def test_llm_chunk_rate_limit_defaults_none_and_survives_add():
    a = LLMChunk(message=LLMMessage(role=Role.assistant, content="a"))
    b = LLMChunk(
        message=LLMMessage(role=Role.assistant, content="b"),
        rate_limit=RateLimitInfo(limit_tokens=10, remaining_tokens=5),
    )
    assert a.rate_limit is None
    merged = a + b
    assert merged.rate_limit is not None
    assert merged.rate_limit.remaining_tokens == 5


def test_agent_stats_rate_limit_defaults():
    stats = AgentStats()
    assert stats.rate_limit_tokens_limit == 0
    assert stats.rate_limit_tokens_remaining == 0
    assert stats.rate_limit_captured_at == 0.0
