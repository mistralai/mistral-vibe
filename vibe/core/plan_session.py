from __future__ import annotations

from pathlib import Path
import time

from vibe.core.paths import PLANS_DIR
from vibe.core.utils.slug import create_slug


class PlanSession:
    def __init__(self) -> None:
        self._plan_file_path: Path | None = None

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
