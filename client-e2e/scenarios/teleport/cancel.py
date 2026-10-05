"""Escape while teleporting cancels the operation and settles its row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import METHODS, handshake as teleport_handshake

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
handshake = teleport_handshake()
request_methods = METHODS
timeline: Timeline = ["/teleport\r", "\x1b"]
