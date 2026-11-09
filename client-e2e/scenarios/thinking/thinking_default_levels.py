from __future__ import annotations

from e2e.app_server.scenario import Timeline

# An older app-server sends no thinkingLevels (null here; the client reads an
# absent and a null field identically), so the picker must fall back to the
# canonical five instead of rendering nothing.
handshake = {
    "runtime/read": {"runtime": {"config": {"activeModel": {"thinkingLevels": None}}}}
}

timeline: Timeline = ["/thinking", "\r"]
