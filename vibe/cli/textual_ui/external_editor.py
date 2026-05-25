from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import tempfile

from vibe.core.utils.io import read_safe


class ExternalEditor:
    """Handles opening an external editor to edit prompt content."""

    @staticmethod
    def get_editor() -> str:
        return os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"

    @classmethod
    def edit_file(cls, file_path: Path, *, check: bool = False) -> None:
        editor = cls.get_editor()
        parts = shlex.split(editor)
        subprocess.run([*parts, str(file_path)], check=check)

    def edit(self, initial_content: str = "") -> str | None:
        fd, filepath = tempfile.mkstemp(suffix=".md", prefix="vibe_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(initial_content)

            self.edit_file(Path(filepath), check=True)

            content = read_safe(Path(filepath)).text.rstrip()
            return content if content != initial_content else None
        except (OSError, subprocess.CalledProcessError):
            return
        finally:
            Path(filepath).unlink(missing_ok=True)
