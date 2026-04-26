"""vibe/core/plugins/resilience.py

────────────────────────────────────────────────────────────────────────────
Plugin resilience — circuit breaker for plugin operations.

Provides circuit breaker protection for plugin lifecycle and tool hook
operations. Prevents a misbehaving plugin from blocking or
crashing the entire plugin system.
"""

from __future__ import annotations

import asyncio
import logging

import pybreaker
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig

logger = logging.getLogger(__name__)


class PluginCircuitListener:
    """Circuit breaker listener that logs state changes."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, cb: pybreaker.CircuitBreaker, ex: BaseException | None) -> None:
        """Called on state changes and failures."""
        state = cb.current_state
        if ex is not None:
            logger.debug(
                "Plugin circuit breaker [%s] recorded failure: %s",
                self._name,
                type(ex).__name__,
            )
        elif state == pybreaker.STATE_OPEN:
            logger.warning(
                "Plugin circuit breaker [%s] transitioned to OPEN",
                self._name,
            )
        elif state == pybreaker.STATE_CLOSED:
            logger.info(
                "Plugin circuit breaker [%s] transitioned to CLOSED",
                self._name,
            )
        elif state == pybreaker.STATE_HALF_OPEN:
            logger.warning(
                "Plugin circuit breaker [%s] transitioned to HALF_OPEN",
                self._name,
            )


def _get_circuit_breaker(config: VibeConfig) -> pybreaker.CircuitBreaker:
    """Create a circuit breaker instance configured from VibeConfig."""
    threshold = getattr(
        config,
        "plugin_circuit_breaker_failure_threshold",
        3,
    )
    timeout = getattr(
        config,
        "plugin_circuit_breaker_recovery_timeout_sec",
        30.0,
    )

    breaker = pybreaker.CircuitBreaker(
        fail_max=threshold,
        reset_timeout=timeout,
        exclude=[KeyboardInterrupt, asyncio.CancelledError],
    )
    listener = PluginCircuitListener("plugin_ops")
    breaker.add_listener(listener)  # type: ignore[arg-type]
    return breaker


PLUGIN_CIRCUIT_BREAKER: pybreaker.CircuitBreaker | None = None


def init_plugin_circuit_breaker(config: VibeConfig) -> pybreaker.CircuitBreaker:
    """Initialize and return the global plugin circuit breaker."""
    global PLUGIN_CIRCUIT_BREAKER  # noqa: PLW0603
    PLUGIN_CIRCUIT_BREAKER = _get_circuit_breaker(config)
    return PLUGIN_CIRCUIT_BREAKER


def get_plugin_circuit_breaker() -> pybreaker.CircuitBreaker:
    """Get the initialized plugin circuit breaker."""
    if PLUGIN_CIRCUIT_BREAKER is None:
        raise RuntimeError("Plugin circuit breaker not initialized")
    return PLUGIN_CIRCUIT_BREAKER