"""`--setup` API key screen: the links are clickable."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline

client_args = ("--setup",)
request_methods = {"setup/status"}

# SGR coordinates are 1-based. The panel content starts at column 25 and the
# provider link is the first row under the subtitle: row 18 (the validation
# row under the card is always reserved, so the panel sits one row higher).
_CLICK_PROVIDER_LINK = "\x1b[<0;30;18M\x1b[<0;30;18m"

expected_actions = {
    "rust": [Action("open_url", "https://chat.mistral.ai/code/extensions?focus=key")]
}
timeline: Timeline = ["\r", "\r", "\x1b[B\r", _CLICK_PROVIDER_LINK]
