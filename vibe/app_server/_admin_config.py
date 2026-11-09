"""Org config: loading it into the admin layer, and reporting what it did.

``OrgConfigService`` (``_org_config_service``) holds and refreshes it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum, auto
from typing import Final, Protocol

from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import (
    MANAGED_CONFIG_TIMEOUT,
    AdminConfigApplyResult,
    AdminConfigOutcome,
)
from vibe.core.config.layers.admin import admin_layer, enforced_keys
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.observability.logging import logger

_FETCH_FAILURES = frozenset({
    AdminConfigOutcome.FETCH_FAILED,
    AdminConfigOutcome.PARSE_FAILED,
    AdminConfigOutcome.APPLY_FAILED,
})

# Caps the whole retry budget of one fetch, not one attempt.
MANAGED_CONFIG_FETCH_BUDGET: Final = MANAGED_CONFIG_TIMEOUT * 1.5

Preflight = Callable[[VibeConfigSchema], Awaitable[None]]


class AdminConfigTelemetry(Protocol):
    def send_admin_config_applied(
        self,
        *,
        outcome: AdminConfigOutcome,
        enforced_keys: list[str] | None = None,
        error: str | None = None,
    ) -> None: ...


class OrgConfigSource(StrEnum):
    """Which org config a session build applies."""

    CACHED = auto()
    """The copy the process holds: every build except a session start."""
    ENSURED = auto()
    """The copy the process holds, waiting for a first one when it holds
    none: a session start."""
    NONE = auto()
    """Local config only: the last fallback when the org config cannot be used."""


@dataclass(frozen=True, slots=True)
class OrgConfigAttempt:
    """What one session build applied of the org config: the source it was
    asked for, and what that turned out to be.
    """

    source: OrgConfigSource
    saved: str | None = None
    """The cached copy the build applied."""
    fetch_outcome: AdminConfigApplyResult | None = None
    """Why a start with nothing cached still has no org config."""
    saved_failure: AdminConfigApplyResult | None = None
    """Why the cached copy did not apply, when one is cached."""
    considered: str | None = None
    """The cached copy the build tried, applied or not."""

    @property
    def cache_failure(self) -> AdminConfigApplyResult | None:
        """The cached copy's failure, for the session to report, unless the
        fetch has a failure of its own to report.
        """
        fetch = self.fetch_outcome
        if fetch is not None and fetch.outcome is not AdminConfigOutcome.FETCH_FAILED:
            return None
        return self.saved_failure

    def next_source(self) -> OrgConfigSource | None:
        """The build to try after this one failed: local config after a build
        that applied an org config, else ``None``, the failure being local.
        """
        return None if self.saved is None else OrgConfigSource.NONE


@dataclass(frozen=True, slots=True)
class OrgConfigReport:
    """What a session build reports about the org config once the session
    runs under it.
    """

    outcome: AdminConfigApplyResult | None = None
    """The fetch outcome, or the failure the build fell back from."""
    excluded_agent: str | None = None
    fetch_outcome: AdminConfigApplyResult | None = None
    """Why a start with nothing cached still has no org config."""


@dataclass(slots=True)
class OrgConfigReportSlot:
    """The report a session context carries until a session publishes it.

    Taken once: ``/clear`` and rewind reuse the context, and a session must
    not report its start again.
    """

    report: OrgConfigReport | None = None
    loaded: str = ""
    """The org config TOML this context's config runs; ``""`` for none. What a
    session opened on it (/clear, rewind) starts following from. Kept by
    ``take``: it is not news to report."""
    refused: set[str] = field(default_factory=set)
    """Org configs a session on this context could not run under: no session
    on it tries them again until ``/reload``."""

    def take(self) -> OrgConfigReport | None:
        report, self.report = self.report, None
        return report

    def inherit(self, earlier: OrgConfigReport | None) -> None:
        """Keep an earlier build's fetch outcome when this one has none to
        report. Only the fetch's: a failure the earlier build fell back from
        happened in its own folder, which this build replaces.
        """
        if earlier is None or earlier.fetch_outcome is None:
            return
        if (self.report and self.report.outcome) is not None:
            return
        self.report = replace(
            self.report or OrgConfigReport(), outcome=earlier.fetch_outcome
        )


def report_admin_config_outcome(
    result: AdminConfigApplyResult,
    *,
    telemetry: AdminConfigTelemetry | None = None,
    quiet_fetch_failures: bool = False,
) -> None:
    if result.applied:
        if telemetry is not None:
            telemetry.send_admin_config_applied(
                outcome=AdminConfigOutcome.APPLIED, enforced_keys=result.enforced_keys
            )
        return
    if result.outcome not in _FETCH_FAILURES:
        return
    # An endpoint nobody can reach (self-hosted, offline) is not operator-
    # actionable on a fetch that runs once per session start, so callers on
    # that path ask for debug. A layer that loaded and then failed to apply
    # always is, whoever asked.
    quiet = quiet_fetch_failures and result.outcome is AdminConfigOutcome.FETCH_FAILED
    log = logger.debug if quiet else logger.warning
    log(
        "Admin-managed config not applied outcome=%s error=%s",
        result.outcome.value,
        result.error,
    )
    if telemetry is not None:
        telemetry.send_admin_config_applied(outcome=result.outcome, error=result.error)


def answer_outcome(toml_text: str) -> str | AdminConfigApplyResult:
    """What ``load_fetched_admin_config`` takes for a cached answer."""
    return toml_text or AdminConfigApplyResult(AdminConfigOutcome.DISABLED)


async def load_fetched_admin_config(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    fetched: str | AdminConfigApplyResult,
    *,
    preflight: Preflight | None = None,
) -> tuple[AdminConfigApplyResult, str | None]:
    """Load what the fetch returned into the admin layer.

    A fetched policy replaces the layer. When the org enforces nothing, the
    layer is emptied: what this session loaded before -- the cached copy at
    start, or an earlier fetch -- stops applying, and the result keeps the
    ``DISABLED`` outcome. A failed fetch leaves the layer as it is.

    Also returns the answer for the caller to cache once the session can run
    under it: the policy, ``""`` when the org enforces nothing, or ``None``
    when the fetch failed or the policy was rolled back. Caching an unchanged
    answer again restarts its expiry.
    """
    if isinstance(fetched, str):
        result = await load_admin_layer(orchestrator, fetched, preflight=preflight)
        return result, fetched if result.applied else None
    if fetched.outcome is not AdminConfigOutcome.DISABLED:
        return fetched, None
    if not enforced_keys(orchestrator):
        return fetched, ""
    result = await load_admin_layer(orchestrator, "", preflight=preflight)
    if not result.applied:
        return result, None
    return replace(result, outcome=AdminConfigOutcome.DISABLED), ""


async def load_admin_layer(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    toml_text: str,
    *,
    preflight: Preflight | None = None,
) -> AdminConfigApplyResult:
    """``preflight`` is the caller's own acceptance test for the merged config
    -- for a session backend, whether the Core can be built from it. Anything it
    rejects is rolled back alongside what fails validation, rather than left
    live for every later reload and config edit to trip over.
    """
    layer = admin_layer(orchestrator)
    if layer is None:
        return AdminConfigApplyResult(
            AdminConfigOutcome.APPLY_FAILED, error="admin layer unavailable"
        )

    previous = layer.snapshot()
    try:
        layer.load_managed_toml(toml_text)
    except Exception as exc:
        logger.warning("Failed to load admin-managed config", exc_info=exc)
        return AdminConfigApplyResult(AdminConfigOutcome.PARSE_FAILED, error=str(exc))

    try:
        await orchestrator.reload_layer(layer.name, preflight=preflight)
    except BaseException as exc:
        # Cancellation must roll the layer back and still propagate: callers
        # (shutdown, a superseded refresh) await the cancellation itself, and
        # a rollback reload here would reconfigure a session being torn down.
        layer.restore(previous)
        if not isinstance(exc, Exception):
            raise
        await orchestrator.reload_layer(layer.name)
        logger.warning("Admin-managed config failed validation", exc_info=exc)
        return AdminConfigApplyResult(AdminConfigOutcome.APPLY_FAILED, error=str(exc))

    return AdminConfigApplyResult(
        AdminConfigOutcome.APPLIED, enforced_keys=layer.enforced_keys
    )


async def apply_held_admin_config(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], held: str | None
) -> tuple[str | None, AdminConfigApplyResult | None]:
    """Apply the org config the process holds. Returns the copy it tried, and
    why it did not apply.

    A copy this folder's config rejects is never fatal: it is skipped, and
    returned as a failure for a session to report.
    """
    cached = held
    if not cached:
        return None, None
    result = await load_admin_layer(orchestrator, cached)
    if not result.applied:
        logger.debug(
            "Cached managed config not applied outcome=%s", result.outcome.value
        )
        return cached, result
    return cached, None


__all__ = [
    "MANAGED_CONFIG_FETCH_BUDGET",
    "AdminConfigTelemetry",
    "OrgConfigAttempt",
    "OrgConfigReport",
    "OrgConfigReportSlot",
    "OrgConfigSource",
    "answer_outcome",
    "apply_held_admin_config",
    "load_admin_layer",
    "load_fetched_admin_config",
    "report_admin_config_outcome",
]
