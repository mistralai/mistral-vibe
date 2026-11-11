"""Tables wider than the viewport pick column widths that render the fewest rows."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, resize, take_snapshot

_PROMPT = "list the changed files and their owners"
_ANSWER = "\n".join([
    "| File | CODEOWNERS rule | Owners | Change |",
    "| --- | --- | --- | --- |",
    (
        "| `agents/harness/harness/plugins/schemas/mistral-vibe/1.0.0/hooks.schema.json` "
        "| `/agents/harness/harness/plugins/` | engineering-le-chat, eng-vibe-code-cli "
        "| +8/-2: adds pre_agent and notification to the hooks.toml schema |"
    ),
    (
        "| `agents/harness/harness/runtimes/typescript/src/plugins/resolver.ts` "
        "| `/agents/harness/harness/runtimes/typescript/src/plugins/` | engineering-le-chat only "
        "| +40/-6: the TypeScript runtime accepts both new hook types (the plugin loads, "
        "without these hooks) and strict is rejected on notification |"
    ),
    (
        "| `agents/harness/harness/runtimes/typescript/src/plugins/plugins.test.ts` "
        "| same rule | engineering-le-chat only | +54: tests for this behavior |"
    ),
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    take_snapshot(),
    resize(40, 80),
    take_snapshot(),
]
