from __future__ import annotations

import json
import os
from pathlib import Path

_ACTION_LOG_ENV = "VIBE_E2E_ACTION_LOG"


def record_open_url(url: str) -> bool:
    action_log = os.environ.get(_ACTION_LOG_ENV)
    if action_log is None:
        return False
    try:
        with Path(action_log).open("a", encoding="utf-8") as file:
            json.dump({"kind": "open_url", "url": url}, file, separators=(",", ":"))
            file.write("\n")
    except OSError:
        pass
    return True
