"""Exporter factory helpers for observability signals."""

from __future__ import annotations

from typing import Any

from opentelemetry.sdk._logs.export import LogExporter
from opentelemetry.sdk.metrics.export import MetricExporter
from opentelemetry.sdk.trace.export import SpanExporter

from vibe.core.observability.exporters.console import (
    create_console_log_exporter,
    create_console_metric_exporter,
    create_console_span_exporter,
)
from vibe.core.observability.exporters.file import (
    create_file_log_exporter,
    create_file_span_exporter,
)
from vibe.core.observability.exporters.otlp import (
    create_otlp_log_exporter,
    create_otlp_metric_exporter,
    create_otlp_span_exporter,
)


def build_span_exporter(
    export_target: str,
    *,
    endpoint: str,
    headers: dict[str, str],
    file_path: str,
) -> SpanExporter | None:
    match export_target:
        case "console":
            return create_console_span_exporter()
        case "otlp":
            return create_otlp_span_exporter(endpoint, headers)
        case "file":
            return create_file_span_exporter(file_path)
        case "none" | "":
            return None
        case _:
            return None


def build_metric_exporter(
    export_target: str,
    *,
    endpoint: str,
    headers: dict[str, str],
) -> MetricExporter | None:
    match export_target:
        case "console" | "file":
            # File metrics fall back to console exporter for readability
            return create_console_metric_exporter()
        case "otlp":
            return create_otlp_metric_exporter(endpoint, headers)
        case "none" | "":
            return None
        case _:
            return None


def build_log_exporter(
    export_target: str,
    *,
    endpoint: str,
    headers: dict[str, str],
    file_path: str,
) -> LogExporter | None:
    match export_target:
        case "console":
            return create_console_log_exporter()
        case "otlp":
            return create_otlp_log_exporter(endpoint, headers)
        case "file":
            return create_file_log_exporter(file_path)
        case "none" | "":
            return None
        case _:
            return None
