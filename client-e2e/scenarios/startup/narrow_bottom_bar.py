"""A narrow terminal turns the context gauge into a one-cell level and keeps the cwd whole."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize

handshake = {"runtime/read": {"runtime": {"stats": {"contextTokens": 23_000}}}}

timeline: Timeline = [resize(20, 28)]

screen_contains = ("/test/workdir", "ctx ▁")
screen_excludes = ("23k/600k",)
