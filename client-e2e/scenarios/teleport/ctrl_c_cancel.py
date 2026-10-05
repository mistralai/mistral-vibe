"""Ctrl+C while teleporting cancels the operation, as the loading hint says."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.teleport import METHODS, handshake as teleport_handshake

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
handshake = teleport_handshake()
request_methods = METHODS
timeline: Timeline = ["/teleport\r", "\x03"]

screen_contains = {"rust": ("Teleport cancelled",)}
screen_excludes = {"rust": ("again to quit",)}
