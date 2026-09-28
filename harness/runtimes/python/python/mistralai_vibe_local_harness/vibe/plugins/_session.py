"""The Host port for session-lifetime plugins, and the two paths that use it.

Create and restore differ in one step: create ingests the installed roots the
provider hands over, restore reads pins it already has. From the checkout
onward they are the same code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
import logging
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from mistralai_vibe_local_harness.protocol import RustPluginContextDefinition
from mistralai_vibe_local_harness.session_protocol import (
    PluginInfo,
    ResolvedPluginDefinition,
)
from mistralai_vibe_local_harness.vibe._errors import HarnessSessionError
from mistralai_vibe_local_harness.vibe._storage import PluginLockEntryV1, PluginLockV1
from mistralai_vibe_local_harness.vibe._subagents import DeclaredAgentTypeProfile
from mistralai_vibe_local_harness.vibe.plugins._store import (
    IgnoredNames,
    PluginBlobUnavailable,
    PluginPackageCorrupt,
    PluginPackageError,
    PluginPackageStore,
    PluginPackageUnavailable,
)

logger = logging.getLogger(__name__)

# Past it the close keeps running unsupervised: cancelling a close is how a
# subprocess gets orphaned.
RELEASE_TIMEOUT_SECONDS = 5.0

type PluginContextDefinition = RustPluginContextDefinition


@dataclass(frozen=True, slots=True)
class PinnedPackage:
    """One installed root, and the names inside it that are not its content."""

    root: Path

    ignored_names: IgnoredNames = frozenset()


@dataclass(frozen=True, slots=True)
class PinnedPlugins:
    """What the Host hands over to be pinned. Produced once, at create."""

    packages: Mapping[str, PinnedPackage]
    """Plugin name to installed root."""


@dataclass(frozen=True, slots=True)
class SessionPluginProjection:
    # One object because the two have to stay consistent: an agent type Core
    # advertises with no profile behind it is a name the model can use and the
    # Runtime cannot execute.
    definitions: tuple[PluginContextDefinition, ...] = ()
    agent_profiles: tuple[DeclaredAgentTypeProfile, ...] = ()


@dataclass(frozen=True, slots=True)
class RestoredPlugins:
    """What the SDK hands to ``bind``. Identical shape on both paths."""

    snapshot: bytes | None
    """The pinned bytes at restore, ``None`` at create. Opaque to the SDK."""

    checkouts: Mapping[str, Path]
    """Plugin name to read-only checkout."""


class SessionPluginProvider(Protocol):
    """Everything plugin-shaped, implemented above the Session Runtime.

    The Session Runtime knows names, paths, and digests. It never learns what a
    plugin is; what ``bind`` returns is already in Runtime vocabulary.
    """

    async def pin(
        self, requested: Sequence[ResolvedPluginDefinition], *, session_id: str
    ) -> PinnedPlugins:
        """Hand over the installed roots satisfying the requested pin.

        Create only. Returning a package the request did not name, or omitting
        one it did, fails session creation.
        """
        ...

    async def bind(
        self, plugins: RestoredPlugins, *, session_id: str
    ) -> tuple[bytes, SessionPluginProjection]:
        """Resolve, materialize, snapshot, and project a set of checkouts.

        Returns the snapshot this bind derived — opaque, the SDK only digests
        it — and the projection. All-or-nothing: a re-bind builds the new set
        beside the live one and swaps, so a raise leaves the previous running.
        """
        ...

    async def info(self, *, session_id: str) -> PluginInfo:
        """Project the bound set for ``plugin/info``. Read-only."""
        ...

    async def release(self, *, session_id: str) -> None:
        """Close MCP clients and drop staged runtime files.

        Idempotent, a no-op for a session that never bound, and must not raise.
        After a failed ``bind`` it releases the half-built set rather than the
        live one, which is why the provider owns that distinction.
        """
        ...


class PluginRestoreDiagnosticCode(StrEnum):
    LOCK_INVALID = "plugin_lock_invalid"
    PACKAGE_UNAVAILABLE = "plugin_package_unavailable"
    PACKAGE_CORRUPT = "plugin_package_corrupt"
    SNAPSHOT_UNAVAILABLE = "plugin_snapshot_unavailable"
    PIN_MISMATCH = "plugin_pin_mismatch"


class PluginRestoreDiagnostic(BaseModel):
    """One plugin's account of why the environment could not be rebuilt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: PluginRestoreDiagnosticCode
    plugin_name: str | None = None
    content_digest: str | None = None
    message: str


