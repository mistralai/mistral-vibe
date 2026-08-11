from __future__ import annotations

from enum import StrEnum, auto

from vibe.utils.platform import get_platform_id


class RecordingMode(StrEnum):
    BUFFER = auto()
    STREAM = auto()


def portaudio_install_hint() -> str:
    """Platform-specific hint to install the PortAudio system library, or empty."""
    match get_platform_id():
        case "darwin":
            return " Install it with `brew install portaudio`, then restart vibe."
        case "linux":
            return " Install it with your package manager (e.g. `apt install libportaudio2`), then restart vibe."
        case _:
            return ""
