from __future__ import annotations

from enum import StrEnum
from typing import Final

from vibe.observability.logging import logger


class ExperimentSurface(StrEnum):
    LEGACY = "legacy"
    UNIFIED = "unified"


class ExperimentName(StrEnum):
    SYSTEM_PROMPT = "vibe_cli_system_prompt"
    # The behavior part of the system prompt, so a new one ships without a
    # release. A non-blank value takes precedence over the SYSTEM_PROMPT
    # variant's; the app server still appends the client surface block.
    SYSTEM_PROMPT_BEHAVIOR = "vibe_cli_system_prompt_behavior"
    MANAGED_SHELL_TOOLS = "vibe_cli_managed_shell_tools"
    SMART_APPROVE = "vibe_cli_smart_approve"
    DEFAULT_AGENT = "vibe_cli_default_agent"
    REGISTRY_SKILLS = "vibe_cli_registry_skills"
    MISTRAL_MCP_CONNECTOR = "vibe_cli_mistral_mcp_connector"
    MODEL_CATALOG_FILTER = "vibe_cli_model_catalog_filter"
    HIDE_MISTRAL_MEDIUM_LATEST = "vibe_cli_hide_mistral_medium_latest"
    STORAGE_USAGE = "vibe_storage_usage"


DEFAULT_VARIANTS: Final[dict[ExperimentName, object]] = {
    ExperimentName.SYSTEM_PROMPT: "cli",
    ExperimentName.SYSTEM_PROMPT_BEHAVIOR: "",
    ExperimentName.MANAGED_SHELL_TOOLS: "legacy",
    ExperimentName.SMART_APPROVE: False,
    ExperimentName.DEFAULT_AGENT: "",
    ExperimentName.REGISTRY_SKILLS: False,
    ExperimentName.MISTRAL_MCP_CONNECTOR: False,
    ExperimentName.MODEL_CATALOG_FILTER: {},
    ExperimentName.HIDE_MISTRAL_MEDIUM_LATEST: False,
    ExperimentName.STORAGE_USAGE: False,
}

assert all(name in DEFAULT_VARIANTS for name in ExperimentName), (
    "Every ExperimentName must have a default in DEFAULT_VARIANTS"
)


# Declared, not inferred from config-field reachability: a wrong inference fails
# silently in the direction that corrupts the readout.
EXPERIMENT_SURFACES: Final[dict[ExperimentName, frozenset[ExperimentSurface]]] = {
    ExperimentName.SYSTEM_PROMPT: frozenset(ExperimentSurface),
    # Only the Unified runtime composes the system prompt from this text.
    ExperimentName.SYSTEM_PROMPT_BEHAVIOR: frozenset({ExperimentSurface.UNIFIED}),
    ExperimentName.REGISTRY_SKILLS: frozenset(ExperimentSurface),
    # ``managed_shell_tools_enabled`` is read only by the legacy ToolManager; the
    # Harness owns the tool surface, so the variant can never apply on Unified.
    ExperimentName.MANAGED_SHELL_TOOLS: frozenset({ExperimentSurface.LEGACY}),
    # Smart approve's ``classify`` tool-gate lives in the Unified Harness runtime;
    # the legacy backend has no classifier gate, so the variant only applies there.
    ExperimentName.SMART_APPROVE: frozenset({ExperimentSurface.UNIFIED}),
    ExperimentName.DEFAULT_AGENT: frozenset({ExperimentSurface.UNIFIED}),
    ExperimentName.MISTRAL_MCP_CONNECTOR: frozenset(ExperimentSurface),
    ExperimentName.MODEL_CATALOG_FILTER: frozenset(ExperimentSurface),
    ExperimentName.HIDE_MISTRAL_MEDIUM_LATEST: frozenset(ExperimentSurface),
    ExperimentName.STORAGE_USAGE: frozenset(ExperimentSurface),
}

assert all(name in EXPERIMENT_SURFACES for name in ExperimentName), (
    "Every ExperimentName must declare its surfaces in EXPERIMENT_SURFACES"
)


def is_exposure_eligible(name: ExperimentName, surface: ExperimentSurface) -> bool:
    # An inert variant still resolves and still caches; only the exposure is
    # suppressed, because reporting one claims a treatment never served.
    if surface in EXPERIMENT_SURFACES[name]:
        return True
    logger.debug(
        "Suppressing %s exposure: no consumer on the %s surface",
        name.value,
        surface.value,
    )
    return False
