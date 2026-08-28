"""Low-cardinality OpenTelemetry instruments for connector catalog operations."""

from __future__ import annotations

from typing import Literal

from opentelemetry import metrics

type ConnectorCatalogOperation = Literal[
    "authorization", "bootstrap", "cache_write", "convergence"
]
type ConnectorCatalogOutcome = Literal[
    "busy",
    "cancelled",
    "deduplicated",
    "failure",
    "invalid",
    "rejected",
    "stale",
    "success",
    "unavailable",
]
type ConnectorCacheDisposition = Literal["memory", "fresh_cache", "not_loaded"]

_meter = metrics.get_meter(__name__)

_catalog_operation_duration = _meter.create_histogram(
    "mistral_ai.vibe.connector.catalog.operation.duration",
    unit="s",
    description="Connector catalog operation duration observed by the app server.",
)
_cache_reads = _meter.create_counter(
    "mistral_ai.vibe.connector.catalog.cache.read",
    unit="{read}",
    description="Connector catalog cache reads by disposition.",
)


def record_connector_catalog_operation(
    elapsed_s: float,
    *,
    operation: ConnectorCatalogOperation,
    outcome: ConnectorCatalogOutcome,
    backend: Literal["legacy", "unified"],
) -> None:
    _catalog_operation_duration.record(
        elapsed_s,
        {
            "mistral_ai.vibe.connector.operation": operation,
            "mistral_ai.vibe.connector.outcome": outcome,
            "mistral_ai.vibe.harness.backend": backend,
        },
    )


def add_connector_cache_read(
    *, disposition: ConnectorCacheDisposition, backend: Literal["legacy", "unified"]
) -> None:
    _cache_reads.add(
        1,
        {
            "mistral_ai.vibe.connector.cache.disposition": disposition,
            "mistral_ai.vibe.harness.backend": backend,
        },
    )


__all__ = ["add_connector_cache_read", "record_connector_catalog_operation"]
