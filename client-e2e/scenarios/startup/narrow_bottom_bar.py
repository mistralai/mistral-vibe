"""A narrow terminal ellipsizes the bottom bar cwd and keeps the full token counter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

handshake = {"runtime/read": {"runtime": {"stats": {"contextTokens": 23_000}}}}

timeline: Timeline = [resize(20, 36)]

screen_contains = {"rust": ("/test/… [PID 0] 23k/600k tokens (4%)",)}
