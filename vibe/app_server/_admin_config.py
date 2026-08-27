"""Shared admin-config refresh and reporting, used by both session backends."""

from __future__ import annotations

from typing import Protocol

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


class AdminConfigTelemetry(Protocol):
    def send_admin_config_applied(
        self,
        *,
        outcome: AdminConfigOutcome,
        enforced_keys: list[str] | None = None,
        error: str | None = None,
    ) -> None: ...


def report_admin_config_outcome(
    result: AdminConfigApplyResult, *, telemetry: AdminConfigTelemetry | None = None
) -> None:
    if result.applied:
        if telemetry is not None:
            telemetry.send_admin_config_applied(
                outcome=AdminConfigOutcome.APPLIED, enforced_keys=result.enforced_keys
            )
        return
    if result.outcome not in _FETCH_FAILURES:
        return
    logger.warning(
        "Admin-managed config not applied outcome=%s error=%s",
        result.outcome.value,
        result.error,
    )
    if telemetry is not None:
        telemetry.send_admin_config_applied(outcome=result.outcome, error=result.error)


async def refresh_admin_layer(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> AdminConfigApplyResult:
    """Fetch org-enforced config, validate it, and load it into the layer.

    Parseable TOML that fails merged-config validation is rolled back so it
    never stays in the live layer; otherwise it would re-break every later
    ``reload`` and config edit for the session. On success the merged config is
    already refreshed. Returns the outcome for the caller to report.
    """
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

    try:
        layer = orchestrator.get_layer(AdminConfigLayer.NAME)
    except KeyError:
        layer = None
    if not isinstance(layer, AdminConfigLayer):
        return AdminConfigApplyResult(
            AdminConfigOutcome.APPLY_FAILED, error="admin layer unavailable"
        )
    return await _load_admin_layer(orchestrator, layer, managed.toml)


async def _load_admin_layer(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    layer: AdminConfigLayer,
    toml_text: str,
) -> AdminConfigApplyResult:
    previous = layer.snapshot()
    try:
        layer.load_managed_toml(toml_text)
    except Exception as exc:
        logger.warning("Failed to load admin-managed config", exc_info=exc)
        return AdminConfigApplyResult(AdminConfigOutcome.PARSE_FAILED, error=str(exc))

    try:
        await orchestrator.reload()
    except Exception as exc:
        layer.restore(previous)
        await orchestrator.reload()
        logger.warning("Admin-managed config failed validation", exc_info=exc)
        return AdminConfigApplyResult(AdminConfigOutcome.APPLY_FAILED, error=str(exc))

    return AdminConfigApplyResult(
        AdminConfigOutcome.APPLIED, enforced_keys=layer.enforced_keys
    )


__all__ = ["AdminConfigTelemetry", "refresh_admin_layer", "report_admin_config_outcome"]
