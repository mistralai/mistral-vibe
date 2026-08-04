from __future__ import annotations

_SHORT_LEN = 8


def shorten_session_id(session_id: str, *, from_end: bool = False) -> str:
    if from_end:
        return session_id[-_SHORT_LEN:]
    return session_id[:_SHORT_LEN]


__all__ = ["shorten_session_id"]
