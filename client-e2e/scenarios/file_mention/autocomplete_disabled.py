"""A disabled file autocomplete never opens the @ file popup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"runtime/read": {"runtime": {"config": {"enableFileAutocomplete": False}}}}

# The committed at_popup directory keeps the would-be popup rows stable; with
# the flag off none of its files may ever reach the screen.
timeline: Timeline = ["@client-e2e/fixtures/at_popup/"]

screen_contains = ("at_popup",)
screen_excludes = ("alpha.md", "beta.md", "gamma.md")