class PluginRestoreError(HarnessSessionError):
    """One diagnostic per plugin, because the operator's next action differs.

    Never partial. A session either runs with the plugin environment it
    recorded or it does not run, and the failure never edits the lock.
    """

    code = "plugin_restore_failed"

    def __init__(self, diagnostics: Sequence[PluginRestoreDiagnostic]) -> None:
        self.diagnostics = tuple(
            sorted(diagnostics, key=lambda item: (item.plugin_name or "", item.code))
        )
        summary = "; ".join(
            f"{item.plugin_name or 'session'}: {item.code}" for item in self.diagnostics
        )
        super().__init__(
            type(self).code,
            f"The session's pinned plugin environment could not be rebuilt ({summary})",
            details={
                "diagnostics": [
                    item.model_dump(mode="json") for item in self.diagnostics
                ]
            },
        )


class PluginPinMismatch(PluginRestoreError):
    """Create only: the provider did not hand over what ``AgentConfig`` declared.

    A distinct class so the failure is legible in a log, and the same wire code
    so a caller has one thing to handle.
    """


@dataclass(frozen=True, slots=True)
class SessionPluginBinding:
    """A lock to store and the projection to configure the session from."""

    lock: PluginLockV1
    projection: SessionPluginProjection = SessionPluginProjection()

    @property
    def definitions(self) -> tuple[PluginContextDefinition, ...]:
        return self.projection.definitions

    @property
    def agent_profiles(self) -> tuple[DeclaredAgentTypeProfile, ...]:
        return self.projection.agent_profiles


def empty_plugin_binding() -> SessionPluginBinding:
    """What a session with no configured provider binds. Holds no digest."""
    return SessionPluginBinding(lock=PluginLockV1(plugins=[]))


