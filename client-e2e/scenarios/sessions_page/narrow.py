"""Narrower terminals drop the folders column, then the preview; the hints wrap, never cut."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize, take_snapshot

timeline: Timeline = [
    "/sessions",
    "\r",
    resize(30, 100),
    take_snapshot("no_folders"),
    resize(30, 70),
    take_snapshot("list_only"),
]
