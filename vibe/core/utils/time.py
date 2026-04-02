from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
