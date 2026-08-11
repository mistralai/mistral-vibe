"""Utilities package. Re-exports all public and test-used symbols from submodules.

Import read_safe / read_safe_async / decode_safe (returns ReadSafeResult) from vibe.utils.io and create_slug from
vibe.core.utils.slug when needed to avoid circular imports with config.
"""

from __future__ import annotations

from vibe.core.utils.async_subprocess import kill_async_subprocess
from vibe.core.utils.concurrency import (
    AsyncExecutor,
    ConversationLimitException,
    run_sync,
)
from vibe.core.utils.matching import name_matches
from vibe.core.utils.merge import MergeConflictError, MergeStrategy
from vibe.core.utils.retry import (
    RetryCategory,
    RetryObserver,
    RetryReason,
    async_generator_retry,
    async_retry,
)
from vibe.core.utils.sse import iter_sse_lines
from vibe.core.utils.tags import (
    CANCELLATION_TAG,
    KNOWN_TAGS,
    TOOL_ERROR_TAG,
    VIBE_STOP_EVENT_TAG,
    CancellationReason,
    TaggedText,
    get_user_cancellation_message,
    is_user_cancellation_event,
)
from vibe.core.utils.time import utc_now
from vibe.utils.paths import is_dangerous_directory
from vibe.utils.platform import (
    WindowsShell,
    WindowsShellKind,
    get_platform_display_name,
    get_platform_id,
    get_platform_version,
    get_windows_bash_path,
    is_windows,
    resolve_windows_shell,
)

__all__ = [
    "CANCELLATION_TAG",
    "KNOWN_TAGS",
    "TOOL_ERROR_TAG",
    "VIBE_STOP_EVENT_TAG",
    "AsyncExecutor",
    "CancellationReason",
    "ConversationLimitException",
    "MergeConflictError",
    "MergeStrategy",
    "RetryCategory",
    "RetryObserver",
    "RetryReason",
    "TaggedText",
    "WindowsShell",
    "WindowsShellKind",
    "async_generator_retry",
    "async_retry",
    "get_platform_display_name",
    "get_platform_id",
    "get_platform_version",
    "get_user_cancellation_message",
    "get_windows_bash_path",
    "is_dangerous_directory",
    "is_user_cancellation_event",
    "is_windows",
    "iter_sse_lines",
    "kill_async_subprocess",
    "name_matches",
    "resolve_windows_shell",
    "run_sync",
    "utc_now",
]
