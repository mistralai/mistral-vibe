"""Map a failed session resume to the message shown in the transcript.

Most resume failures surface the underlying error verbatim. A session written
by a newer build is different: the store is intact and only needs a newer
reader, so the user is told to update Vibe rather than shown an internal
store-format error.
"""

from __future__ import annotations

from vibe.app_server.protocol import AppServerResponseError

# Carried in the protocol error's ``data.harnessCode`` when a session store was
# written by a newer store-format minor than this build can read.
STORE_REQUIRES_NEWER_READER_CODE = "store_requires_newer_reader"

_UPGRADE_MESSAGE = (
    "This session was created by a newer version of Vibe. "
    "Restart with --check-upgrade to update Vibe, then resume this session."
)


def is_store_requires_newer_reader(error: BaseException) -> bool:
    """Report whether a resume failed because the store needs a newer Vibe."""
    if not isinstance(error, AppServerResponseError):
        return False
    data = error.error.data
    return (
        isinstance(data, dict)
        and data.get("harnessCode") == STORE_REQUIRES_NEWER_READER_CODE
    )


def resume_failure_message(error: BaseException, fallback_prefix: str) -> str:
    """Return the transcript message for a failed resume.

    ``fallback_prefix`` labels an ordinary failure (for example
    ``"Failed to resume session"``). The upgrade case ignores it and returns a
    self-contained instruction instead of an internal store-format error.
    """
    if is_store_requires_newer_reader(error):
        return _UPGRADE_MESSAGE
    return f"{fallback_prefix}: {error}"
