"""`--setup` browser sign-in: the copy key reveals the sign-in URL."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline

client_args = ("--setup",)
request_methods = {"setup/status"}

_SIGN_IN_URL = "https://console.mistral.ai/codestral/cli/authenticate?process_id=replay"

# Reaching the sign-in screen opens the browser; `c` then copies the same URL.
expected_actions = {"rust": [Action("open_url", _SIGN_IN_URL)]}
clipboard_clients = {"rust"}
expected_clipboard = _SIGN_IN_URL
timeline: Timeline = ["\r", "\r", "\r", "\r", "c"]
