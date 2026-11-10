"""A bracketed multi-line paste stays in the composer as one draft."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = ["before ", paste("one\ntwo\nthree"), " after", take_snapshot()]
