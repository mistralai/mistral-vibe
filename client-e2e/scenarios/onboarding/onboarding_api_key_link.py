"""`--setup` API key screen: the links are clickable."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline, take_snapshot

client_args = ("--setup",)
request_methods = {"setup/status"}

# SGR coordinates are 1-based. The panel content starts at column 25 and the
# provider link is the first row under the subtitle: row 18 (the validation
# row under the card is always reserved, so the panel sits one row higher).
_CLICK_PROVIDER_LINK = "\x1b[<0;30;18M\x1b[<0;30;18m"

# The terminal can open each link itself; the panel clips the second label, not its target.
screen_hyperlinks = (
    (
        "https://chat.mistral.ai/code/extensions?focus=key",
        "https://chat.mistral.ai/code/extensions?focus=key",
    ),
    (
        "https://github.com/mistralai/mistral-vibe?tab=readme-ov-file#configu",
        "https://github.com/mistralai/mistral-vibe?tab=readme-ov-file#configuration",
    ),
)
expected_actions = [
    Action("open_url", "https://chat.mistral.ai/code/extensions?focus=key")
]
timeline: Timeline = [
    "\r",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "\x1b[B\r",
    take_snapshot(),
    _CLICK_PROVIDER_LINK,
    take_snapshot(),
]
