from __future__ import annotations

import os


def process_name() -> str:
    # Shown in Activity Monitor / ps / top so Vibe can be spotted and killed.
    # Concurrent instances are differentiated by the process manager's own PID
    # column, so the name itself stays clean.
    return "Vibe CLI"


def process_id_label() -> str:
    # Compact PID tag for the TUI bottom bar, shown right after the cwd path.
    return f"[PID {os.getpid()}]"
