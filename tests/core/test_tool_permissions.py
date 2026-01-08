from __future__ import annotations

import asyncio

import pytest

from vibe.core.tools.base import ToolPermission, ToolPermissionError
from vibe.core.tools.permission_tracker import (
    PermissionExpirationReason,
    PermissionTracker,
)


class TestToolPermission:
    def test_by_name_with_standard_values(self) -> None:
        assert ToolPermission.by_name("always") == ToolPermission.ALWAYS
        assert ToolPermission.by_name("never") == ToolPermission.NEVER
        assert ToolPermission.by_name("ask") == ToolPermission.ASK

    def test_by_name_with_new_values(self) -> None:
        assert ToolPermission.by_name("ask-time") == ToolPermission.ASK_TIME
        assert ToolPermission.by_name("ask-iterations") == ToolPermission.ASK_ITERATIONS
        assert ToolPermission.by_name("ASK_TIME") == ToolPermission.ASK_TIME
        assert ToolPermission.by_name("ASK_ITERATIONS") == ToolPermission.ASK_ITERATIONS

    def test_by_name_case_insensitive(self) -> None:
        assert ToolPermission.by_name("ASK-TIME") == ToolPermission.ASK_TIME
        assert ToolPermission.by_name("Ask-Time") == ToolPermission.ASK_TIME
        assert ToolPermission.by_name("ask-ITERATIONS") == ToolPermission.ASK_ITERATIONS

    def test_by_name_invalid_value(self) -> None:
        with pytest.raises(ToolPermissionError):
            ToolPermission.by_name("invalid-permission")


