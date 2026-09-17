from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/rename Renamed session\r"]

handshake = {"session/rename": {"title": "Renamed session", "updatedAt": None}}
