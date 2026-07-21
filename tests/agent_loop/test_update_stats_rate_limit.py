from __future__ import annotations

from vibe.core.agent_loop._loop import AgentLoop
from vibe.core.types import AgentStats, LLMUsage, RateLimitInfo


def _bare_loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)  # 绕过重量级 __init__,只测 _update_stats
    loop.stats = AgentStats()
    return loop


def test_update_stats_records_rate_limit():
    loop = _bare_loop()
    loop._update_stats(
        usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
        time_seconds=1.0,
        rate_limit=RateLimitInfo(limit_tokens=500_000, remaining_tokens=330_000),
    )
    assert loop.stats.rate_limit_tokens_limit == 500_000
    assert loop.stats.rate_limit_tokens_remaining == 330_000
    assert loop.stats.rate_limit_captured_at > 0


def test_update_stats_none_rate_limit_keeps_previous():
    loop = _bare_loop()
    loop.stats.rate_limit_tokens_limit = 100
    loop.stats.rate_limit_tokens_remaining = 40
    loop.stats.rate_limit_captured_at = 123.0
    loop._update_stats(
        usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        time_seconds=1.0,
        rate_limit=None,
    )
    assert loop.stats.rate_limit_tokens_limit == 100
    assert loop.stats.rate_limit_tokens_remaining == 40
    assert loop.stats.rate_limit_captured_at == 123.0
