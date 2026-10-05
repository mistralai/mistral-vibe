"""A `/` mention mid-prompt opens the skill popup at the caret and Tab accepts it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SKILLS = [
    {
        "name": name,
        "description": description,
        "prompt": "",
        "userInvocable": True,
        "source": "local",
    }
    for name, description in (
        ("code-review", "Review the current change."),
        ("lint", "Run the linters."),
    )
]

handshake = {"runtime/read": {"runtime": {"skills": _SKILLS}}}

capture_startup = False
capture_steps = {1, 2, 3, 4}
settle_per_key = True
expected_clipboard = "run /lint then"

timeline: Timeline = ["run ", "/", "li", "\t", "then\x1b[18~\x19"]
