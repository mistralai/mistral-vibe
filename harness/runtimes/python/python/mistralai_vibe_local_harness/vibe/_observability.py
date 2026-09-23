"""Low-cardinality OpenTelemetry instruments for Unified session recovery."""

from __future__ import annotations

from typing import Literal

from opentelemetry import metrics

type SessionOperation = Literal["open", "restore", "import", "replay", "compaction"]
type OperationOutcome = Literal["success", "failure"]
type EffectKind = Literal[
    "completion", "tool", "hook", "process", "callback", "child", "filesystem"
]
type ConnectorOperation = Literal["cleanup", "gateway_call"]
type ConnectorOutcome = Literal["cancelled", "failure", "success"]
type SubagentOperation = Literal[
    "list", "spawn", "wait", "send_message", "interrupt", "close"
]
type SubagentOperationOutcome = Literal["failure", "success", "timeout"]
type SubagentTerminalOutcome = Literal["completed", "failed", "interrupted"]
type SubagentRecoveryPhase = Literal[
    "action_reconciliation", "child_preflight", "child_watch"
]

_meter = metrics.get_meter(__name__)

_session_operation_duration = _meter.create_histogram(
    "mistral_ai.vibe_harness.session.operation.duration",
    unit="s",
    description="Unified Harness session persistence operation duration.",
)
_recovery_failures = _meter.create_counter(
    "mistral_ai.vibe_harness.session.recovery.failure",
    unit="{failure}",
    description="Unified Harness typed recovery failures.",
)
_effect_reconciliation_duration = _meter.create_histogram(
    "mistral_ai.vibe_harness.effect.reconciliation.duration",
    unit="s",
    description="Runtime effect reconciliation duration after restore.",
)
_uncorrelated_tool_results = _meter.create_counter(
    "mistral_ai.vibe_harness.tool.result.uncorrelated",
    unit="{result}",
    description="Tool results projected without a matching source call.",
)
_connector_operation_duration = _meter.create_histogram(
    "mistral_ai.vibe_harness.connector.operation.duration",
    unit="s",
    description="Unified Harness connector Runtime operation duration.",
)
_subagent_operation_duration = _meter.create_histogram(
    "mistral_ai.vibe_harness.subagent.operation.duration",
    unit="s",
    description="Unified Harness stateful-subagent operation duration.",
)
_subagent_active_turns = _meter.create_up_down_counter(
    "mistral_ai.vibe_harness.subagent.active_turns",
    unit="{turn}",
    description="Active child turns watched by this Unified Harness process.",
)
_subagent_recovery_failures = _meter.create_counter(
    "mistral_ai.vibe_harness.subagent.recovery.failure",
    unit="{failure}",
    description="Unified Harness stateful-subagent recovery failures.",
)
_subagent_orphans = _meter.create_counter(
    "mistral_ai.vibe_harness.subagent.orphan.detected",
    unit="{orphan}",
    description="Durable child Sessions missing their authoritative parent edge.",
)
_subagent_notification_lag = _meter.create_histogram(
    "mistral_ai.vibe_harness.subagent.notification.lag",
    unit="s",
    description="Delay between a durable child outcome and parent notification delivery.",
)


def record_session_operation(
    elapsed_s: float,
    *,
    operation: SessionOperation,
    outcome: OperationOutcome,
    source_backend: Literal["legacy", "unified", "none"] = "none",
) -> None:
    _session_operation_duration.record(
        elapsed_s,
        {
            "mistral_ai.vibe_harness.operation": operation,
            "mistral_ai.vibe_harness.outcome": outcome,
            "mistral_ai.vibe_harness.source.backend": source_backend,
        },
    )


def add_recovery_failure(*, failure_code: str, phase: str) -> None:
    _recovery_failures.add(
        1, {"error.type": failure_code, "mistral_ai.vibe_harness.restore.phase": phase}
    )


def record_effect_reconciliation(
    elapsed_s: float, *, kind: EffectKind, outcome: OperationOutcome
) -> None:
    _effect_reconciliation_duration.record(
        elapsed_s,
        {
            "mistral_ai.vibe_harness.effect.kind": kind,
            "mistral_ai.vibe_harness.outcome": outcome,
        },
    )


def add_uncorrelated_tool_result() -> None:
    _uncorrelated_tool_results.add(1)


def record_connector_operation(
    elapsed_s: float,
    *,
    operation: ConnectorOperation,
    outcome: ConnectorOutcome,
    error_type: str | None = None,
) -> None:
    attributes = {
        "mistral_ai.vibe_harness.connector.operation": operation,
        "mistral_ai.vibe_harness.outcome": outcome,
        "mistral_ai.vibe_harness.backend": "unified",
    }
    if error_type is not None:
        attributes["error.type"] = error_type
    _connector_operation_duration.record(elapsed_s, attributes)


def record_subagent_operation(
    elapsed_s: float,
    *,
    operation: SubagentOperation,
    outcome: SubagentOperationOutcome,
    recovering: bool,
) -> None:
    _subagent_operation_duration.record(
        elapsed_s,
        {
            "mistral_ai.vibe_harness.subagent.operation": operation,
            "mistral_ai.vibe_harness.outcome": outcome,
            "mistral_ai.vibe_harness.recovering": recovering,
            "mistral_ai.vibe_harness.backend": "unified",
        },
    )


def add_subagent_active_turns(delta: int) -> None:
    _subagent_active_turns.add(delta, {"mistral_ai.vibe_harness.backend": "unified"})


def add_subagent_recovery_failure(
    *, failure_code: str, phase: SubagentRecoveryPhase
) -> None:
    _subagent_recovery_failures.add(
        1,
        {
            "error.type": failure_code,
            "mistral_ai.vibe_harness.subagent.recovery.phase": phase,
            "mistral_ai.vibe_harness.backend": "unified",
        },
    )


def add_subagent_orphan_detected() -> None:
    _subagent_orphans.add(
        1,
        {
            "mistral_ai.vibe_harness.subagent.orphan.reason": "missing_parent_edge",
            "mistral_ai.vibe_harness.backend": "unified",
        },
    )


def record_subagent_notification_lag(
    elapsed_s: float, *, outcome: SubagentTerminalOutcome
) -> None:
    _subagent_notification_lag.record(
        elapsed_s,
        {
            "mistral_ai.vibe_harness.subagent.outcome": outcome,
            "mistral_ai.vibe_harness.backend": "unified",
        },
    )


__all__ = [
    "add_recovery_failure",
    "add_subagent_active_turns",
    "add_subagent_orphan_detected",
    "add_subagent_recovery_failure",
    "add_uncorrelated_tool_result",
    "record_connector_operation",
    "record_effect_reconciliation",
    "record_session_operation",
    "record_subagent_notification_lag",
    "record_subagent_operation",
]
