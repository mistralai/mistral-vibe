"""Metrics Module

Core metrics collection for Mistral Vibe observability.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import os
import sys
try:  # pragma: no cover - platform specific fallback
    import resource
except ImportError:  # Windows
    resource = None  # type: ignore
from typing import Any

from opentelemetry.metrics import (
    CallbackOptions,
    Counter,
    Histogram,
    ObservableGauge,
    Observation,
    UpDownCounter,
    get_meter,
)

from vibe.core.observability.semconv import (
    METRIC_AGENT_EXECUTION_COUNT,
    METRIC_AGENT_EXECUTION_DURATION,
    METRIC_GENAI_CLIENT_OPERATION_DURATION,
    METRIC_GENAI_CLIENT_TOKEN_USAGE,
    METRIC_SYSTEM_CPU_USAGE,
    METRIC_SYSTEM_MEMORY_USAGE,
    METRIC_TOOL_EXECUTION_COUNT,
    METRIC_TOOL_EXECUTION_DURATION,
)


class MetricsManager:
    """Manages metrics collection for Mistral Vibe"""
    
    def __init__(self) -> None:
        self._meter = None
        self._metrics = {}

    def _get_meter(self):
        if self._meter is None:
            self._meter = get_meter("mistral-vibe")
        return self._meter

    def reset_meter(self) -> None:
        """Reset cached meter after provider changes"""
        self._meter = get_meter("mistral-vibe")
        self._metrics.clear()
        
    def get_counter(self, name: str, description: str = "", unit: str = "1") -> Counter:
        """Get or create a counter metric"""
        if name not in self._metrics:
            self._metrics[name] = self._get_meter().create_counter(
                name=name,
                description=description,
                unit=unit
            )
        return self._metrics[name]
    
    def get_histogram(self, name: str, description: str = "", unit: str = "ms") -> Histogram:
        """Get or create a histogram metric"""
        if name not in self._metrics:
            self._metrics[name] = self._get_meter().create_histogram(
                name=name,
                description=description,
                unit=unit
            )
        return self._metrics[name]
    
    def get_updown_counter(self, name: str, description: str = "", unit: str = "1") -> UpDownCounter:
        """Get or create an up/down counter metric"""
        if name not in self._metrics:
            self._metrics[name] = self._get_meter().create_up_down_counter(
                name=name,
                description=description,
                unit=unit
            )
        return self._metrics[name]
    
    def get_observable_gauge(
        self,
        name: str,
        description: str = "",
        unit: str = "1",
        callbacks: list[Callable[[CallbackOptions], Iterable[Observation]]] | None = None,
    ) -> ObservableGauge:
        """Get or create an observable gauge metric"""
        if name not in self._metrics:
            kwargs: dict[str, Any] = dict(name=name, description=description, unit=unit)
            if callbacks:
                kwargs["callbacks"] = callbacks
            self._metrics[name] = self._get_meter().create_observable_gauge(**kwargs)
        return self._metrics[name]


# Global metrics manager instance
_metrics_manager = MetricsManager()


def get_metrics_manager() -> MetricsManager:
    """Get global metrics manager instance"""
    return _metrics_manager


def reset_meter() -> None:
    """Reset cached meter on provider changes"""
    _metrics_manager.reset_meter()


# Re-export metric names for backward compatibility
AGENT_EXECUTION_COUNT = METRIC_AGENT_EXECUTION_COUNT
AGENT_EXECUTION_DURATION = METRIC_AGENT_EXECUTION_DURATION
TOOL_EXECUTION_COUNT = METRIC_TOOL_EXECUTION_COUNT
TOOL_EXECUTION_DURATION = METRIC_TOOL_EXECUTION_DURATION
SYSTEM_MEMORY_USAGE = METRIC_SYSTEM_MEMORY_USAGE
SYSTEM_CPU_USAGE = METRIC_SYSTEM_CPU_USAGE
GENAI_CLIENT_TOKEN_USAGE = METRIC_GENAI_CLIENT_TOKEN_USAGE
GENAI_CLIENT_OPERATION_DURATION = METRIC_GENAI_CLIENT_OPERATION_DURATION


def init_core_metrics() -> None:
    """Initialize core metrics"""
    manager = get_metrics_manager()
    
    # Agent metrics
    manager.get_counter(
        AGENT_EXECUTION_COUNT,
        "Number of agent executions",
        "execution"
    )
    
    manager.get_histogram(
        AGENT_EXECUTION_DURATION,
        "Duration of agent executions",
        "ms"
    )
    
    # Tool metrics
    manager.get_counter(
        TOOL_EXECUTION_COUNT,
        "Number of tool executions",
        "execution"
    )
    
    manager.get_histogram(
        TOOL_EXECUTION_DURATION,
        "Duration of tool executions",
        "ms"
    )


def init_genai_metrics() -> None:
    """Initialize GenAI metrics following OTel GenAI semantic conventions"""
    manager = get_metrics_manager()
    
    # Token usage counter - will be recorded with attributes:
    # - gen_ai.token.type: "input" | "output"
    # - gen_ai.response.model: model name
    # - gen_ai.operation.name: "chat"
    manager.get_counter(
        GENAI_CLIENT_TOKEN_USAGE,
        "Measures number of input and output tokens used",
        "token"
    )
    
    # Operation duration histogram - will be recorded with attributes:
    # - gen_ai.response.model: model name  
    # - gen_ai.operation.name: "chat"
    manager.get_histogram(
        GENAI_CLIENT_OPERATION_DURATION,
        "GenAI operation duration",
        "s"  # OTel convention uses seconds
    )


def _memory_usage_callback(_: CallbackOptions) -> Iterable[Observation]:
    if resource is None:
        usage = 0
    else:
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform == "darwin":
                usage = usage / 1024  # macOS reports bytes
        except Exception:
            usage = 0
    yield Observation(int(usage), {})


def _cpu_usage_callback(_: CallbackOptions) -> Iterable[Observation]:
    try:
        load_avg = os.getloadavg()[0]
    except Exception:
        load_avg = 0.0
    yield Observation(float(load_avg), {})


def init_system_metrics() -> None:
    """Register system gauges for CPU and memory usage"""
    manager = get_metrics_manager()
    manager.get_observable_gauge(
        SYSTEM_MEMORY_USAGE,
        "Resident memory usage (kilobytes)",
        "kB",
        callbacks=[_memory_usage_callback],
    )
    manager.get_observable_gauge(
        SYSTEM_CPU_USAGE,
        "System load average (1 minute)",
        "1",
        callbacks=[_cpu_usage_callback],
    )
