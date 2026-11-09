"""Pasted or dropped shell-escaped path lists become one placeholder or mention per path."""

from __future__ import annotations

from pathlib import Path

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

# Fixed shallow `/tmp` paths keep the rendered mentions checkout-independent.
_DIR = Path("/tmp/vibe_e2e_fixtures/path_list")
_DIR.mkdir(parents=True, exist_ok=True)
# Only the non-image path needs to exist: the existence probe checks it.
(_DIR / "notes.md").write_text("notes\n", encoding="utf-8")

timeline: Timeline = [
    paste(f"{_DIR}/shot\\ \\(1\\).png {_DIR}/b.png"),
    paste(f"{_DIR}/notes.md\n{_DIR}/b.png\n"),
]
