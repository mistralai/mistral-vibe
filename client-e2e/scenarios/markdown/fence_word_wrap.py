"""Fence lines wrap at word boundaries and stand one row apart from neighbouring blocks on ANSI."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_THEME": "ansi-dark"}
handshake = {
    "config/read": {"config": {"theme": "ansi-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "ansi-dark"}}},
}

_PROMPT = "how does the skill handle CI failures?"
_ANSWER = "\n".join([
    "The skill now asks the agent to fix CI failures caused by the PR itself:",
    "",
    "```",
    "For each failed check, read its logs (`gh run view <run-id> --log-failed`, or the "
    "Buildkite tools) and find the cause. If the PR caused it, fix it in the working "
    "tree; it ships with the step 7 commit. If not (flaky, infra, or also failing on "
    "`main`), do not fix it; tell the user in the final report what failed and why.",
    "    indented continuation keeps its leading spaces",
    "```",
    "",
    "- **Failure caused by the PR**: the agent fixes it in the same commit.",
    "- **Other cause**: it leaves it alone and explains it in the final report.",
    "```",
    " " * 120 + "return some_function_name(first_argument, second_argument)",
    "```",
    "```",
    "back-to-back fence, last block of the message",
    "```",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
