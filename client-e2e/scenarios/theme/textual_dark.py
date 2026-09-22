from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"VIBE_THEME": "textual-dark"}
handshake = {
    "config/read": {"config": {"theme": "textual-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "textual-dark"}}},
}
timeline: Timeline = ["x"]
