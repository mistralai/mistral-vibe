from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from vibe.core.paths._vibe_home import VIBE_HOME

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class WatchdogPaths:
    run_dir: Path

    @classmethod
    def for_run(cls, run_id: str, *, root: Path | None = None) -> WatchdogPaths:
        if not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("run_id must be safe for use as a directory name")
        base = root or VIBE_HOME.path / "watchcat" / "runs"
        return cls(run_dir=base / run_id)

    @property
    def state(self) -> Path:
        return self.run_dir / "state.json"

    @property
    def events(self) -> Path:
        return self.run_dir / "events.jsonl"

    @property
    def decisions(self) -> Path:
        return self.run_dir / "decisions.jsonl"

    @property
    def summary(self) -> Path:
        return self.run_dir / "summary.json"

    @property
    def verification_log(self) -> Path:
        return self.run_dir / "verification.log"

    @property
    def snapshots(self) -> Path:
        return self.run_dir / "snapshots"
