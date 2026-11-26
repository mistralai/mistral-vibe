from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_titles = ("Vibe", "Renamed session")

timeline: Timeline = ["/rename Renamed session\r"]

handshake = {"session/rename": {"title": "Renamed session", "updatedAt": None}}
