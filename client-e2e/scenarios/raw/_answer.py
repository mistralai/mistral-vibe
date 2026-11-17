"""A Markdown agent answer shared by the `/raw` scenarios."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

PROMPT = "explain the setup"
LONG = (
    "Run the installer with the default options, then restart the terminal so the new "
    "PATH entry is picked up by every shell you open afterwards."
)
ANSWER = f"""## Setup

{LONG}

- **Install** the `vibe` package
- Run `vibe --setup`

```bash
uv tool install mistral-vibe
```

| Flag | Effect |
|---|---|
| `-p` | Run headless |"""

ANSWERED: Timeline = [
    f"{PROMPT}\r",
    turn_started(),
    user_msg(PROMPT),
    assistant_msg(ANSWER),
    turn_completed(),
]
