from __future__ import annotations

import os

# Settled-frame signal; mirrors IDLE_MARKER in cli-rust and e2e/app_server/config.py.
MARKER = "\x1b]5379;vibe-idle\x07"

# Bracket a batched step: one marker per step; mirrors cli-rust and e2e/pty/capture.py.
HOLD_KEY = "f23"
RELEASE_KEY = "f24"


def replaying() -> bool:
    """True while the client-e2e harness drives this process."""
    return os.environ.get("VIBE_REPLAY_FIXTURE") is not None


# Machine-independent bottom-bar values, so goldens stay byte-identical across OSes.
_FOOTER_CWD = "/test/workdir"
_FOOTER_PID_LABEL = "[PID 0]"


def footer_cwd(cwd: str) -> str:
    """Pin the bottom-bar cwd under replay; the real value otherwise."""
    return _FOOTER_CWD if replaying() else cwd


def footer_pid_label(label: str) -> str:
    """Pin the bottom-bar PID label under replay; the real value otherwise."""
    return _FOOTER_PID_LABEL if replaying() else label


def settle_busy() -> bool:
    """True when the harness snapshots mid-turn, so busy frames must settle too."""
    return os.environ.get("VIBE_REPLAY_SETTLE_BUSY") == "1"
