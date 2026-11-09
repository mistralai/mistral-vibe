from __future__ import annotations

from enum import StrEnum
from typing import Final

from vibe.observability.logging import logger


class ExperimentSurface(StrEnum):
    LEGACY = "legacy"
    UNIFIED = "unified"


class ExperimentName(StrEnum):
    SYSTEM_PROMPT = "vibe_cli_system_prompt"
    MANAGED_SHELL_TOOLS = "vibe_cli_managed_shell_tools"
    CLI_MODEL_ROUTING = "vibe_cli_default_routing_model"
    SMART_APPROVE = "vibe_cli_smart_approve"
    DEFAULT_AGENT = "vibe_cli_default_agent"
    # Adds models to the dropdown for exposure/rollout without making any of them
    # the default (unlike CLI_MODEL_ROUTING, which couples add-to-dropdown with
    # make-default).
    CLI_EXTRA_MODELS = "vibe_cli_extra_models"
    REGISTRY_SKILLS = "vibe_cli_registry_skills"
    MISTRAL_MCP_CONNECTOR = "vibe_cli_mistral_mcp_connector"
    LOCAL_RUNTIME_SESSION_API = "vibe_cli_local_runtime_session_api_migration"
    MODEL_CATALOG = "vibe_cli_model_catalog"
    MODEL_CATALOG_FILTER = "vibe_cli_model_catalog_filter"


DEFAULT_VARIANTS: Final[dict[ExperimentName, object]] = {
    ExperimentName.SYSTEM_PROMPT: "cli",
    ExperimentName.MANAGED_SHELL_TOOLS: "legacy",
    ExperimentName.CLI_MODEL_ROUTING: {},
    ExperimentName.SMART_APPROVE: False,
    ExperimentName.DEFAULT_AGENT: "",
    ExperimentName.CLI_EXTRA_MODELS: {},
    ExperimentName.REGISTRY_SKILLS: False,
    ExperimentName.MISTRAL_MCP_CONNECTOR: False,
    ExperimentName.LOCAL_RUNTIME_SESSION_API: False,
    ExperimentName.MODEL_CATALOG: False,
    ExperimentName.MODEL_CATALOG_FILTER: {},
}

assert all(name in DEFAULT_VARIANTS for name in ExperimentName), (
    "Every ExperimentName must have a default in DEFAULT_VARIANTS"
)


# Declared, not inferred from config-field reachability: a wrong inference fails
# silently in the direction that corrupts the readout.
EXPERIMENT_SURFACES: Final[dict[ExperimentName, frozenset[ExperimentSurface]]] = {
    ExperimentName.SYSTEM_PROMPT: frozenset(ExperimentSurface),
    ExperimentName.CLI_MODEL_ROUTING: frozenset(ExperimentSurface),
    ExperimentName.CLI_EXTRA_MODELS: frozenset(ExperimentSurface),
    ExperimentName.REGISTRY_SKILLS: frozenset(ExperimentSurface),
    # ``managed_shell_tools_enabled`` is read only by the legacy ToolManager; the
    # Harness owns the tool surface, so the variant can never apply on Unified.
    ExperimentName.MANAGED_SHELL_TOOLS: frozenset({ExperimentSurface.LEGACY}),
    # Smart approve's ``classify`` tool-gate lives in the Unified Harness runtime;
    # the legacy backend has no classifier gate, so the variant only applies there.
    ExperimentName.SMART_APPROVE: frozenset({ExperimentSurface.UNIFIED}),
    ExperimentName.DEFAULT_AGENT: frozenset({ExperimentSurface.UNIFIED}),
    ExperimentName.MISTRAL_MCP_CONNECTOR: frozenset(ExperimentSurface),
    # Only the Unified backend's app server talks to the local runtime.
    ExperimentName.LOCAL_RUNTIME_SESSION_API: frozenset({ExperimentSurface.UNIFIED}),
    ExperimentName.MODEL_CATALOG: frozenset(ExperimentSurface),
    ExperimentName.MODEL_CATALOG_FILTER: frozenset(ExperimentSurface),
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
