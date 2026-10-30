"""Session config builds, reused while what they read is unchanged.

A build reads the user and project config files, the trust store, the
environment, the org config the process holds, and the cached experiment
variants and model catalog. Every session read builds one, and Desktop reads
once per sidebar query, so without reuse a single refresh repeats the same
build several times.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import time

from vibe.app_server._org_config_service import OrgConfigKey, OrgConfigService
from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import AdminConfigApplyResult
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.project import project_config_candidates
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.paths import EXPERIMENT_EVAL_CACHE_FILE, MODEL_CATALOG_CACHE_FILE
from vibe.core.telemetry.send import mistral_credential_cache_key

# A backstop for what the inputs cannot see change: the cached variants and
# catalog expire after days, which no file signature shows.
_MAX_AGE_S = 5 * 60
_MAX_ENTRIES = 32


@dataclass(frozen=True, slots=True)
class SessionConfigKey:
    cwd: Path
    overrides: str
    org_config: bool

    @classmethod
    def of(
        cls, cwd: Path, overrides: dict[str, object], *, org_config: bool
    ) -> SessionConfigKey:
        return cls(cwd, json.dumps(overrides, sort_keys=True), org_config)


@dataclass(frozen=True, slots=True)
class BuiltSessionConfig:
    config_orchestrator: ConfigOrchestrator[VibeConfigSchema]
    # The config before the org config applied: the org config can change the
    # provider and endpoint its own key is made of.
    pre_org_config: VibeConfigSchema
    org_config_key: OrgConfigKey | None
    org_config_tried: str | None
    org_config_failure: AdminConfigApplyResult | None


@dataclass(frozen=True, slots=True)
class _Entry:
    inputs: Hashable
    credential: str | None
    built: BuiltSessionConfig
    built_at: float


@dataclass(frozen=True, slots=True)
class _Flight:
    inputs: Hashable
    task: asyncio.Task[_Entry]


class SessionConfigCache:
    """Hands out copies, never the build it keeps: a session writes its
    orchestrator, and a read restoring pins writes the read's.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: OrderedDict[SessionConfigKey, _Entry] = OrderedDict()
        # Desktop opens with a burst of reads for the same folder, all arriving
        # before the first build is done; they share it.
        self._flights: dict[SessionConfigKey, _Flight] = {}

    async def get(
        self,
        key: SessionConfigKey,
        harness_files: HarnessFilesManager,
        org_config: OrgConfigService | None,
        build: Callable[[], Awaitable[BuiltSessionConfig]],
    ) -> BuiltSessionConfig:
        # Taken before the build, so a change landing during it rebuilds next
        # time rather than being taken as built.
        inputs = session_config_inputs(harness_files)
        now = self._clock()
        entry = self._entries.get(key)
        if entry is None or not await self._is_current(entry, inputs, now, org_config):
            entry = await self._join_or_build(key, inputs, now, build)
        if key in self._entries:
            self._entries.move_to_end(key)
        return replace(
            entry.built, config_orchestrator=entry.built.config_orchestrator.copy()
        )

    async def _join_or_build(
        self,
        key: SessionConfigKey,
        inputs: Hashable,
        now: float,
        build: Callable[[], Awaitable[BuiltSessionConfig]],
    ) -> _Entry:
        flight = self._flights.get(key)
        # A build that started before an input changed is not this one's.
        if flight is None or flight.inputs != inputs:
            task = asyncio.create_task(self._build(key, inputs, now, build))
            # Retrieved here too, for when every caller stopped waiting.
            task.add_done_callback(lambda done: done.cancelled() or done.exception())
            flight = _Flight(inputs, task)
            self._flights[key] = flight
        # Shielded: a caller that stops waiting does not cancel the others'.
        return await asyncio.shield(flight.task)

    async def _build(
        self,
        key: SessionConfigKey,
        inputs: Hashable,
        now: float,
        build: Callable[[], Awaitable[BuiltSessionConfig]],
    ) -> _Entry:
        this = asyncio.current_task()
        try:
            built = await build()
            entry = _Entry(
                inputs=inputs, credential=_credential(built), built=built, built_at=now
            )
            # A newer build for changed inputs supersedes this one.
            if (flight := self._flights.get(key)) is not None and flight.task is this:
                self._entries[key] = entry
                self._entries.move_to_end(key)
                while len(self._entries) > _MAX_ENTRIES:
                    self._entries.popitem(last=False)
            return entry
        finally:
            if (flight := self._flights.get(key)) is not None and flight.task is this:
                del self._flights[key]

    async def _is_current(
        self,
        entry: _Entry,
        inputs: Hashable,
        now: float,
        org_config: OrgConfigService | None,
    ) -> bool:
        if (
            entry.inputs != inputs
            or now - entry.built_at >= _MAX_AGE_S
            # The variants and catalog are keyed by the Mistral credential,
            # which can come from the keyring or the running session's
            # credentials as well as the environment.
            or _credential(entry.built) != entry.credential
        ):
            return False
        built = entry.built
        # The org config key is made of the same credential, before the org
        # config applied, and of the endpoint.
        key = OrgConfigService.key_for_config(built.pre_org_config)
        if key != built.org_config_key:
            return False
        # The process refreshes the org config it holds in the background.
        held = await org_config.load(key) if org_config is not None else None
        # ``""``, an org that enforces nothing, is tried as no org config.
        return (held or None) == built.org_config_tried


def session_config_inputs(harness_files: HarnessFilesManager) -> Hashable:
    """Everything ``build_default_orchestrator`` and ``apply_cached_layers``
    read, cheaply, except the org config, which the process holds: file
    signatures, the project config's trust, and the environment. Keep it in
    step with them.
    """
    files = [EXPERIMENT_EVAL_CACHE_FILE.path, MODEL_CATALOG_CACHE_FILE.path]
    if "user" in harness_files.sources and harness_files.user_config is None:
        files.append(harness_files.user_config_file)
    project_trust: bool | None = None
    if harness_files.project_source_enabled:
        candidates = project_config_candidates(harness_files.cwd or Path.cwd())
        files.extend(candidates)
        project_file = next((path for path in candidates if path.is_file()), None)
        if project_file is not None:
            project_trust = harness_files.trust_store.is_trusted(project_file.parent)
    return (
        tuple((str(path), _signature(path)) for path in files),
        project_trust,
        frozenset(os.environ.items()),
    )


def _signature(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_mtime_ns, stat.st_size)


def _credential(built: BuiltSessionConfig) -> str | None:
    return mistral_credential_cache_key(built.config_orchestrator.config)
