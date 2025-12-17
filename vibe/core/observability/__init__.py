"""Mistral Vibe Observability Module

This module provides OpenTelemetry-based observability for Mistral Vibe,
including metrics, tracing, and logging capabilities.
"""

from __future__ import annotations

from vibe.core.observability.config import ObservabilityConfig
from vibe.core.observability.sdk import ObservabilitySDK

__all__ = ["ObservabilityConfig", "ObservabilitySDK"]