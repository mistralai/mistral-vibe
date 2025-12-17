"""Observability Configuration Module

Handles configuration for the OpenTelemetry observability system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

import json
import logging
import os
import tomllib

logger = logging.getLogger("mistral-vibe.observability.config")

OBSERVABILITY_CONFIG_FILENAMES = (
    "observability.toml",
    "observability.yaml",
    "observability.yml",
)
DEFAULT_CONFIG_DIR = Path.home() / ".vibe"


class ObservabilityConfig(BaseModel):
    """Configuration for Mistral Vibe Observability"""

    # Main settings
    enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry observability"
    )

    export_target: Literal["console", "otlp", "file", "none"] = Field(
        default="console",
        description="Telemetry export target"
    )

    development_mode: bool = Field(
        default=False,
        description="Enable development mode with enhanced logging"
    )

    sampling_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Sampling rate for traces (0.0 to 1.0)"
    )

    log_level: str = Field(
        default="INFO",
        description="Log level for observability system"
    )

    # OTLP specific settings
    otlp_endpoint: str = Field(
        default="http://localhost:4318",
        description="OTLP collector endpoint"
    )

    otlp_headers: dict = Field(
        default_factory=dict,
        description="Additional headers for OTLP requests"
    )

    # File specific settings
    file_path: str = Field(
        default="mistral-vibe-telemetry.jsonl",
        description="Path for file-based telemetry export"
    )

    # Component-specific toggles
    metrics_enabled: bool = Field(
        default=True,
        description="Enable metrics collection"
    )

    tracing_enabled: bool = Field(
        default=True,
        description="Enable distributed tracing"
    )

    logging_enabled: bool = Field(
        default=True,
        description="Enable structured logging"
    )

    # Trace behavior
    session_based_trace: bool = Field(
        default=False,
        description="When enabled, all agent executions within a CLI session share a single trace. "
                    "When disabled (default), each agent execution creates a separate trace."
    )


class ObservabilitySettings(BaseSettings):
    """Settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_prefix="MISTRAL_VIBE_TELEMETRY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool | None = Field(
        default=None,
        description="Enable telemetry (MISTRAL_VIBE_TELEMETRY_ENABLED)"
    )

    export_target: Literal["console", "otlp", "file", "none"] | None = Field(
        default=None,
        description="Export target (MISTRAL_VIBE_TELEMETRY_EXPORT_TARGET)"
    )

    development_mode: bool | None = Field(
        default=None,
        description="Development mode (MISTRAL_VIBE_TELEMETRY_DEVELOPMENT_MODE)"
    )

    sampling_rate: float | None = Field(
        default=None,
        description="Sampling rate (MISTRAL_VIBE_TELEMETRY_SAMPLING_RATE)"
    )

    log_level: str | None = Field(
        default=None,
        description="Log level (MISTRAL_VIBE_TELEMETRY_LOG_LEVEL)"
    )

    otlp_endpoint: str | None = Field(
        default=None,
        description="OTLP endpoint (MISTRAL_VIBE_TELEMETRY_OTLP_ENDPOINT)"
    )

    otlp_headers: dict | None = Field(
        default=None,
        description="OTLP headers (MISTRAL_VIBE_TELEMETRY_OTLP_HEADERS)"
    )

    file_path: str | None = Field(
        default=None,
        description="File path (MISTRAL_VIBE_TELEMETRY_FILE_PATH)"
    )

    metrics_enabled: bool | None = Field(
        default=None,
        description="Metrics enabled (MISTRAL_VIBE_TELEMETRY_METRICS_ENABLED)"
    )

    tracing_enabled: bool | None = Field(
        default=None,
        description="Tracing enabled (MISTRAL_VIBE_TELEMETRY_TRACING_ENABLED)"
    )

    logging_enabled: bool | None = Field(
        default=None,
        description="Logging enabled (MISTRAL_VIBE_TELEMETRY_LOGGING_ENABLED)"
    )

    session_based_trace: bool | None = Field(
        default=None,
        description="Session-based trace (MISTRAL_VIBE_TELEMETRY_SESSION_BASED_TRACE)"
    )


def _load_config_file(path: Path) -> dict[str, Any]:
    """Load telemetry configuration from supported config files"""
    if not path.exists():
        return {}

    if path.suffix == ".toml":
        with path.open("rb") as handle:
            return tomllib.load(handle).get("telemetry", {})

    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            logger.warning(
                "YAML telemetry config requested but PyYAML is unavailable: %s",
                exc,
            )
            return {}
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
            return data.get("telemetry", {})

    logger.warning("Unsupported telemetry config format: %s", path)
    return {}


def _parse_headers(headers: dict[str, str] | str | None) -> dict[str, str]:
    if headers is None:
        return {}
    if isinstance(headers, dict):
        return headers
    try:
        parsed = json.loads(headers)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except json.JSONDecodeError:
        logger.warning("Failed to parse OTLP headers JSON: %s", headers)
    return {}


def _apply_overrides(
    config: ObservabilityConfig, overrides: dict[str, Any] | None
) -> ObservabilityConfig:
    if not overrides:
        return config
    for key, value in overrides.items():
        if value is None:
            continue
        if key == "otlp_headers":
            setattr(config, key, _parse_headers(value))
        else:
            setattr(config, key, value)
    return config


def load_config(
    *,
    cli_overrides: dict[str, Any] | None = None,
    config_path: Path | None = None,
) -> ObservabilityConfig:
    """Load configuration following priority: CLI > env > config file > defaults"""

    selected_path = config_path
    if selected_path is None:
        config_dir = DEFAULT_CONFIG_DIR
        for candidate in OBSERVABILITY_CONFIG_FILENAMES:
            candidate_path = config_dir / candidate
            if candidate_path.exists():
                selected_path = candidate_path
                break

    file_values: dict[str, Any] = {}
    if selected_path:
        file_values = _load_config_file(selected_path)
        if "otlp_headers" in file_values:
            file_values["otlp_headers"] = _parse_headers(file_values["otlp_headers"])

    config = ObservabilityConfig(**file_values)

    env_settings = ObservabilitySettings()
    env_overrides = env_settings.model_dump(exclude_none=True)
    # Ensure otlp_headers from env is parsed correctly
    if "otlp_headers" in env_overrides:
        env_overrides["otlp_headers"] = _parse_headers(env_overrides["otlp_headers"])

    _apply_overrides(config, env_overrides)
    _apply_overrides(config, cli_overrides)

    return config


def get_version() -> str:
    """Get Mistral Vibe version"""
    try:
        # Try to get version from package metadata
        from importlib.metadata import version
        return version("mistral-vibe")
    except Exception:
        try:
            # Fallback to reading from pyproject.toml
            import tomllib
            with open("pyproject.toml", "rb") as f:
                data = tomllib.load(f)
                return data["project"]["version"]
        except Exception:
            return "unknown"
