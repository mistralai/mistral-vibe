"""Org config: fetch, load into the admin layer, cache fallback, and reporting."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum, auto
from typing import Final, Protocol

from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import (
    MANAGED_CONFIG_TIMEOUT,
    AdminConfigApplyResult,
    AdminConfigOutcome,
    fetch_managed_config,
)
from vibe.core.config.layers.admin import admin_layer, enforced_keys
from vibe.core.config.managed_config_cache import load_cached_managed_config
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key

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
    """The cached copy: every build except a session start."""
    FETCHED = auto()
    """The server's answer now, over the cached copy: a session start."""
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
    fetched: str | None = None
    """The server's answer the build applied (``""``: the org enforces
    nothing); ``None`` when nothing was fetched or the answer was rolled back.
    Cached once the session can run under it."""
    fetch_outcome: AdminConfigApplyResult | None = None
    """What the fetch did, for a build that fetched."""
    saved_failure: AdminConfigApplyResult | None = None
    """Why the cached copy did not apply, when one is cached."""

    @property
    def cache_failure(self) -> AdminConfigApplyResult | None:
        """The cached copy's failure, for the session to report: only when no
        fetched answer replaced the cached copy, and the fetch has no failure
        of its own to report.
        """
        if self.saved_failure is None or self.fetched is not None:
            return None
        fetch = self.fetch_outcome
        if fetch is not None and fetch.outcome is not AdminConfigOutcome.FETCH_FAILED:
            return None
        return self.saved_failure

    @property
    def loaded(self) -> str | None:
        """The org config the build applied; ``None`` when none."""
        if self.fetched is None:
            return self.saved
        return self.fetched or None

    def next_source(self) -> OrgConfigSource | None:
        """The build to try after this one failed, or ``None`` when its failure
        is not the org config's.

        Never builds the same org config twice: the cached copy only when it
        differs from what failed, and local config only after a build that
        applied an org config.
        """
        if self.loaded is None:
            return None
        if (
            self.source is OrgConfigSource.FETCHED
            and self.saved is not None
            and self.saved != self.loaded
        ):
            return OrgConfigSource.CACHED
        return OrgConfigSource.NONE


@dataclass(frozen=True, slots=True)
class OrgConfigReport:
    """What a session build reports about the org config once the session
    runs under it.
    """

    outcome: AdminConfigApplyResult | None = None
    """The fetch outcome, or the failure the build fell back from."""
    excluded_agent: str | None = None
    fetch_outcome: AdminConfigApplyResult | None = None
    """What became of the fetched answer, for a build that fetched:
    ``APPLY_FAILED`` when the build could not run under it."""

    @property
    def fetched_answer_rejected(self) -> bool:
        """The build could not run under the fetched answer in its folder. The
        project config there can be the cause, so a rebuild in another folder
        fetches again rather than read an older cached copy.
        """
        return (
            self.fetch_outcome is not None
            and self.fetch_outcome.outcome is AdminConfigOutcome.APPLY_FAILED
        )


@dataclass(slots=True)
class OrgConfigReportSlot:
    """The report a session context carries until a session publishes it.

    Taken once: ``/clear`` and rewind reuse the context, and a session must
    not report its start again.
    """

    report: OrgConfigReport | None = None

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


async def refresh_admin_layer(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    preflight: Preflight | None = None,
) -> AdminConfigApplyResult:
    """Parseable TOML that fails merged-config validation is rolled back so it
    never stays in the live layer; otherwise it would re-break every later
    ``reload`` and config edit for the session. On success the merged config is
    already refreshed.
    """
    fetched = await fetch_admin_toml(orchestrator)
    if isinstance(fetched, AdminConfigApplyResult):
        return fetched
    return await load_admin_layer(orchestrator, fetched, preflight=preflight)


async def fetch_admin_toml(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> str | AdminConfigApplyResult:
    """The org config TOML, or why there is none.

    Never raises, and returns within ``MANAGED_CONFIG_FETCH_BUDGET``: a session
    start and ``/reload`` both wait on it, and any failure is a failed fetch,
    never a failed start or reload.
    """
    try:
        async with asyncio.timeout(MANAGED_CONFIG_FETCH_BUDGET):
            return await _fetch_admin_toml(orchestrator)
    except TimeoutError:
        error = "timed out"
    except Exception as exc:
        error = str(exc)
    return AdminConfigApplyResult(AdminConfigOutcome.FETCH_FAILED, error=error)


async def _fetch_admin_toml(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> str | AdminConfigApplyResult:
    config = orchestrator.config
    provider = config.get_mistral_provider()
    api_key = resolve_api_key(provider.api_key_env_var) if provider else None
    if not api_key:
        return AdminConfigApplyResult(AdminConfigOutcome.NO_API_KEY)

    fetched = await fetch_managed_config(config.vibe_base_url, api_key)
    if fetched.error is not None:
        # A fetch failure keeps the cached policy: the session stays under
        # the last known org config instead of silently falling back to
        # unenforced settings.
        return AdminConfigApplyResult(
            AdminConfigOutcome.FETCH_FAILED, error=fetched.error
        )
    managed = fetched.config
    if managed is None or not managed.is_enabled or managed.toml is None:
        # A valid answer that the org enforces nothing ("no_config", or an
        # admin who turned it off).
        return AdminConfigApplyResult(AdminConfigOutcome.DISABLED)
    return managed.toml


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
        await orchestrator.reload(preflight=preflight)
    except BaseException as exc:
        # Cancellation must roll the layer back and still propagate: callers
        # (shutdown, a superseded refresh) await the cancellation itself, and
        # a rollback reload here would reconfigure a session being torn down.
        layer.restore(previous)
        if not isinstance(exc, Exception):
            raise
        await orchestrator.reload()
        logger.warning("Admin-managed config failed validation", exc_info=exc)
        return AdminConfigApplyResult(AdminConfigOutcome.APPLY_FAILED, error=str(exc))

    return AdminConfigApplyResult(
        AdminConfigOutcome.APPLIED, enforced_keys=layer.enforced_keys
    )


async def apply_cached_admin_config(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], cache_key: str | None
) -> tuple[str | None, AdminConfigApplyResult | None]:
    """Apply the last cached managed config. Returns it if it applied, else
    why it did not.

    At a session start it is the fallback the fetch replaces; for requests
    outside a session (``agents/list``, ``config/read``) it is the only
    source, since those fetch only to fill it. A stale, corrupt, or invalid cached
    policy is never fatal: it is skipped. One this folder's config rejects
    is returned as a failure, for a session to report.
    """
    cached = load_cached_managed_config(cache_key)
    if not cached:
        return None, None
    result = await load_admin_layer(orchestrator, cached)
    if not result.applied:
        logger.debug(
            "Cached managed config not applied outcome=%s", result.outcome.value
        )
        return None, result
    return cached, None


__all__ = [
    "AdminConfigTelemetry",
    "OrgConfigAttempt",
    "OrgConfigReport",
    "OrgConfigReportSlot",
    "OrgConfigSource",
    "apply_cached_admin_config",
    "fetch_admin_toml",
    "load_admin_layer",
    "load_fetched_admin_config",
    "refresh_admin_layer",
    "report_admin_config_outcome",
]
