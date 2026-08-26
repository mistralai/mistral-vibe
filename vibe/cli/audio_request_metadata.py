from __future__ import annotations

from collections.abc import Callable

from vibe import __version__
from vibe.utils.platform import get_platform_id, get_platform_version

type RequestMetadataGetter = Callable[[], dict[str, str]]


def build_audio_request_metadata(
    *, session_id: str, parent_session_id: str | None
) -> dict[str, str]:
    metadata = {
        "os": get_platform_id(),
        "version": __version__,
        "session_id": session_id,
        "call_type": "secondary_call",
        "call_source": "vibe_code",
    }
    if os_version := get_platform_version():
        metadata["os_version"] = os_version
    if parent_session_id is not None:
        metadata["parent_session_id"] = parent_session_id
    return metadata
