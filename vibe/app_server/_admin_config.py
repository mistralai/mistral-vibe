"""Shared admin-config refresh and reporting, used by both session backends."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import (
    AdminConfigApplyResult,
    AdminConfigOutcome,
    fetch_managed_config,
)
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key

_FETCH_FAILURES = frozenset({
    AdminConfigOutcome.FETCH_FAILED,
    AdminConfigOutcome.PARSE_FAILED,
    AdminConfigOutcome.APPLY_FAILED,
})

Preflight = Callable[[VibeConfigSchema], Awaitable[None]]


class AdminConfigTelemetry(Protocol):
    def send_admin_config_applied(
        self,
        *,
        outcome: AdminConfigOutcome,
        enforced_keys: list[str] | None = None,
        error: str | None = None,
    ) -> None: ...


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
    # actionable on a refresh that runs once per session open, so callers on
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


async def apply_admin_config(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    apply: Callable[[], Awaitable[bool]],
    telemetry: AdminConfigTelemetry | None = None,
    preflight: Preflight | None = None,
    lock: asyncio.Lock | None = None,
    timeout: float | None = None,
    quiet_fetch_failures: bool = False,
    on_failure: Callable[[AdminConfigApplyResult], None] | None = None,
) -> bool:
    """``apply`` lands the merged config on the running session and answers
    whether it actually changed what that session runs -- a backend that can
    only defer the push says ``False``, so the caller does not announce a
    runtime it is still about to replace. ``timeout`` caps the whole retry
    budget of the fetch, not one attempt.
    """
    try:
        async with asyncio.timeout(timeout):
            fetched = await fetch_admin_toml(orchestrator)
    except TimeoutError:
        fetched = AdminConfigApplyResult(
            AdminConfigOutcome.FETCH_FAILED, error="timed out"
        )
    if isinstance(fetched, AdminConfigApplyResult):
        if on_failure is not None:
            on_failure(fetched)
        report_admin_config_outcome(
            fetched, telemetry=telemetry, quiet_fetch_failures=quiet_fetch_failures
        )
        return False

    # Held across the merge and the apply but never across the fetch, so a
    # concurrent reconfiguration can neither derive from a half-merged config
    # nor land its older derivation last.
    guard: AbstractAsyncContextManager[Any] = (
        contextlib.nullcontext() if lock is None else lock
    )
    async with guard:
        result = await load_admin_layer(orchestrator, fetched, preflight=preflight)
        if not result.applied:
            if on_failure is not None:
                on_failure(result)
            report_admin_config_outcome(result, telemetry=telemetry)
            return False
        try:
            changed = await apply()
        except Exception as exc:
            logger.debug("Failed to apply admin-managed config", exc_info=exc)
            result = AdminConfigApplyResult(
                AdminConfigOutcome.APPLY_FAILED, error=str(exc)
            )
            if on_failure is not None:
                on_failure(result)
            report_admin_config_outcome(result, telemetry=telemetry)
            return False
    report_admin_config_outcome(result, telemetry=telemetry)
    return changed


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
    config = orchestrator.config
    provider = config.get_mistral_provider()
    api_key = resolve_api_key(provider.api_key_env_var) if provider else None
    if not api_key:
        return AdminConfigApplyResult(AdminConfigOutcome.NO_API_KEY)

    fetched = await fetch_managed_config(config.vibe_base_url, api_key)
    if fetched.error is not None:
        return AdminConfigApplyResult(
            AdminConfigOutcome.FETCH_FAILED, error=fetched.error
        )
    managed = fetched.config
    if managed is None or not managed.is_enabled or managed.toml is None:
        return AdminConfigApplyResult(AdminConfigOutcome.DISABLED)
    return managed.toml


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
    try:
        layer = orchestrator.get_layer(AdminConfigLayer.NAME)
    except KeyError:
        layer = None
    if not isinstance(layer, AdminConfigLayer):
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


__all__ = [
    "AdminConfigTelemetry",
    "apply_admin_config",
    "fetch_admin_toml",
    "load_admin_layer",
    "refresh_admin_layer",
    "report_admin_config_outcome",
]
