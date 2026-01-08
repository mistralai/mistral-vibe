from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel


class TemporaryPermission(BaseModel):
    tool_name: str
    expires_at: datetime | None = None  # For time-based
    remaining_iterations: int | None = None  # For iteration-based
    granted_at: datetime


class PermissionExpirationReason:
    TIME_EXPIRED = "time_expired"
    ITERATIONS_EXHAUSTED = "iterations_exhausted"
    NONE = "none"  # Permission still valid


class PermissionTracker:
    """Thread-safe tracker for temporary tool permissions.

    Tracks time-based and iteration-based temporary permissions with
    atomic operations to prevent race conditions.
    """

    def __init__(self) -> None:
        self._permissions: dict[str, TemporaryPermission] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_lock = asyncio.Lock()  # For lock dictionary access

    async def _get_lock(self, tool_name: str) -> asyncio.Lock:
        """Get or create lock for tool_name."""
        async with self._lock_lock:
            if tool_name not in self._locks:
                self._locks[tool_name] = asyncio.Lock()
            return self._locks[tool_name]

    async def grant_time_based(self, tool_name: str, duration_seconds: int) -> None:
        """Grant time-based temporary permission.

        Replaces any existing temporary permission (last grant wins).

        Args:
            tool_name: Name of the tool
            duration_seconds: Duration in seconds for the permission.
                Must be non-negative. Zero or negative values result in
                immediate expiration.

        Security:
            Negative values are accepted but result in immediate expiration
            to prevent unexpected behavior. For security, consider validating
            at the caller level to reject negative values explicitly.
        """
        async with await self._get_lock(tool_name):
            now = datetime.now()
            # Note: Negative duration_seconds will result in immediate expiration
            # This is safe but may not be the intended behavior
            expires_at = now + timedelta(seconds=duration_seconds)
            self._permissions[tool_name] = TemporaryPermission(
                tool_name=tool_name,
                expires_at=expires_at,
                remaining_iterations=None,
                granted_at=now,
            )

    async def grant_iteration_based(self, tool_name: str, iterations: int) -> None:
        """Grant iteration-based temporary permission.

        Replaces any existing temporary permission (last grant wins).

        Args:
            tool_name: Name of the tool
            iterations: Number of iterations to grant.
                Must be non-negative. Zero or negative values result in
                immediate exhaustion.

        Security:
            Negative values are accepted but result in immediate exhaustion
            to prevent unexpected behavior. For security, consider validating
            at the caller level to reject negative values explicitly.
        """
        async with await self._get_lock(tool_name):
            now = datetime.now()
            # Note: Negative or zero iterations will result in immediate exhaustion
            # This is safe but may not be the intended behavior
            self._permissions[tool_name] = TemporaryPermission(
                tool_name=tool_name,
                expires_at=None,
                remaining_iterations=iterations,
                granted_at=now,
            )

    async def check_and_reserve_iteration(self, tool_name: str) -> tuple[bool, str]:
        """Atomically check if iteration available and reserve it.

        This operation is atomic and thread-safe. If an iteration is
        available, it is immediately decremented.

        Args:
            tool_name: Name of the tool to check

        Returns:
            Tuple of (is_granted, expiration_reason):
            - is_granted: True if permission granted, False if exhausted/expired
            - expiration_reason: Reason if not granted, "none" if granted
        """
        async with await self._get_lock(tool_name):
            perm = self._permissions.get(tool_name)
            if perm is None:
                return False, PermissionExpirationReason.NONE

            # Check time-based expiration
            if perm.expires_at is not None:
                if datetime.now() >= perm.expires_at:
                    del self._permissions[tool_name]
                    return False, PermissionExpirationReason.TIME_EXPIRED

            # Check iteration-based exhaustion
            if perm.remaining_iterations is not None:
                if perm.remaining_iterations <= 0:
                    # Already exhausted - delete and return exhausted reason
                    del self._permissions[tool_name]
                    return False, PermissionExpirationReason.ITERATIONS_EXHAUSTED

                # Atomic decrement
                perm.remaining_iterations -= 1
                # Don't delete when it reaches 0 - keep it so next call can detect exhaustion
                # The permission will be cleaned up on the next call when it's detected as exhausted
                return True, PermissionExpirationReason.NONE

            # Time-based permission (no iteration limit)
            return True, PermissionExpirationReason.NONE

    async def is_granted(self, tool_name: str) -> tuple[bool, str]:
        """Check if permission is valid (read-only check).

        Does not modify permission state. For iteration-based permissions,
        use check_and_reserve_iteration() instead.

        Args:
            tool_name: Name of the tool to check

        Returns:
            Tuple of (is_granted, expiration_reason):
            - is_granted: True if permission is valid, False if expired
            - expiration_reason: Reason if expired, "none" if valid
        """
        async with await self._get_lock(tool_name):
            perm = self._permissions.get(tool_name)
            if perm is None:
                return False, PermissionExpirationReason.NONE

            # Check time-based expiration
            if perm.expires_at is not None:
                if datetime.now() >= perm.expires_at:
                    del self._permissions[tool_name]
                    return False, PermissionExpirationReason.TIME_EXPIRED

            # Check iteration-based exhaustion
            if perm.remaining_iterations is not None:
                if perm.remaining_iterations <= 0:
                    del self._permissions[tool_name]
                    return False, PermissionExpirationReason.ITERATIONS_EXHAUSTED

            return True, PermissionExpirationReason.NONE

    async def get_remaining_info(self, tool_name: str) -> dict[str, Any] | None:
        """Get remaining time or iterations for display in UI.

        Args:
            tool_name: Name of the tool

        Returns:
            Dictionary with remaining info or None if no permission exists:
            - For time-based: {"type": "time", "remaining_seconds": int}
            - For iteration-based: {"type": "iterations", "remaining": int}
        """
        async with await self._get_lock(tool_name):
            perm = self._permissions.get(tool_name)
            if perm is None:
                return None

            # Check if expired
            if perm.expires_at is not None:
                now = datetime.now()
                if now >= perm.expires_at:
                    return None
                remaining_seconds = int((perm.expires_at - now).total_seconds())
                return {"type": "time", "remaining_seconds": remaining_seconds}

            if perm.remaining_iterations is not None:
                if perm.remaining_iterations <= 0:
                    return None
                return {"type": "iterations", "remaining": perm.remaining_iterations}

            return None

    async def cleanup_expired(self) -> None:
        """Remove expired time-based permissions.

        This is called periodically to clean up expired permissions.
        Iteration-based permissions are cleaned up automatically when exhausted.

        Security:
            This method uses a two-phase approach to minimize lock contention:
            1. First, collect expired tool names (without holding locks)
            2. Then, acquire locks individually to remove them
            This prevents race conditions where permissions might be modified
            between collection and removal, as we re-check expiration under lock.
        """
        now = datetime.now()
        expired_tools = []

        # Collect expired tools (need to check all, but minimize lock time)
        # Create a snapshot to avoid holding lock during iteration
        # Note: This is safe because we re-check expiration under lock below
        for tool_name, perm in list(self._permissions.items()):
            if perm.expires_at is not None and now >= perm.expires_at:
                expired_tools.append(tool_name)

        # Remove expired permissions (with locks)
        # Re-check expiration under lock to handle race conditions
        for tool_name in expired_tools:
            async with await self._get_lock(tool_name):
                perm = self._permissions.get(tool_name)
                if perm is not None and perm.expires_at is not None:
                    # Re-check expiration under lock (handles race conditions)
                    if now >= perm.expires_at:
                        del self._permissions[tool_name]
