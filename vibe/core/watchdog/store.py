from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from typing import cast

from pydantic import JsonValue, ValidationError

from vibe.core.utils.io import read_safe
from vibe.core.watchdog.events import WatchdogEvent
from vibe.core.watchdog.fingerprint import sanitize_artifact
from vibe.core.watchdog.models import RecoveryDecision, RunState
from vibe.core.watchdog.paths import WatchdogPaths
from vibe.core.watchdog.reducer import apply_event


class WatchdogStorageError(Exception):
    pass


class WatchdogSchemaVersionError(WatchdogStorageError):
    pass


class WatchdogStore:
    def __init__(
        self, paths: WatchdogPaths, *, max_record_bytes: int = 256 * 1024
    ) -> None:
        self.paths = paths
        self._max_record_bytes = max_record_bytes
        self._write_lock = asyncio.Lock()

    async def persist(
        self,
        event: WatchdogEvent,
        state: RunState,
        *,
        decision: RecoveryDecision | None = None,
    ) -> None:
        if event.sequence != state.last_applied_sequence:
            raise WatchdogStorageError("event and state sequences do not match")
        if event.run_id != state.run_id or event.session_id != state.session_id:
            raise WatchdogStorageError("event and state identities do not match")
        async with self._write_lock:
            await asyncio.to_thread(self._persist_sync, event, state, decision)

    async def load_state(self) -> RunState | None:
        return await asyncio.to_thread(self._load_state_sync)

    async def load_events(self) -> list[WatchdogEvent]:
        return await asyncio.to_thread(self._load_events_sync)

    async def replay(self) -> RunState | None:
        events = await self.load_events()
        if not events:
            return None
        state = RunState.new(run_id=events[0].run_id, session_id=events[0].session_id)
        for event in events:
            state = apply_event(state, event)
        return state

    async def snapshot(self, state: RunState) -> Path:
        async with self._write_lock:
            return await asyncio.to_thread(self._snapshot_sync, state)

    def _persist_sync(
        self, event: WatchdogEvent, state: RunState, decision: RecoveryDecision | None
    ) -> None:
        self.paths.run_dir.mkdir(parents=True, exist_ok=True)
        self._append_record(self.paths.events, event.model_dump(mode="json"))
        if decision is not None:
            incident_id = state.incident.incident_id if state.incident else None
            self._append_record(
                self.paths.decisions,
                {
                    "schema_version": state.schema_version,
                    "run_id": state.run_id,
                    "session_id": state.session_id,
                    "sequence": event.sequence,
                    "incident_id": incident_id,
                    "epoch": state.epoch,
                    "decision": decision.model_dump(mode="json"),
                },
            )
        self._write_state_atomic(state)

    def _append_record(self, path: Path, record: dict[str, JsonValue]) -> None:
        sanitized = sanitize_artifact(record)
        encoded = (
            json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode()
        if len(encoded) > self._max_record_bytes:
            raise WatchdogStorageError(
                f"Watchdog audit record exceeds {self._max_record_bytes} bytes"
            )
        try:
            with path.open("ab") as audit:
                audit.write(encoded)
                audit.flush()
                os.fsync(audit.fileno())
        except OSError as error:
            raise WatchdogStorageError(f"failed to append {path.name}") from error

    def _write_state_atomic(self, state: RunState) -> None:
        record = cast(
            dict[str, JsonValue], sanitize_artifact(state.model_dump(mode="json"))
        )
        self._write_json_atomic(self.paths.state, record)

    def _snapshot_sync(self, state: RunState) -> Path:
        self.paths.snapshots.mkdir(parents=True, exist_ok=True)
        path = self.paths.snapshots / f"state-{state.last_applied_sequence:06d}.json"
        record = cast(
            dict[str, JsonValue], sanitize_artifact(state.model_dump(mode="json"))
        )
        self._write_json_atomic(path, record)
        return path

    @staticmethod
    def _write_json_atomic(path: Path, record: dict[str, JsonValue]) -> None:
        payload = json.dumps(
            record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".state.",
                suffix=".tmp",
                delete=False,
            ) as temp:
                temp_path = Path(temp.name)
                temp.write(payload)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, path)
        except OSError as error:
            raise WatchdogStorageError("failed to atomically persist state") from error
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _load_state_sync(self) -> RunState | None:
        if not self.paths.state.exists():
            return None
        try:
            raw = json.loads(read_safe(self.paths.state, raise_on_error=True).text)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise WatchdogStorageError("failed to read Watchdog state") from error
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise WatchdogSchemaVersionError("unsupported Watchdog state schema")
        try:
            return RunState.model_validate(raw)
        except ValidationError as error:
            raise WatchdogStorageError("invalid Watchdog state") from error

    def _load_events_sync(self) -> list[WatchdogEvent]:
        if not self.paths.events.exists():
            return []
        events: list[WatchdogEvent] = []
        try:
            text = read_safe(self.paths.events, raise_on_error=True).text
            for line in text.splitlines():
                if line:
                    events.append(WatchdogEvent.model_validate_json(line))
        except (OSError, UnicodeError, ValidationError) as error:
            raise WatchdogStorageError("failed to read Watchdog events") from error
        return events
