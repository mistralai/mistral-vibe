from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Action, Timeline

_PROMPT = "give me an editor link to the README"
_URL = "vscode://file/tmp/README.md:1"
_ANSWER = f"Open [README]({_URL}) in your editor."

expected_actions = {
    "rust": [Action("open_url", _URL)],
    "python": [Action("open_url", _URL)],
}

_ROW, _COL = 32, 8
_CLICK = f"\x1b[<0;{_COL};{_ROW}M\x1b[<0;{_COL};{_ROW}m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _CLICK,
]
