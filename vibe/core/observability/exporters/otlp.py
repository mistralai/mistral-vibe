"""OTLP exporters for OpenTelemetry signals."""

from __future__ import annotations

from typing import Any


def _import_exporter(module_path: str, class_name: str) -> Any:
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


def create_otlp_span_exporter(
    endpoint: str,
    headers: dict[str, str] | None = None,
) -> Any:
    try:
        exporter_cls = _import_exporter(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
            "OTLPSpanExporter",
        )
    except ImportError:
        exporter_cls = _import_exporter(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter",
            "OTLPSpanExporter",
        )
    return exporter_cls(endpoint=endpoint, headers=headers)


def create_otlp_metric_exporter(
    endpoint: str,
    headers: dict[str, str] | None = None,
) -> Any:
    try:
        exporter_cls = _import_exporter(
            "opentelemetry.exporter.otlp.proto.grpc.metric_exporter",
            "OTLPMetricExporter",
        )
    except ImportError:
        exporter_cls = _import_exporter(
            "opentelemetry.exporter.otlp.proto.http.metric_exporter",
            "OTLPMetricExporter",
        )
    return exporter_cls(endpoint=endpoint, headers=headers)


def create_otlp_log_exporter(
    endpoint: str,
    headers: dict[str, str] | None = None,
) -> Any:
    try:
        exporter_cls = _import_exporter(
            "opentelemetry.exporter.otlp.proto.grpc._log_exporter",
            "OTLPLogExporter",
        )
    except ImportError:
        exporter_cls = _import_exporter(
            "opentelemetry.exporter.otlp.proto.http._log_exporter",
            "OTLPLogExporter",
        )
    return exporter_cls(endpoint=endpoint, headers=headers)
