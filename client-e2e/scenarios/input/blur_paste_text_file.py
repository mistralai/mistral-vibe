"""A text file dropped while the terminal is unfocused becomes an `@` mention."""

from __future__ import annotations

from pathlib import Path

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

# A fixed shallow `/tmp` path keeps the rendered mention checkout-independent.
_DIR = Path("/tmp/vibe_e2e_fixtures/blur_paste")
_DIR.mkdir(parents=True, exist_ok=True)
(_DIR / "notes.md").write_text("notes\n", encoding="utf-8")

capture_startup = False
screen_contains = (f"@{_DIR}/notes.md",)

timeline: Timeline = ["\x1b[O" + paste(f"{_DIR}/notes.md") + "\x1b[I"]
