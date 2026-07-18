from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
import tempfile

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.watchdog.models import RunPhase


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    process_id: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    incident_id: str | None = None
    epoch: int = Field(ge=0)
    phase: RunPhase
    sequence: int = Field(ge=0)
    monotonic_sample: float = Field(ge=0)
    wall_time: str

    @classmethod
    def sample(
        cls,
        *,
        run_id: str,
        session_id: str,
        epoch: int,
        phase: RunPhase,
        sequence: int,
        monotonic_sample: float,
        incident_id: str | None = None,
    ) -> Heartbeat:
        return cls(
            process_id=os.getpid(),
            run_id=run_id,
            session_id=session_id,
            incident_id=incident_id,
            epoch=epoch,
            phase=phase,
            sequence=sequence,
            monotonic_sample=monotonic_sample,
            wall_time=datetime.now(UTC).isoformat(),
        )


class HeartbeatWriter:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def write(self, heartbeat: Heartbeat) -> None:
        await asyncio.to_thread(self._write_sync, heartbeat)

    def _write_sync(self, heartbeat: Heartbeat) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=".heartbeat.",
                suffix=".tmp",
                delete=False,
            ) as temp:
                temp_path = Path(temp.name)
                temp.write(heartbeat.model_dump_json())
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, self._path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
