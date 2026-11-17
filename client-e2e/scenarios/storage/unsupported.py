"""An older app server without `storage/usage/read` makes `/storage` say it needs a newer one."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import FLAG_ON

handshake = FLAG_ON
request_methods = {"storage/usage/read"}

timeline: Timeline = ["/storage", "\r", take_snapshot()]

screen_contains = ("Storage usage needs a newer Vibe app server.",)
screen_excludes = ("Vibe storage",)
