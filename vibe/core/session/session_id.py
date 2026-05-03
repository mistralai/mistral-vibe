"""Session ID generation with a stable suffix preserved across compact/fork/rewind."""

from __future__ import annotations

import secrets


def generate_session_id(*, suffix: str | None = None) -> str:
    """Generate a UUID-shaped session ID with an optional stable suffix.

    The last segment (12 hex chars after the final hyphen) is either
    the provided *suffix* or freshly random.  The first four segments
    (20 hex chars) are always random.

    Format: ``xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx``
    """
    head = secrets.token_hex(10)  # 10 bytes = 20 hex chars
    tail = suffix or secrets.token_hex(6)  # 6 bytes = 12 hex chars
    return f"{head[:8]}-{head[8:12]}-{head[12:16]}-{head[16:20]}-{tail}"


def extract_suffix(session_id: str) -> str:
    """Extract the stable suffix (last segment after the final hyphen)."""
    return session_id.rsplit("-", 1)[-1]
