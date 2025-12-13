"""File-based exporters that persist telemetry as JSON Lines."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Sequence

from opentelemetry.sdk._logs.export import LogExportResult, LogExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult, SpanExporter


def _ns_to_iso(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, timezone.utc).isoformat()


def _safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe_value(v) for k, v in value.items()}
    return str(value)


def _serialize_attributes(attributes: Any) -> dict[str, Any]:
    return {str(key): _safe_value(val) for key, val in (attributes or {}).items()}


def _serialize_span(span: ReadableSpan) -> dict[str, Any]:
    context = span.context
    parent = span.parent
    return {
        "name": span.name,
        "context": {
            "trace_id": f"{context.trace_id:032x}" if context else None,
            "span_id": f"{context.span_id:016x}" if context else None,
        },
        "kind": getattr(span.kind, "name", str(span.kind)),
        "parent_id": f"{parent.span_id:016x}" if parent else None,
        "start_time": _ns_to_iso(span.start_time),
        "end_time": _ns_to_iso(span.end_time),
        "status": {
            "code": getattr(span.status.status_code, "name", "UNSET"),
            "description": span.status.description,
        },
        "attributes": _serialize_attributes(span.attributes),
        "resource": _serialize_attributes(span.resource.attributes),
        "events": [
            {
                "name": event.name,
                "timestamp": _ns_to_iso(event.timestamp),
                "attributes": _serialize_attributes(event.attributes),
            }
            for event in span.events
        ],
        "links": [
            {
                "context": {
                    "trace_id": f"{link.context.trace_id:032x}",
                    "span_id": f"{link.context.span_id:016x}",
                },
                "attributes": _serialize_attributes(link.attributes),
            }
            for link in span.links
        ],
    }


def _serialize_log(record: Any) -> dict[str, Any]:
    body = getattr(record, "body", None)
    body_value = getattr(body, "body", body)
    timestamp = getattr(record, "timestamp", 0)
    severity_number = getattr(record, "severity_number", None)
    severity_text = getattr(record, "severity_text", None)
    attributes = getattr(record, "attributes", {})
    resource = getattr(record, "resource", None)
    trace_id = getattr(record, "trace_id", 0)
    span_id = getattr(record, "span_id", 0)

    return {
        "timestamp": _ns_to_iso(timestamp),
        "severity_number": getattr(severity_number, "value", severity_number),
        "severity_text": severity_text,
        "body": _safe_value(body_value),
        "attributes": _serialize_attributes(attributes),
        "resource": _serialize_attributes(getattr(resource, "attributes", {})),
        "trace_id": f"{trace_id:032x}" if trace_id else None,
        "span_id": f"{span_id:016x}" if span_id else None,
    }


class _JSONLWriter:
    def __init__(self, file_path: str | Path) -> None:
        self._path = Path(file_path)
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write_records(self, records: Iterable[dict[str, Any]]) -> None:
        payload = "".join(json.dumps(record, default=str) + "\n" for record in records)
        if not payload:
            return
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(payload)


class JSONLSpanExporter(SpanExporter):
    """Simple JSONL span exporter."""

    def __init__(self, file_path: str | Path) -> None:
        super().__init__()
        self._writer = _JSONLWriter(file_path)

    def export(self, spans: Iterable[ReadableSpan]) -> SpanExportResult:
        records = [_serialize_span(span) for span in spans]
        self._writer.write_records(records)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - nothing to cleanup
        return None


class JSONLLogExporter(LogExporter):
    """JSONL exporter for log records."""

    def __init__(self, file_path: str | Path) -> None:
        self._writer = _JSONLWriter(file_path)

    def export(self, batch: Sequence[Any]) -> LogExportResult:
        records = [_serialize_log(record) for record in batch]
        self._writer.write_records(records)
        return LogExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover - nothing to cleanup
        return None


def create_file_span_exporter(file_path: str) -> JSONLSpanExporter:
    return JSONLSpanExporter(file_path)


def create_file_log_exporter(file_path: str) -> JSONLLogExporter:
    return JSONLLogExporter(file_path)
