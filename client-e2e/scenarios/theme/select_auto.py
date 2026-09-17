from __future__ import annotations

from e2e.app_server.scenario import Timeline

env = {"VIBE_THEME": "auto"}
handshake = {
    "config/read": {"config": {"theme": "auto"}},
    "runtime/read": {"runtime": {"config": {"theme": "auto"}}},
    "config/write": {"runtime": {"config": {"theme": "auto"}}},
}
terminal_responses = {"\x1b]11;?\x07": "\x1b]11;rgb:ffff/ffff/ffff\x07"}
timeline: Timeline = ["/theme", "\r", "\r", "/theme", "\r"]
