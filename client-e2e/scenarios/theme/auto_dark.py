from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"VIBE_THEME": "auto"}
handshake = {
    "config/read": {"config": {"theme": "auto"}},
    "runtime/read": {"runtime": {"config": {"theme": "auto"}}},
}
terminal_responses = {"\x1b]11;?\x07": "\x1b]11;rgb:0000/0000/0000\x1b\\"}
timeline: Timeline = ["x"]
