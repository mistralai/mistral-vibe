"""Console exporters for spans, metrics, and logs."""

from __future__ import annotations

from opentelemetry.sdk._logs.export import ConsoleLogExporter
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter


def create_console_span_exporter() -> ConsoleSpanExporter:
    return ConsoleSpanExporter()


def create_console_metric_exporter() -> ConsoleMetricExporter:
    return ConsoleMetricExporter()


def create_console_log_exporter() -> ConsoleLogExporter:
    return ConsoleLogExporter()
