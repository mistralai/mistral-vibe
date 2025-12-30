"""Provider presets for common local LLM backends.

This module provides pre-configured settings for popular local LLM providers
like Ollama, llama.cpp, vLLM, LocalAI, and others.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.core.config import Backend, ModelConfig, ProviderConfig


@dataclass
class ProviderPreset:
    """A preset configuration for a provider."""

    name: str
    description: str
    api_base: str
    api_key_env_var: str
    backend: str  # "generic" or "mistral"
    default_model: str
    supports_streaming: bool = True
    supports_tools: bool = True
    notes: str = ""


# Built-in provider presets for common local LLM backends
PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "ollama": ProviderPreset(
        name="ollama",
        description="Ollama - Run LLMs locally with ease",
        api_base="http://localhost:11434/v1",
        api_key_env_var="",
        backend="generic",
        default_model="devstral",
        supports_streaming=True,
        supports_tools=True,
        notes="Start Ollama with: ollama serve",
    ),
    "llamacpp": ProviderPreset(
        name="llamacpp",
        description="llama.cpp server - High-performance local inference",
        api_base="http://127.0.0.1:8080/v1",
        api_key_env_var="",
        backend="generic",
        default_model="devstral",
        supports_streaming=True,
        supports_tools=True,
        notes="Start with: llama-server -m model.gguf --port 8080",
    ),
    "vllm": ProviderPreset(
        name="vllm",
        description="vLLM - High-throughput LLM serving",
        api_base="http://localhost:8000/v1",
        api_key_env_var="",
        backend="generic",
        default_model="devstral",
        supports_streaming=True,
        supports_tools=True,
        notes="Start with: vllm serve <model> --port 8000",
    ),
    "localai": ProviderPreset(
        name="localai",
        description="LocalAI - Self-hosted OpenAI-compatible API",
        api_base="http://localhost:8080/v1",
        api_key_env_var="",
        backend="generic",
        default_model="devstral",
        supports_streaming=True,
        supports_tools=True,
        notes="See https://localai.io for setup",
    ),
    "lmstudio": ProviderPreset(
        name="lmstudio",
        description="LM Studio - Desktop app for local LLMs",
        api_base="http://localhost:1234/v1",
        api_key_env_var="",
        backend="generic",
        default_model="devstral",
        supports_streaming=True,
        supports_tools=True,
        notes="Enable 'Local Server' in LM Studio settings",
    ),
    "openai": ProviderPreset(
        name="openai",
        description="OpenAI API",
        api_base="https://api.openai.com/v1",
        api_key_env_var="OPENAI_API_KEY",
        backend="generic",
        default_model="gpt-4o",
        supports_streaming=True,
        supports_tools=True,
        notes="Requires OPENAI_API_KEY environment variable",
    ),
    "openrouter": ProviderPreset(
        name="openrouter",
        description="OpenRouter - Access multiple LLM providers",
        api_base="https://openrouter.ai/api/v1",
        api_key_env_var="OPENROUTER_API_KEY",
        backend="generic",
        default_model="anthropic/claude-3.5-sonnet",
        supports_streaming=True,
        supports_tools=True,
        notes="Requires OPENROUTER_API_KEY environment variable",
    ),
    "together": ProviderPreset(
        name="together",
        description="Together AI - Fast inference API",
        api_base="https://api.together.xyz/v1",
        api_key_env_var="TOGETHER_API_KEY",
        backend="generic",
        default_model="mistralai/Devstral-Small-2505",
        supports_streaming=True,
        supports_tools=True,
        notes="Requires TOGETHER_API_KEY environment variable",
    ),
    "groq": ProviderPreset(
        name="groq",
        description="Groq - Ultra-fast inference",
        api_base="https://api.groq.com/openai/v1",
        api_key_env_var="GROQ_API_KEY",
        backend="generic",
        default_model="llama-3.3-70b-versatile",
        supports_streaming=True,
        supports_tools=True,
        notes="Requires GROQ_API_KEY environment variable",
    ),
    "mistral": ProviderPreset(
        name="mistral",
        description="Mistral AI API (default)",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var="MISTRAL_API_KEY",
        backend="mistral",
        default_model="mistral-vibe-cli-latest",
        supports_streaming=True,
        supports_tools=True,
        notes="Requires MISTRAL_API_KEY environment variable",
    ),
}


def get_provider_preset(name: str) -> ProviderPreset | None:
    """Get a provider preset by name (case-insensitive)."""
    return PROVIDER_PRESETS.get(name.lower())


def list_provider_presets() -> list[ProviderPreset]:
    """Get all available provider presets."""
    return list(PROVIDER_PRESETS.values())


def format_provider_list(
    presets: list[ProviderPreset], configured_providers: list[str] | None = None
) -> str:
    """Format provider presets for display."""
    lines = []
    lines.append("Available Provider Presets:")
    lines.append("=" * 50)

    # Group by local vs remote
    local_presets = [
        p for p in presets if p.api_base.startswith("http://localhost")
        or p.api_base.startswith("http://127.0.0.1")
    ]
    remote_presets = [p for p in presets if p not in local_presets]

    lines.append("\n[Local Providers]")
    for preset in local_presets:
        configured = (
            " (configured)" if configured_providers and preset.name in configured_providers else ""
        )
        lines.append(f"  {preset.name:<12} - {preset.description}{configured}")
        lines.append(f"               API: {preset.api_base}")
        if preset.notes:
            lines.append(f"               Note: {preset.notes}")

    lines.append("\n[Remote Providers]")
    for preset in remote_presets:
        configured = (
            " (configured)" if configured_providers and preset.name in configured_providers else ""
        )
        lines.append(f"  {preset.name:<12} - {preset.description}{configured}")
        lines.append(f"               API: {preset.api_base}")
        if preset.api_key_env_var:
            lines.append(f"               Key: ${preset.api_key_env_var}")

    lines.append("\nUsage Examples:")
    lines.append("  vibe --provider ollama --model devstral")
    lines.append("  vibe --provider vllm --api-base http://localhost:8000/v1")
    lines.append("  vibe --provider openai --model gpt-4o")

    return "\n".join(lines)


def create_provider_config_from_preset(
    preset: ProviderPreset, api_base_override: str | None = None
) -> "ProviderConfig":
    """Create a ProviderConfig from a preset."""
    from vibe.core.config import Backend, ProviderConfig

    return ProviderConfig(
        name=preset.name,
        api_base=api_base_override or preset.api_base,
        api_key_env_var=preset.api_key_env_var,
        api_style="openai",
        backend=Backend.MISTRAL if preset.backend == "mistral" else Backend.GENERIC,
    )


def create_model_config_from_preset(
    preset: ProviderPreset, model_name: str | None = None
) -> "ModelConfig":
    """Create a ModelConfig from a preset."""
    from vibe.core.config import ModelConfig

    name = model_name or preset.default_model
    return ModelConfig(
        name=name,
        provider=preset.name,
        alias=f"{preset.name}-{name}",
        temperature=0.2,
        input_price=0.0,
        output_price=0.0,
    )
