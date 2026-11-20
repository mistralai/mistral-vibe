"""A `--resume <id>` rejected for a store written by a newer Vibe says how to upgrade, keeping the fresh session."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"
_HARNESS_MESSAGE = (
    "This session was written by a newer store format (1.9) "
    "and needs a newer reader to open."
)

client_args = ("--resume", _SAVED)
handshake = {
    "session/resume": {
        "error": {
            "code": "internal_error",
            "message": _HARNESS_MESSAGE,
            "data": {
                "harnessCode": "store_requires_newer_reader",
                "details": {"session_id": _SAVED, "store_format_minor": 9},
            },
        }
    }
}
request_methods = {"session/resume"}
timeline: Timeline = []

screen_contains = (
    "This session was created by a newer version of Vibe.",
    "--check-upgrade",
)
screen_excludes = (
    _HARNESS_MESSAGE,
    "Failed to resume session",
    "Resuming session",
    "Saved session restored.",
)
