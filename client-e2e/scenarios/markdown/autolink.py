from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

_PROMPT = "where do I reach you"
# Every case markdown-it's linkify pass decides on: bare email, bare URL, `www.`,
# a parenthesised URL, a bare domain, a non-TLD filename, and inline code.
_ANSWER = (
    "Mail ada@example.com now.\n\n"
    "Docs at https://docs.mistral.ai/guides/ and www.mistral.ai/pricing.\n\n"
    "Trailing (https://example.org/x), then bare example.com and file config.toml.\n\n"
    "Code `ops@example.com` stays plain, and [a link](https://example.net) too."
)
# Every link the terminal can open itself; mailto links stay plain like their clicks.
screen_hyperlinks = (
    ("https://docs.mistral.ai/guides/", "https://docs.mistral.ai/guides/"),
    ("www.mistral.ai/pricing", "http://www.mistral.ai/pricing"),
    ("https://example.org/x", "https://example.org/x"),
    ("example.com", "http://example.com"),
    ("a link", "https://example.net"),
)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    take_snapshot(),
]
