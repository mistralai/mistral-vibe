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
    CLI_EXTRA_MODELS = "vibe_cli_extra_models"
    REGISTRY_SKILLS = "vibe_cli_registry_skills"


DEFAULT_VARIANTS: Final[dict[ExperimentName, str]] = {
    ExperimentName.SYSTEM_PROMPT: "cli",
    ExperimentName.MANAGED_SHELL_TOOLS: "legacy",
    ExperimentName.CLI_MODEL_ROUTING: "{}",
    ExperimentName.CLI_EXTRA_MODELS: "{}",
    ExperimentName.REGISTRY_SKILLS: "off",
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