class SessionPluginBinder:
    """Runs create and restore over one provider and one package store."""

    def __init__(
        self, provider: SessionPluginProvider, store: PluginPackageStore
    ) -> None:
        self._provider = provider
        self._store = store

    async def create(
        self, requested: Sequence[ResolvedPluginDefinition], *, session_id: str
    ) -> SessionPluginBinding:
        """Pin, ingest, verify, check out, bind, and blob the snapshot.

        Also the prepare half of a ``config/write`` re-pin, but not a dry run:
        the last step binds, and ``bind`` swaps the provider's live set. A
        re-pin therefore establishes that the session accepts one before
        calling this, and re-binds the recorded lock if the apply half fails.
        """
        requested = list(requested)
        wanted = {
            definition.name: definition.content_digest for definition in requested
        }
        if len(wanted) != len(requested):
            raise PluginPinMismatch([
                PluginRestoreDiagnostic(
                    code=PluginRestoreDiagnosticCode.PIN_MISMATCH,
                    message="the requested plugin list names one plugin more than once",
                )
            ])

        pinned = await self._provider.pin(requested, session_id=session_id)
        if pinned.packages.keys() != wanted.keys():
            raise PluginPinMismatch([
                PluginRestoreDiagnostic(
                    code=PluginRestoreDiagnosticCode.PIN_MISMATCH,
                    plugin_name=name,
                    content_digest=wanted.get(name),
                    message=(
                        "the provider did not hand over a root for this plugin"
                        if name in wanted
                        else "the provider handed over a plugin the request did not name"
                    ),
                )
                for name in sorted(wanted.keys() ^ pinned.packages.keys())
            ])

        entries: list[PluginLockEntryV1] = []
        mismatched: list[PluginRestoreDiagnostic] = []
        for name, package in sorted(pinned.packages.items()):
            try:
                digest = await asyncio.to_thread(
                    partial(
                        self._store.ingest,
                        Path(package.root),
                        expected=wanted[name],
                        ignored_names=package.ignored_names,
                    )
                )
            except PluginPackageError as exc:
                mismatched.append(
                    PluginRestoreDiagnostic(
                        code=PluginRestoreDiagnosticCode.PIN_MISMATCH,
                        plugin_name=name,
                        content_digest=wanted[name],
                        message=str(exc),
                    )
                )
                continue
            entries.append(PluginLockEntryV1(name=name, content_digest=digest))
        if mismatched:
            raise PluginPinMismatch(mismatched)

        checkouts, diagnostics = await self._checkout_all(entries)
        if diagnostics:
            raise PluginRestoreError(diagnostics)

        derived, projection = await self._bind(
            RestoredPlugins(snapshot=None, checkouts=checkouts), session_id=session_id
        )
        lock = PluginLockV1(
            snapshot_digest=(
                await asyncio.to_thread(self._store.put_blob, derived)
                if entries
                else None
            ),
            plugins=entries,
        )
        return SessionPluginBinding(lock=lock, projection=projection)

    async def restore(
        self, lock: PluginLockV1, *, session_id: str
    ) -> SessionPluginBinding:
        """Rebuild the pinned checkouts and replay the pinned bytes into ``bind``.

        Nothing here re-resolves an installed root, compares against what is
        mounted now, or digests what ``bind`` derives, so a resolver change can
        never fail a restore.
        """
        checkouts, diagnostics = await self._checkout_all(lock.plugins)

        snapshot = b""
        if lock.snapshot_digest is not None:
            try:
                snapshot = await asyncio.to_thread(
                    self._store.read_blob, lock.snapshot_digest
                )
            except PluginBlobUnavailable:
                diagnostics.append(
                    _snapshot_unavailable(lock.snapshot_digest, "no blob")
                )
            except PluginPackageCorrupt as exc:
                diagnostics.append(
                    _snapshot_unavailable(lock.snapshot_digest, str(exc))
                )

        if diagnostics:
            raise PluginRestoreError(diagnostics)

        _, projection = await self._bind(
            RestoredPlugins(snapshot=snapshot, checkouts=checkouts),
            session_id=session_id,
        )
        return SessionPluginBinding(lock=lock, projection=projection)

    async def info(self, *, session_id: str) -> PluginInfo:
        return await self._provider.info(session_id=session_id)

    async def release(self, *, session_id: str) -> None:
        """Close the provider's session state. Never raises, never cancelled."""
        closing = asyncio.ensure_future(self._provider.release(session_id=session_id))
        try:
            await asyncio.wait_for(asyncio.shield(closing), RELEASE_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning(
                "Plugin provider release did not finish in time",
                extra={
                    "session_id": session_id,
                    "timeout_seconds": RELEASE_TIMEOUT_SECONDS,
                },
            )
        except Exception:
            logger.warning(
                "Plugin provider release failed",
                extra={"session_id": session_id},
                exc_info=True,
            )

    async def _bind(
        self, plugins: RestoredPlugins, *, session_id: str
    ) -> tuple[bytes, SessionPluginProjection]:
        try:
            return await self._provider.bind(plugins, session_id=session_id)
        except BaseException:
            # A half-materialized set holds MCP children nobody else will close.
            await self.release(session_id=session_id)
            raise

    async def _checkout_all(
        self, entries: Sequence[PluginLockEntryV1]
    ) -> tuple[dict[str, Path], list[PluginRestoreDiagnostic]]:
        """Rebuild every pinned package. Shared verbatim by both paths."""
        checkouts: dict[str, Path] = {}
        diagnostics: list[PluginRestoreDiagnostic] = []
        for entry in entries:
            try:
                checkouts[entry.name] = await asyncio.to_thread(
                    self._store.checkout, entry.content_digest
                )
            except PluginPackageUnavailable as exc:
                diagnostics.append(
                    PluginRestoreDiagnostic(
                        code=PluginRestoreDiagnosticCode.PACKAGE_UNAVAILABLE,
                        plugin_name=entry.name,
                        content_digest=entry.content_digest,
                        message=str(exc),
                    )
                )
            except PluginPackageCorrupt as exc:
                diagnostics.append(
                    PluginRestoreDiagnostic(
                        code=PluginRestoreDiagnosticCode.PACKAGE_CORRUPT,
                        plugin_name=entry.name,
                        content_digest=entry.content_digest,
                        message=str(exc),
                    )
                )
        return checkouts, diagnostics


def _snapshot_unavailable(digest: str, detail: str) -> PluginRestoreDiagnostic:
    return PluginRestoreDiagnostic(
        code=PluginRestoreDiagnosticCode.SNAPSHOT_UNAVAILABLE,
        content_digest=digest,
        message=f"the pinned blob {digest} cannot be read: {detail}",
    )


__all__ = [
    "RELEASE_TIMEOUT_SECONDS",
    "DeclaredAgentTypeProfile",
    "IgnoredNames",
    "PinnedPackage",
    "PinnedPlugins",
    "PluginContextDefinition",
    "PluginPinMismatch",
    "PluginRestoreDiagnostic",
    "PluginRestoreDiagnosticCode",
    "PluginRestoreError",
    "RestoredPlugins",
    "SessionPluginBinder",
    "SessionPluginBinding",
    "SessionPluginProjection",
    "SessionPluginProvider",
    "empty_plugin_binding",
]
