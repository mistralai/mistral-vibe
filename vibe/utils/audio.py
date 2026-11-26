from __future__ import annotations

from enum import StrEnum, auto


class RecordingMode(StrEnum):
    BUFFER = auto()
    STREAM = auto()


def miniaudio_unavailable_message(base: str, import_error: BaseException | None) -> str:
    """`base` with the reason miniaudio failed to import, when there is one.

    miniaudio is a default dependency, so an import failure is a driver or
    broken-install problem, not something an install hint would fix.
    """
    if import_error is None:
        return base
    return f"{base}: {import_error}"
