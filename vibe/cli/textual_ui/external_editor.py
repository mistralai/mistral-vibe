from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import tempfile


class ExternalEditor:
    """Handles opening an external editor to edit prompt content."""

    @staticmethod
    def get_editor() -> str:
        return os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"

    def edit(self, initial_content: str = "") -> str | None:
        editor = self.get_editor()
        fd, filepath = tempfile.mkstemp(suffix=".md", prefix="vibe_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(initial_content)

            parts = shlex.split(editor)
            subprocess.run([*parts, filepath], check=True)

            content = Path(filepath).read_text().rstrip()
            return content if content != initial_content else None
        except (OSError, subprocess.CalledProcessError):
            return
        finally:
            Path(filepath).unlink(missing_ok=True)
