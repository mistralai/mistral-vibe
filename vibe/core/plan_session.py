from __future__ import annotations

from pathlib import Path
import time

from vibe.core.paths import PLANS_DIR
from vibe.core.utils.io import read_safe
from vibe.core.utils.slug import create_slug


class PlanSession:
    def __init__(self) -> None:
        self._plan_file_path: Path | None = None
        self._snapshot: str | None = None

    @property
    def plan_file_path(self) -> Path:
        if self._plan_file_path is None:
            slug = create_slug()
            timestamp = int(time.time())
            self._plan_file_path = PLANS_DIR.path / f"{timestamp}-{slug}.md"
        return self._plan_file_path

    @property
    def plan_file_path_str(self) -> str:
        return str(self.plan_file_path)

    def read(self) -> str | None:
        if self._plan_file_path is None:
            return None

        if not self._plan_file_path.exists():
            return None

        return read_safe(self._plan_file_path).text

    def snapshot_content_hash(self) -> None:
        content = self.read()
        self._snapshot = content

    def has_content_changed(self) -> bool:
        return self.read() != self._snapshot
