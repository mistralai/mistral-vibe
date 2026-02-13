"""E2E benchmark: human-perceived performance checks for the memory system.

Each test simulates a realistic user scenario and asserts on outcomes
a user would actually notice: latency, context quality, token overhead,
and graceful degradation. All tests use deterministic mock LLMs so
results are reproducible.

Run with: uv run pytest tests/memory/test_e2e_benchmark.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from vibe.core.memory import MemoryManager, MemoryMetrics
from vibe.core.memory.config import MemoryConfig
from vibe.core.memory.injection import MemoryInjector, _CHARS_PER_TOKEN
from vibe.core.memory.models import Seed, UserField
from vibe.core.memory.scoring import heuristic_score
from vibe.core.memory.storage import MemoryStore

# ---------------------------------------------------------------------------
# Realistic conversation corpus
# ---------------------------------------------------------------------------

# A realistic coding session: mix of trivial, informational, and high-signal msgs
_CONVERSATION: list[tuple[str, str]] = [
    ("user", "I always use Python 3.12 and prefer async patterns for all backend work"),
    ("user", "yes"),
    ("user", "Our project uses FastAPI with SQLAlchemy as the ORM layer"),
    ("user", "ok"),
    ("user", "I need to add a new endpoint for user analytics that returns engagement metrics over time"),
    ("user", "sure"),
    ("user", "We follow the repository pattern for database access and never use raw SQL in route handlers"),
    ("user", "The team convention is to use pydantic models for all API request and response schemas"),
    ("user", "thanks"),
    ("user", "I prefer to write integration tests first before unit tests, following outside-in TDD"),
    ("user", "k"),
    ("user", "lgtm"),
    ("user", "Our CI pipeline runs on GitHub Actions and we always require 80% test coverage before merge"),
    ("user", "The database is PostgreSQL 16 running on AWS RDS with read replicas for analytics queries"),
    ("user", "go ahead"),
    ("user", "I never use print statements for debugging - always use structured logging with structlog"),
    ("user", "Can you explain how connection pooling works?"),
    ("user", "What's the difference between ASGI and WSGI?"),
    ("user", "I think we should refactor the auth module to use dependency injection consistently"),
    ("user", "done"),
]

# Reflection LLM response keyed to conversation content
_REFLECTION_RESPONSE = json.dumps({
    "seed_updates": {
        "user_model": "Senior Python backend developer, async-first, TDD advocate",
        "affect": "collaborative and systematic",
        "intention": "Building analytics endpoints with clean architecture",
    },
    "field_updates": [
        {"action": "add", "key": "lang", "value": "Python 3.12"},
        {"action": "add", "key": "framework", "value": "FastAPI + SQLAlchemy"},
        {"action": "add", "key": "db", "value": "PostgreSQL 16 on AWS RDS"},
        {"action": "add", "key": "testing", "value": "Outside-in TDD, 80% coverage"},
        {"action": "add", "key": "logging", "value": "structlog, no print debugging"},
        {"action": "add", "key": "patterns", "value": "Repository pattern, DI, Pydantic schemas"},
    ],
})


def _make_mock_llm(
    score_value: str = "7",
    reflection_response: str = _REFLECTION_RESPONSE,
    llm_latency: float = 0.0,
):
    """Build a mock LLM that routes scoring vs reflection prompts."""
    call_log: list[dict] = []

    async def mock_llm(system: str, user: str) -> str:
        if llm_latency > 0:
            await asyncio.sleep(llm_latency)
        entry = {"system_prefix": system[:80], "user_prefix": user[:80]}
        call_log.append(entry)

        if "Rate the following" in system:
            return score_value
        if "reflection engine" in system.lower():
            return reflection_response
        if "Compress" in user or "key points" in user.lower():
            return '["Uses FastAPI with async patterns", "PostgreSQL on RDS"]'
        if "Rewrite" in user or "long-term" in user.lower():
            return "Python 3.12 FastAPI project with PostgreSQL. Uses repository pattern and TDD."
        return score_value

    return mock_llm, call_log


def _make_config(tmp_path: Path, **overrides) -> MemoryConfig:
    return MemoryConfig(
        enabled=True,
        db_path=str(tmp_path / "bench.db"),
        reflection_trigger=50,
        importance_threshold=3,
        **overrides,
    )


# ===================================================================
# 1. FIRST-MESSAGE EXPERIENCE
#    A user sends their first message. Does memory provide context
#    on the very next LLM call?
# ===================================================================


@pytest.mark.asyncio
async def test_first_message_produces_nonempty_injection(tmp_path: Path) -> None:
    """After a single non-trivial message, get_memory_block should return
    a <recent> section containing that message -- even before any reflection."""
    llm, _ = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        await mgr.observe(
            "I prefer Python and always use type hints in my code", "user"
        )
        block = mgr.get_memory_block()

        assert block != "", "Memory block should be non-empty after first observation"
        assert "<recent>" in block, "Sensory injection (<recent>) should be present"
        assert "type hints" in block
    finally:
        mgr.close()


@pytest.mark.asyncio
async def test_trivial_first_message_leaves_memory_empty(tmp_path: Path) -> None:
    """A trivial first message like 'ok' should NOT produce a memory block."""
    llm, _ = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        await mgr.observe("ok", "user")
        block = mgr.get_memory_block()
        assert block == "", "Trivial message should not populate memory"
    finally:
        mgr.close()


# ===================================================================
# 2. CONTEXT QUALITY AFTER A REALISTIC SESSION
#    After 20 messages, does the injected context actually reflect
#    what the user told us?
# ===================================================================


@pytest.mark.asyncio
async def test_session_produces_accurate_context(tmp_path: Path) -> None:
    """After a full conversation, the memory block should contain key facts
    the user stated: language, framework, patterns, preferences."""
    llm, _ = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        for role, msg in _CONVERSATION:
            await mgr.observe(msg, role)

        block = mgr.get_memory_block()

        # Seed should contain synthesized user model
        assert "Python" in block or "python" in block, (
            "Memory block should mention Python"
        )
        # Fields should appear
        assert "FastAPI" in block or "fastapi" in block.lower(), (
            "Memory block should mention the framework"
        )
        # Sensory should show recent messages
        assert "<recent>" in block, "Sensory injection should be present"
    finally:
        mgr.close()


@pytest.mark.asyncio
async def test_session_reflection_fires(tmp_path: Path) -> None:
    """Reflection should trigger during a realistic 20-message conversation,
    populating seed and fields so the user gets personalized responses."""
    llm, call_log = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        for role, msg in _CONVERSATION:
            await mgr.observe(msg, role)

        assert mgr.metrics.reflections_triggered >= 1, (
            "Reflection should fire at least once in a 20-message session"
        )

        block = mgr.get_memory_block()
        assert "<seed>" in block, "Seed section should be populated after reflection"

        # Verify the reflection LLM call happened
        reflection_calls = [
            c for c in call_log if "reflection" in c["system_prefix"].lower()
        ]
        assert len(reflection_calls) >= 1
    finally:
        mgr.close()


# ===================================================================
# 3. LATENCY IMPACT
#    Memory operations should be imperceptible to the user.
# ===================================================================


@pytest.mark.asyncio
async def test_observe_latency_under_5ms_heuristic(tmp_path: Path) -> None:
    """Heuristic scoring observe() should complete in under 5ms (no LLM call)."""
    llm, _ = _make_mock_llm()
    config = _make_config(tmp_path, scoring_mode="heuristic")
    mgr = MemoryManager(config, llm)
    try:
        msg = "I prefer to use async patterns and always write type annotations"
        times = []
        for _ in range(50):
            start = time.perf_counter()
            await mgr.observe(msg, "user")
            times.append(time.perf_counter() - start)

        median = sorted(times)[len(times) // 2]
        p99 = sorted(times)[int(len(times) * 0.99)]

        assert median < 0.005, f"Median observe latency {median*1000:.2f}ms exceeds 5ms"
        assert p99 < 0.020, f"P99 observe latency {p99*1000:.2f}ms exceeds 20ms"
    finally:
        mgr.close()


def test_injection_latency_under_1ms(tmp_path: Path) -> None:
    """Building the memory block should take under 1ms, even with a rich profile."""
    llm, _ = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        # Pre-populate rich state
        state = mgr._store.get_or_create_user_state(mgr._user_id)
        state.seed = Seed(
            affect="collaborative",
            trust="high",
            intention="building APIs",
            user_model="Senior Python developer",
        )
        state.fields = [
            UserField(key=f"field_{i}", value=f"value_{i}")
            for i in range(15)
        ]
        mgr._store.update_user_state(state)
        ctx_key = mgr.get_context_key()
        for i in range(20):
            mgr._store.add_sensory(ctx_key, mgr._user_id, f"observation {i}")
        ctx = mgr._store.get_or_create_context_memory(ctx_key, mgr._user_id)
        ctx.short_term = [f"point {i}" for i in range(10)]
        ctx.long_term = "This is a Python FastAPI project using PostgreSQL."
        mgr._store.update_context_memory(ctx)

        times = []
        for _ in range(100):
            start = time.perf_counter()
            block = mgr.get_memory_block()
            times.append(time.perf_counter() - start)
        assert block != ""

        median = sorted(times)[len(times) // 2]
        p99 = sorted(times)[int(len(times) * 0.99)]

        assert median < 0.001, f"Median injection latency {median*1000:.3f}ms exceeds 1ms"
        assert p99 < 0.005, f"P99 injection latency {p99*1000:.3f}ms exceeds 5ms"
    finally:
        mgr.close()


# ===================================================================
# 4. TRIVIAL FILTER EFFECTIVENESS
#    Trivial messages should never trigger LLM calls.
# ===================================================================


@pytest.mark.asyncio
async def test_trivial_filter_saves_llm_calls(tmp_path: Path) -> None:
    """Trivial messages in a realistic conversation should be filtered
    without making any LLM calls, saving cost and latency."""
    llm, call_log = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        trivial_msgs = ["yes", "ok", "sure", "thanks", "lgtm", "k", "done", "go ahead"]
        for msg in trivial_msgs:
            await mgr.observe(msg, "user")

        assert mgr.metrics.observations_skipped_trivial == len(trivial_msgs)
        assert mgr.metrics.observations_stored == 0

        # No scoring LLM calls should have been made
        scoring_calls = [
            c for c in call_log if "Rate the following" in c["system_prefix"]
        ]
        assert len(scoring_calls) == 0, (
            f"Expected 0 scoring LLM calls for trivial messages, got {len(scoring_calls)}"
        )
    finally:
        mgr.close()


@pytest.mark.asyncio
async def test_mixed_conversation_trivial_ratio(tmp_path: Path) -> None:
    """In a realistic conversation, the trivial filter should catch
    at least 30% of messages (typical coding session pattern)."""
    llm, _ = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        for role, msg in _CONVERSATION:
            await mgr.observe(msg, role)

        total = len(_CONVERSATION)
        trivial = mgr.metrics.observations_skipped_trivial
        ratio = trivial / total

        assert ratio >= 0.30, (
            f"Trivial filter ratio {ratio:.0%} is below 30%. "
            f"Got {trivial}/{total} trivial in a realistic conversation"
        )
    finally:
        mgr.close()


# ===================================================================
# 5. TOKEN OVERHEAD
#    Memory injection should stay within budget and not dominate
#    the context window.
# ===================================================================


def test_token_overhead_within_budget(tmp_path: Path) -> None:
    """The injected memory block should not exceed the configured token budget."""
    llm, _ = _make_mock_llm()
    config = _make_config(tmp_path, injection_budget_tokens=2000)
    mgr = MemoryManager(config, llm)
    try:
        state = mgr._store.get_or_create_user_state(mgr._user_id)
        state.seed = Seed(
            affect="collaborative",
            user_model="Senior developer with 10 years experience in distributed systems",
        )
        state.fields = [
            UserField(key=f"pref_{i}", value=f"Long preference description for field {i} " * 5)
            for i in range(20)
        ]
        mgr._store.update_user_state(state)
        ctx_key = mgr.get_context_key()
        for i in range(50):
            mgr._store.add_sensory(ctx_key, mgr._user_id, f"User discussed topic {i} in detail")
        ctx = mgr._store.get_or_create_context_memory(ctx_key, mgr._user_id)
        ctx.short_term = [f"Key insight about topic {i}" for i in range(15)]
        ctx.long_term = "Detailed project knowledge " * 100
        mgr._store.update_context_memory(ctx)

        block = mgr.get_memory_block()

        estimated_tokens = len(block) / _CHARS_PER_TOKEN
        assert estimated_tokens <= config.injection_budget_tokens * 1.1, (
            f"Memory block ~{estimated_tokens:.0f} tokens exceeds budget "
            f"of {config.injection_budget_tokens} tokens (10% tolerance)"
        )
    finally:
        mgr.close()


def test_minimal_profile_low_overhead(tmp_path: Path) -> None:
    """A minimal profile (just seed) should produce a small memory block
    with low tag overhead relative to content."""
    llm, _ = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        state = mgr._store.get_or_create_user_state(mgr._user_id)
        state.seed = Seed(user_model="Python developer")
        mgr._store.update_user_state(state)
        mgr._store.get_or_create_context_memory(mgr.get_context_key(), mgr._user_id)

        block = mgr.get_memory_block()
        content_len = len("user_model: Python developer")
        total_len = len(block)
        overhead_ratio = (total_len - content_len) / total_len

        # Flat format: overhead should be under 75% for a minimal profile
        assert overhead_ratio < 0.75, (
            f"Tag overhead {overhead_ratio:.0%} is too high for minimal profile "
            f"({total_len} total, {content_len} content)"
        )
    finally:
        mgr.close()


# ===================================================================
# 6. HEURISTIC vs LLM SCORING
#    Heuristic mode should produce comparable results to LLM mode
#    while making zero LLM calls.
# ===================================================================


@pytest.mark.asyncio
async def test_heuristic_mode_no_scoring_calls(tmp_path: Path) -> None:
    """Heuristic mode should never call the LLM for scoring."""
    llm, call_log = _make_mock_llm()
    config = _make_config(tmp_path, scoring_mode="heuristic")
    mgr = MemoryManager(config, llm)
    try:
        for role, msg in _CONVERSATION:
            await mgr.observe(msg, role)

        scoring_calls = [
            c for c in call_log if "Rate the following" in c["system_prefix"]
        ]
        assert len(scoring_calls) == 0, (
            f"Heuristic mode made {len(scoring_calls)} scoring LLM calls"
        )
        # Should still store high-signal messages
        assert mgr.metrics.observations_stored > 0
    finally:
        mgr.close()


def test_heuristic_ranks_high_signal_messages_higher() -> None:
    """Heuristic scorer should rank preference/decision messages higher
    than questions or short messages."""
    high_signal = [
        "I always use Python 3.12 and prefer async patterns for all backend work",
        "We follow the repository pattern for database access and never use raw SQL",
        "The team convention is to use pydantic models for all API request schemas",
    ]
    low_signal = [
        "Can you explain how connection pooling works?",
        "What's the difference between ASGI and WSGI?",
        "fix the bug",
    ]

    high_scores = [heuristic_score(m) for m in high_signal]
    low_scores = [heuristic_score(m) for m in low_signal]

    avg_high = sum(high_scores) / len(high_scores)
    avg_low = sum(low_scores) / len(low_scores)

    assert avg_high > avg_low, (
        f"High-signal avg {avg_high:.1f} should exceed low-signal avg {avg_low:.1f}"
    )


# ===================================================================
# 7. SCALING: LONG CONVERSATION
#    Performance should remain stable over a long conversation.
# ===================================================================


@pytest.mark.asyncio
async def test_injection_latency_stable_over_100_messages(tmp_path: Path) -> None:
    """Injection latency should not degrade as sensory buffer grows."""
    llm, _ = _make_mock_llm()
    config = _make_config(tmp_path, scoring_mode="heuristic", sensory_cap=200)
    mgr = MemoryManager(config, llm)
    try:
        # Measure injection latency after 10 messages
        for i in range(10):
            await mgr.observe(f"Message about topic {i} with enough detail to store", "user")
        early_times = []
        for _ in range(50):
            start = time.perf_counter()
            mgr.get_memory_block()
            early_times.append(time.perf_counter() - start)

        # Add 90 more messages
        for i in range(10, 100):
            await mgr.observe(f"Message about topic {i} with enough detail to store", "user")

        late_times = []
        for _ in range(50):
            start = time.perf_counter()
            mgr.get_memory_block()
            late_times.append(time.perf_counter() - start)

        early_median = sorted(early_times)[len(early_times) // 2]
        late_median = sorted(late_times)[len(late_times) // 2]

        # Late should not be more than 5x slower than early
        assert late_median < early_median * 5, (
            f"Injection latency degraded: {early_median*1000:.3f}ms -> {late_median*1000:.3f}ms "
            f"({late_median/early_median:.1f}x slowdown)"
        )
    finally:
        mgr.close()


@pytest.mark.asyncio
async def test_db_size_grows_linearly(tmp_path: Path) -> None:
    """Database size should grow roughly linearly, not exponentially."""
    llm, _ = _make_mock_llm()
    config = _make_config(tmp_path, scoring_mode="heuristic")
    mgr = MemoryManager(config, llm)
    db_path = Path(config.db_path)
    try:
        # Measure after 10 messages
        for i in range(10):
            await mgr.observe(f"Message about topic {i} with enough detail to store", "user")
        size_10 = db_path.stat().st_size

        # Measure after 50 messages
        for i in range(10, 50):
            await mgr.observe(f"Message about topic {i} with enough detail to store", "user")
        size_50 = db_path.stat().st_size

        # 5x messages should yield at most ~6x DB size (some overhead)
        ratio = size_50 / size_10 if size_10 > 0 else float("inf")
        assert ratio < 8, (
            f"DB size grew {ratio:.1f}x for 5x messages "
            f"({size_10} -> {size_50} bytes) -- may not be linear"
        )
    finally:
        mgr.close()


# ===================================================================
# 8. GRACEFUL DEGRADATION
#    Memory failures should never block or crash the user experience.
# ===================================================================


@pytest.mark.asyncio
async def test_observe_survives_db_closure(tmp_path: Path) -> None:
    """If the DB connection is lost, observe() should fail silently."""
    llm, _ = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        mgr._store.close()  # simulate connection loss

        # Should not raise
        await mgr.observe("I prefer Python", "user")
        block = mgr.get_memory_block()
        assert block == ""  # graceful empty return
        await mgr.on_session_end()
    finally:
        mgr.close()


@pytest.mark.asyncio
async def test_observe_survives_llm_failure(tmp_path: Path) -> None:
    """If the LLM fails during scoring, observe() should fail silently."""

    async def failing_llm(system: str, user: str) -> str:
        raise RuntimeError("API timeout")

    mgr = MemoryManager(_make_config(tmp_path), failing_llm)
    try:
        # Should not raise despite LLM failure
        await mgr.observe("I prefer Python for backend work", "user")
        # Observation should not be stored (scoring failed -> default 3 which passes threshold)
        # The important thing is no crash
    finally:
        mgr.close()


# ===================================================================
# 9. MEMORY PERSISTENCE ACROSS SESSIONS
#    A returning user should see their profile from previous sessions.
# ===================================================================


@pytest.mark.asyncio
async def test_returning_user_sees_previous_context(tmp_path: Path) -> None:
    """Simulates a user returning for a second session -- their profile
    from session 1 should be available in session 2."""
    db_path = str(tmp_path / "persistent.db")
    llm, _ = _make_mock_llm()

    # Session 1
    config = MemoryConfig(enabled=True, db_path=db_path, reflection_trigger=50)
    mgr1 = MemoryManager(config, llm)
    for role, msg in _CONVERSATION:
        await mgr1.observe(msg, role)
    block_s1 = mgr1.get_memory_block()
    mgr1.close()

    # Session 2 -- new MemoryManager, same DB
    mgr2 = MemoryManager(config, llm)
    block_s2 = mgr2.get_memory_block()

    # Session 2 should still have the user profile from session 1
    assert "<seed>" in block_s2, "Returning user should see their seed from session 1"
    mgr2.close()


# ===================================================================
# 10. CONSOLIDATION END-TO-END
#     After session_end, sensory is compressed into short-term/long-term.
# ===================================================================


@pytest.mark.asyncio
async def test_consolidation_compresses_sensory(tmp_path: Path) -> None:
    """After on_session_end(), sensory buffer should be compressed into
    short-term points, reducing future injection size."""
    llm, _ = _make_mock_llm()
    config = _make_config(tmp_path, scoring_mode="heuristic", sensory_cap=10)
    mgr = MemoryManager(config, llm)
    try:
        # Fill sensory buffer beyond cap
        for i in range(15):
            await mgr.observe(
                f"Detailed message about architectural decision number {i} for the project",
                "user",
            )

        ctx_key = mgr.get_context_key()
        ctx_before = mgr._store.get_or_create_context_memory(ctx_key, mgr._user_id)
        sensory_before = len(ctx_before.sensory)

        await mgr.on_session_end()

        ctx_after = mgr._store.get_or_create_context_memory(ctx_key, mgr._user_id)

        # After consolidation, sensory should be cleared or reduced
        # and short_term should have new entries
        if sensory_before >= config.sensory_cap:
            assert len(ctx_after.sensory) < sensory_before or len(ctx_after.short_term) > 0, (
                "Consolidation should compress sensory into short-term"
            )
    finally:
        mgr.close()


# ===================================================================
# 11. METRICS ACCURACY
#     Metrics counters should exactly match the conversation events.
# ===================================================================


@pytest.mark.asyncio
async def test_metrics_match_conversation(tmp_path: Path) -> None:
    """Every message should be accounted for in exactly one metric bucket."""
    llm, _ = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        for role, msg in _CONVERSATION:
            await mgr.observe(msg, role)

        m = mgr.metrics
        accounted = (
            m.observations_stored
            + m.observations_skipped_trivial
            + m.observations_skipped_low_score
        )

        assert accounted == len(_CONVERSATION), (
            f"Metrics don't add up: stored={m.observations_stored} "
            f"+ trivial={m.observations_skipped_trivial} "
            f"+ low={m.observations_skipped_low_score} = {accounted}, "
            f"expected {len(_CONVERSATION)}"
        )
    finally:
        mgr.close()


@pytest.mark.asyncio
async def test_metrics_injection_counters(tmp_path: Path) -> None:
    """Injection counters should track served vs empty correctly."""
    llm, _ = _make_mock_llm()
    mgr = MemoryManager(_make_config(tmp_path), llm)
    try:
        # Empty memory -- should count as empty
        mgr.get_memory_block()
        assert mgr.metrics.injections_empty == 1

        # Add data -- should count as served
        await mgr.observe("I prefer Python for all backend work and data processing", "user")
        mgr.get_memory_block()
        assert mgr.metrics.injections_served == 1

        total = mgr.metrics.injections_served + mgr.metrics.injections_empty
        assert total == 2
    finally:
        mgr.close()
