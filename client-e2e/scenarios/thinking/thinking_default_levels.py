from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# An older app-server sends no thinkingLevels (null here; the client reads an
# absent and a null field identically), so the picker must fall back to the
# canonical five instead of rendering nothing.
handshake = {
    "runtime/read": {"runtime": {"config": {"activeModel": {"thinkingLevels": None}}}}
}

timeline: Timeline = ["/thinking", take_snapshot(), "\r", take_snapshot()]
