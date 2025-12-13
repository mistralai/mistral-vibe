# Observability Usage Guide

## Quick Start

Enable observability with console output:

```bash
vibe --telemetry-enabled --telemetry-export-target console
```

## Configuration Methods

### 1. CLI Flags

| Flag | Description |
|------|-------------|
| `--telemetry-enabled` | Enable observability |
| `--no-telemetry-enabled` | Disable observability |
| `--telemetry-export-target` | `console`, `otlp`, `file`, `none` |
| `--telemetry-config` | Path to config file |
| `--telemetry-sampling-rate` | Sampling rate (0.0-1.0) |
| `--telemetry-development-mode` | Enable dev mode |
| `--telemetry-otlp-endpoint` | OTLP collector URL |
| `--telemetry-otlp-headers` | JSON headers for OTLP |
| `--telemetry-file-path` | Output file path |
| `--telemetry-log-level` | Log level |

### 2. Environment Variables

```bash
export MISTRAL_VIBE_TELEMETRY_ENABLED=true
export MISTRAL_VIBE_TELEMETRY_EXPORT_TARGET=otlp
export MISTRAL_VIBE_TELEMETRY_OTLP_ENDPOINT=http://localhost:4318
export MISTRAL_VIBE_TELEMETRY_SAMPLING_RATE=1.0
```

### 3. Config File

Create `~/.vibe/observability.toml`:

```toml
enabled = true
export_target = "otlp"
sampling_rate = 1.0
development_mode = false
log_level = "INFO"

# OTLP settings
otlp_endpoint = "http://localhost:4318"
otlp_headers = {}

# File export settings
file_path = "telemetry.jsonl"

# Component toggles
metrics_enabled = true
tracing_enabled = true
logging_enabled = true
```

## Export Targets

### Console

Print traces and metrics to stdout (for debugging):

```bash
vibe --telemetry-enabled --telemetry-export-target console
```

### OTLP

Send to OpenTelemetry Collector:

```bash
vibe --telemetry-enabled \
  --telemetry-export-target otlp \
  --telemetry-otlp-endpoint http://localhost:4318
```

### File

Write to JSONL file:

```bash
vibe --telemetry-enabled \
  --telemetry-export-target file \
  --telemetry-file-path ./traces.jsonl
```

## Configuration Priority

1. CLI flags (highest)
2. Environment variables
3. Config file
4. Defaults (lowest)

## Programmatic Usage

```python
from vibe.core.observability import ObservabilitySDK
from vibe.core.observability.config import ObservabilityConfig

config = ObservabilityConfig(
    enabled=True,
    export_target="otlp",
    otlp_endpoint="http://localhost:4318",
)

sdk = ObservabilitySDK(config)
sdk.initialize()

# Your code here...

sdk.shutdown()
```
