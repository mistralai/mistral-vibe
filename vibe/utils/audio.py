from __future__ import annotations

from enum import StrEnum, auto

_VOICE_EXTRA_HINT = 'install the voice extra: uv tool install "mistral-vibe[voice]"'


class RecordingMode(StrEnum):
    BUFFER = auto()
    STREAM = auto()


def miniaudio_unavailable_message(base: str, import_error: BaseException | None) -> str:
    """`base` with the reason miniaudio failed to import, and how to install it when missing.

    The install hint is only given when the package is absent, which happens on
    platforms where it is not a default dependency; a driver that fails to load
    is not fixed by reinstalling.
    """
    if import_error is None:
        return base
    message = f"{base}: {import_error}"
    if isinstance(import_error, ModuleNotFoundError):
        message = f"{message} ({_VOICE_EXTRA_HINT})"
    return message
