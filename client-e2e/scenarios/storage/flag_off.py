"""With the `vibe_storage_usage` flag off, `/storage` is not listed in completion or help and is sent as a plain prompt, like any unknown command."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import usage

# A server with storage usage, but the flag off (the fixture's config leaves it out).
handshake = {"storage/usage/read": usage()}
# No storage request is sent; the prompt goes to the agent as `session/turn/enqueue`.
request_methods = {
    "storage/usage/read",
    "storage/usage/refresh",
    "workspace/prompt/prepare",
}

ESCAPE = "\x1b[27u"

# The completion popup for `/sto`, then `/help`, then `/storage` itself.
timeline: Timeline = [
    "/sto",
    take_snapshot(),
    # One Esc closes the popup (`/sto` matches `/trusted-folders`), two clear the input.
    ESCAPE * 3,
    "/help",
    "\r",
    take_snapshot(),
    "/storage",
    "\r",
    take_snapshot(),
]

screen_excludes = ("Vibe storage", "See how much disk Vibe uses")
