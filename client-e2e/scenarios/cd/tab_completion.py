"""Typing `/cd ` lists the session folder's subfolders; hidden ones stay out."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.cd._dirs import CLIENT_ARGS, REQUEST_METHODS, handshake

handshake = handshake()
client_args = CLIENT_ARGS
request_methods = REQUEST_METHODS
screen_contains = ("alpha/", "beta/")
screen_excludes = (".hidden/", "notes.md")

timeline: Timeline = ["/cd ", take_snapshot()]