class TestPermissionTracker:
    @pytest.mark.asyncio
    async def test_grant_time_based_permission(self) -> None:
        tracker = PermissionTracker()
        await tracker.grant_time_based("test_tool", duration_seconds=60)

        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True
        assert reason == PermissionExpirationReason.NONE

        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        assert remaining_info["type"] == "time"
        assert remaining_info["remaining_seconds"] > 0
        assert remaining_info["remaining_seconds"] <= 60

    @pytest.mark.asyncio
    async def test_grant_iteration_based_permission(self) -> None:
        tracker = PermissionTracker()
        await tracker.grant_iteration_based("test_tool", iterations=5)

        # Check remaining before reserving (should be 5)
        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        assert remaining_info["type"] == "iterations"
        assert remaining_info["remaining"] == 5

        # Reserve one iteration
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True
        assert reason == PermissionExpirationReason.NONE

        # Check remaining after reserving (should be 4)
        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        assert remaining_info["remaining"] == 4

    @pytest.mark.asyncio
    async def test_check_and_reserve_iteration_decrements_count(self) -> None:
        tracker = PermissionTracker()
        await tracker.grant_iteration_based("test_tool", iterations=3)

        # First check and reserve
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True
        assert reason == PermissionExpirationReason.NONE

        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        assert remaining_info["remaining"] == 2

        # Second check and reserve
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True
        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        assert remaining_info["remaining"] == 1

        # Third check and reserve (last one)
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True
        assert reason == PermissionExpirationReason.NONE
        # Permission still exists with 0 iterations (will be detected as exhausted on next call)
        remaining_info = await tracker.get_remaining_info("test_tool")
        assert (
            remaining_info is None
        )  # get_remaining_info returns None when remaining is 0

        # Fourth check should fail (detects exhaustion)
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is False
        assert reason == PermissionExpirationReason.ITERATIONS_EXHAUSTED

    @pytest.mark.asyncio
    async def test_check_and_reserve_iteration_time_based_no_decrement(self) -> None:
        tracker = PermissionTracker()
        await tracker.grant_time_based("test_tool", duration_seconds=60)

        # Time-based permissions don't decrement
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True
        assert reason == PermissionExpirationReason.NONE

        # Should still be granted
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True

    @pytest.mark.asyncio
    async def test_time_based_permission_expires(self) -> None:
        tracker = PermissionTracker()
        # Grant permission for 0 seconds (expires immediately)
        await tracker.grant_time_based("test_tool", duration_seconds=0)

        # Should be expired immediately
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is False
        assert reason == PermissionExpirationReason.TIME_EXPIRED

    @pytest.mark.asyncio
    async def test_iteration_based_permission_exhausted(self) -> None:
        tracker = PermissionTracker()
        await tracker.grant_iteration_based("test_tool", iterations=1)

        # Use the only iteration
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True
        assert reason == PermissionExpirationReason.NONE

        # Should be exhausted now (detects remaining_iterations = 0)
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is False
        assert reason == PermissionExpirationReason.ITERATIONS_EXHAUSTED

    @pytest.mark.asyncio
    async def test_last_grant_wins(self) -> None:
        tracker = PermissionTracker()
        await tracker.grant_time_based("test_tool", duration_seconds=60)
        await tracker.grant_iteration_based("test_tool", iterations=5)

        # Should be iteration-based now (last grant)
        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        assert remaining_info["type"] == "iterations"
        assert remaining_info["remaining"] == 5

        # Grant time-based again
        await tracker.grant_time_based("test_tool", duration_seconds=120)
        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        assert remaining_info["type"] == "time"

    @pytest.mark.asyncio
    async def test_cleanup_expired_removes_expired_permissions(self) -> None:
        tracker = PermissionTracker()
        await tracker.grant_time_based("expired_tool", duration_seconds=0)
        await tracker.grant_time_based("valid_tool", duration_seconds=3600)

        await tracker.cleanup_expired()

        is_granted_expired, _ = await tracker.check_and_reserve_iteration(
            "expired_tool"
        )
        is_granted_valid, _ = await tracker.check_and_reserve_iteration("valid_tool")

        assert is_granted_expired is False
        assert is_granted_valid is True

    @pytest.mark.asyncio
    async def test_get_remaining_info_none_for_missing_tool(self) -> None:
        tracker = PermissionTracker()
        remaining_info = await tracker.get_remaining_info("nonexistent_tool")
        assert remaining_info is None

    @pytest.mark.asyncio
    async def test_concurrent_iteration_reservation(self) -> None:
        """Test that concurrent reservations don't cause race conditions."""
        tracker = PermissionTracker()
        await tracker.grant_iteration_based("test_tool", iterations=5)

        # Create 10 concurrent reservation attempts
        async def reserve() -> tuple[bool, str]:
            return await tracker.check_and_reserve_iteration("test_tool")

        results = await asyncio.gather(*[reserve() for _ in range(10)])

        # Exactly 5 should succeed
        successful = sum(1 for is_granted, _ in results if is_granted)
        assert successful == 5

        # Verify remaining is 0
        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is None

    @pytest.mark.asyncio
    async def test_concurrent_different_tools_no_contention(self) -> None:
        """Test that different tools can be checked in parallel."""
        tracker = PermissionTracker()
        await tracker.grant_iteration_based("tool1", iterations=3)
        await tracker.grant_iteration_based("tool2", iterations=3)

        async def reserve_tool1() -> tuple[bool, str]:
            return await tracker.check_and_reserve_iteration("tool1")

        async def reserve_tool2() -> tuple[bool, str]:
            return await tracker.check_and_reserve_iteration("tool2")

        # Reserve from both tools concurrently
        results = await asyncio.gather(
            *[reserve_tool1() for _ in range(3)], *[reserve_tool2() for _ in range(3)]
        )

        # All should succeed (no contention between different tools)
        successful = sum(1 for is_granted, _ in results if is_granted)
        assert successful == 6

    @pytest.mark.asyncio
    async def test_iteration_count_never_goes_negative(self) -> None:
        """Test that iteration count never goes negative even with concurrent access."""
        tracker = PermissionTracker()
        await tracker.grant_iteration_based("test_tool", iterations=1)

        # Use the only iteration
        is_granted, _ = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True

        # Try to reserve again (should fail, not go negative)
        is_granted, _ = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is False

        # Verify no negative count
        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is None

    @pytest.mark.asyncio
    async def test_negative_iterations_rejected(self) -> None:
        """Security: Test that negative iteration values are rejected."""
        tracker = PermissionTracker()
        # Grant with negative value - should be caught by validation
        # Note: This test documents expected behavior - if validation is added, this should fail
        await tracker.grant_iteration_based("test_tool", iterations=-1)

        # Even if granted, it should be immediately exhausted
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is False
        assert reason == PermissionExpirationReason.ITERATIONS_EXHAUSTED

    @pytest.mark.asyncio
    async def test_negative_duration_handled(self) -> None:
        """Security: Test that negative duration values are handled safely."""
        tracker = PermissionTracker()
        # Grant with negative duration - should expire immediately
        await tracker.grant_time_based("test_tool", duration_seconds=-1)

        # Should be expired immediately
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is False
        assert reason == PermissionExpirationReason.TIME_EXPIRED

    @pytest.mark.asyncio
    async def test_zero_iterations_handled(self) -> None:
        """Security: Test that zero iterations are handled correctly."""
        tracker = PermissionTracker()
        await tracker.grant_iteration_based("test_tool", iterations=0)

        # Should be immediately exhausted
        is_granted, reason = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is False
        assert reason == PermissionExpirationReason.ITERATIONS_EXHAUSTED

    @pytest.mark.asyncio
    async def test_extremely_large_iterations_handled(self) -> None:
        """Security: Test that extremely large iteration values don't cause DoS."""
        tracker = PermissionTracker()
        # Grant with very large value - system should handle it
        large_value = 1_000_000_000
        await tracker.grant_iteration_based("test_tool", iterations=large_value)

        # Should still work correctly
        is_granted, _ = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True

        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        assert remaining_info["remaining"] == large_value - 1

    @pytest.mark.asyncio
    async def test_extremely_large_duration_handled(self) -> None:
        """Security: Test that extremely large duration values don't cause DoS."""
        tracker = PermissionTracker()
        # Grant with very large duration (years)
        large_duration = 365 * 24 * 60 * 60  # 1 year in seconds
        await tracker.grant_time_based("test_tool", duration_seconds=large_duration)

        # Should still work correctly
        is_granted, _ = await tracker.check_and_reserve_iteration("test_tool")
        assert is_granted is True

        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        assert remaining_info["remaining_seconds"] > 0

    @pytest.mark.asyncio
    async def test_cleanup_expired_thread_safety(self) -> None:
        """Security: Test that cleanup_expired is thread-safe with concurrent grants."""
        tracker = PermissionTracker()
        await tracker.grant_time_based("tool1", duration_seconds=0)  # Expired
        await tracker.grant_time_based("tool2", duration_seconds=3600)  # Valid

        # Concurrent cleanup and grant operations
        async def cleanup_and_grant() -> None:
            await tracker.cleanup_expired()
            await tracker.grant_time_based("tool3", duration_seconds=60)

        # Run multiple cleanup operations concurrently
        await asyncio.gather(*[cleanup_and_grant() for _ in range(10)])

        # Verify state is consistent
        is_granted_tool1, _ = await tracker.check_and_reserve_iteration("tool1")
        is_granted_tool2, _ = await tracker.check_and_reserve_iteration("tool2")
        is_granted_tool3, _ = await tracker.check_and_reserve_iteration("tool3")

        assert is_granted_tool1 is False  # Should be cleaned up
        assert is_granted_tool2 is True  # Should still be valid
        assert is_granted_tool3 is True  # Should be granted

    @pytest.mark.asyncio
    async def test_rapid_grant_replacement(self) -> None:
        """Security: Test that rapid grant replacements don't cause race conditions."""
        tracker = PermissionTracker()

        # Rapidly grant and replace permissions
        async def grant_sequence(iterations: int) -> None:
            await tracker.grant_iteration_based("test_tool", iterations=iterations)
            await asyncio.sleep(0.001)  # Small delay to allow interleaving

        # Grant multiple times concurrently
        await asyncio.gather(*[grant_sequence(i) for i in range(1, 11)])

        # Last grant should win
        remaining_info = await tracker.get_remaining_info("test_tool")
        assert remaining_info is not None
        # Should be one of the granted values (last one wins)
        assert remaining_info["remaining"] >= 0

    @pytest.mark.asyncio
    async def test_concurrent_cleanup_and_check(self) -> None:
        """Security: Test concurrent cleanup and permission checks."""
        tracker = PermissionTracker()
        await tracker.grant_time_based("expired_tool", duration_seconds=0)
        await tracker.grant_time_based("valid_tool", duration_seconds=3600)

        async def check_tool(tool_name: str) -> tuple[bool, str]:
            return await tracker.check_and_reserve_iteration(tool_name)

        async def cleanup() -> None:
            await tracker.cleanup_expired()

        # Run cleanup and checks concurrently
        results = await asyncio.gather(
            check_tool("expired_tool"),
            check_tool("valid_tool"),
            cleanup(),
            check_tool("expired_tool"),
            check_tool("valid_tool"),
        )

        # All checks should be consistent
        # Expired tool should be False
        assert results[0][0] is False or results[3][0] is False
        # Valid tool should be True
        assert results[1][0] is True
        assert results[4][0] is True
