from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "show me a table"
_ANSWER = "\n".join([
    "Here is a comparison:",
    "",
    "| Feature | Python | Rust |",
    "| --- | --- | --- |",
    "| Tables | yes | no |",
    "| Lists | yes | yes |",
    "",
    "And a wide one:",
    "",
    "| Feature | Description | Status |",
    "| --- | --- | --- |",
    "| Tables | This is a very long description cell that is intentionally verbose and repetitive to exceed the available column width and force the text to wrap across multiple physical lines when rendered in the terminal | done |",
    "| Lists | This is another deliberately lengthy cell with lots of text that is designed to be long enough to also wrap across multiple lines when the column is shrunk to fit the terminal width | pending |",
    "",
    "That is the difference.",
])

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
]
