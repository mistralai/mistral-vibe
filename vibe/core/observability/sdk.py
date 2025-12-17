"""Observability SDK Module

Main SDK initialization and management for OpenTelemetry observability.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from opentelemetry._logs import set_logger_provider
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import set_tracer_provider

from vibe.core.observability.config import ObservabilityConfig, get_version
from vibe.core.observability.exporters import (
    build_log_exporter,
    build_metric_exporter,
    build_span_exporter,
)
from vibe.core.observability.metrics import (
    init_core_metrics,
    init_genai_metrics,
    init_system_metrics,
    reset_meter,
)
from vibe.core.observability.tracing import set_tracing_enabled, set_session_based_trace

if TYPE_CHECKING:
    pass


class ObservabilitySDK:
    """Main SDK class for Mistral Vibe Observability"""
    
    def __init__(self, config: ObservabilityConfig | None = None) -> None:
        """Initialize Observability SDK"""
        self.config = config or ObservabilityConfig()
        self.resource = None
        self.tracer_provider = None
        self.meter_provider = None
        self.logger_provider = None
        self._initialized = False
        
        # Set up logging
        self._setup_logging()
    
    def _setup_logging(self) -> None:
        """Set up Python logging for observability"""
        logging.basicConfig(
            level=self.config.log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        self.logger = logging.getLogger("mistral-vibe.observability")
        
        if self.config.development_mode:
            self.logger.setLevel("DEBUG")
            self.logger.debug("Development mode enabled - enhanced logging active")
    
    def initialize(self) -> bool:
        """Initialize OpenTelemetry SDK with configured exporters"""
        if not self.config.enabled:
            self.logger.info("Telemetry disabled by configuration")
            set_tracing_enabled(False)
            return False
        
        if self._initialized:
            self.logger.warning("SDK already initialized")
            return True
        
        try:
            self.logger.info("Initializing Mistral Vibe Observability SDK")
            
            # Create resource with service information
            self._create_resource()

            # Initialize providers based on configuration
            self._initialize_tracing()
            self._initialize_metrics()
            self._initialize_logging()
            
            # Set session-based trace mode
            set_session_based_trace(self.config.session_based_trace)
            
            self._initialized = True
            self.logger.info("Mistral Vibe Observability SDK initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Observability SDK: {e}", exc_info=True)
            self._initialized = False
            set_tracing_enabled(False)
            return False
    
    def _create_resource(self) -> None:
        """Create OpenTelemetry resource with service attributes"""
        # Handle opentelemetry version safely
        try:
            from importlib.metadata import version
            otel_version = version("opentelemetry-api")
        except Exception:
            otel_version = "unknown"
        
        self.resource = Resource.create({
            "service.name": "mistral-vibe",
            "service.version": get_version(),
            "telemetry.sdk.language": "python",
            "telemetry.sdk.name": "opentelemetry",
            "telemetry.sdk.version": otel_version,
        })
        self.logger.debug(f"Created resource: {self.resource}")
    
    def _initialize_tracing(self) -> None:
        """Initialize tracing provider and exporters"""
        if not self.config.tracing_enabled:
            self.logger.info("Tracing disabled by configuration")
            set_tracing_enabled(False)
            return

        span_exporter = build_span_exporter(
            self.config.export_target,
            endpoint=self.config.otlp_endpoint,
            headers=self.config.otlp_headers,
            file_path=self.config.file_path,
        )

        if span_exporter is None:
            self.logger.info("No span exporter configured")
            set_tracing_enabled(False)
            return

        sampler = ParentBased(TraceIdRatioBased(self.config.sampling_rate))
        self.tracer_provider = TracerProvider(
            resource=self.resource,
            sampler=sampler,
        )

        processor_cls = SimpleSpanProcessor if self.config.development_mode else BatchSpanProcessor
        self.tracer_provider.add_span_processor(processor_cls(span_exporter))

        set_tracer_provider(self.tracer_provider)
        set_tracing_enabled(True)

        self.logger.info(
            "Tracing initialized with %s exporter (sampling=%s)",
            self.config.export_target,
            self.config.sampling_rate,
        )
    
    def _initialize_metrics(self) -> None:
        """Initialize metrics provider and exporters"""
        if not self.config.metrics_enabled:
            self.logger.info("Metrics disabled by configuration")
            return

        metric_exporter = build_metric_exporter(
            self.config.export_target,
            endpoint=self.config.otlp_endpoint,
            headers=self.config.otlp_headers,
        )

        if metric_exporter is None:
            self.logger.info("No metric exporter configured")
            return

        export_interval = 10000 if self.config.development_mode else 60000

        metric_reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=export_interval,
        )

        self.meter_provider = MeterProvider(
            resource=self.resource,
            metric_readers=[metric_reader],
        )

        set_meter_provider(self.meter_provider)
        reset_meter()
        init_core_metrics()
        init_genai_metrics()
        init_system_metrics()

        self.logger.info(
            "Metrics initialized with %s exporter (interval=%sms)",
            self.config.export_target,
            export_interval,
        )
    
    def _initialize_logging(self) -> None:
        """Initialize logging provider and exporters"""
        if not self.config.logging_enabled:
            self.logger.info("Logging disabled by configuration")
            return

        log_exporter = build_log_exporter(
            self.config.export_target,
            endpoint=self.config.otlp_endpoint,
            headers=self.config.otlp_headers,
            file_path=self.config.file_path,
        )

        if log_exporter is None:
            self.logger.info("No log exporter configured")
            return

        self.logger_provider = LoggerProvider(resource=self.resource)
        log_processor = BatchLogRecordProcessor(log_exporter)
        self.logger_provider.add_log_record_processor(log_processor)

        set_logger_provider(self.logger_provider)

        self.logger.info("Logging initialized with %s exporter", self.config.export_target)
    
    def shutdown(self) -> None:
        """Gracefully shutdown SDK and flush data"""
        if not self._initialized:
            self.logger.info("SDK not initialized, nothing to shutdown")
            return
        
        self.logger.info("Shutting down Mistral Vibe Observability SDK")
        
        try:
            # Shutdown tracer provider
            if self.tracer_provider:
                self.tracer_provider.shutdown()
                self.logger.debug("Tracer provider shutdown complete")
            
            # Shutdown meter provider
            if self.meter_provider:
                self.meter_provider.shutdown()
                self.logger.debug("Meter provider shutdown complete")
            
            # Shutdown logger provider
            if self.logger_provider:
                self.logger_provider.shutdown()
                self.logger.debug("Logger provider shutdown complete")
            
            self._initialized = False
            set_tracing_enabled(False)
            self.logger.info("Mistral Vibe Observability SDK shutdown complete")
            
        except Exception as e:
            self.logger.error(f"Error during SDK shutdown: {e}", exc_info=True)
    
    def is_initialized(self) -> bool:
        """Check if SDK is initialized"""
        return self._initialized
    
    def __enter__(self) -> ObservabilitySDK:
        """Context manager entry"""
        self.initialize()
        return self
    
    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any | None) -> None:
        """Context manager exit"""
        self.shutdown()


def get_sdk() -> ObservabilitySDK:
    """Get global SDK instance"""
    global _global_sdk
    if _global_sdk is None:
        _global_sdk = ObservabilitySDK()
    return _global_sdk


# Global SDK instance
_global_sdk = None
