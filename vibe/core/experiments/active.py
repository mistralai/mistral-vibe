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
    # Independent smart-approve rollout flags, mirroring model routing: one exposes the
    # mode in the picker, the other makes it the default.
    SMART_APPROVE = "vibe_cli_smart_approve"
    SMART_APPROVE_DEFAULT = "vibe_cli_smart_approve_default"
    # Adds models to the dropdown for exposure/rollout without making any of them
    # the default (unlike CLI_MODEL_ROUTING, which couples add-to-dropdown with
    # make-default).
    CLI_EXTRA_MODELS = "vibe_cli_extra_models"
    REGISTRY_SKILLS = "vibe_cli_registry_skills"
    UNIFIED_HARNESS_ROLLOUT = "vibe_cli_unified_harness_rollout"
    # Read from the eval cache by the `vibe` launcher (vibe/cli/_rust.py).
    RUST_TUI_ROLLOUT = "vibe_cli_rust_tui_rollout"


DEFAULT_VARIANTS: Final[dict[ExperimentName, object]] = {
    ExperimentName.SYSTEM_PROMPT: "cli",
    ExperimentName.MANAGED_SHELL_TOOLS: "legacy",
    ExperimentName.CLI_MODEL_ROUTING: {},
    ExperimentName.SMART_APPROVE: False,
    ExperimentName.SMART_APPROVE_DEFAULT: False,
    ExperimentName.CLI_EXTRA_MODELS: {},
    ExperimentName.REGISTRY_SKILLS: False,
    ExperimentName.UNIFIED_HARNESS_ROLLOUT: "legacy",
    ExperimentName.RUST_TUI_ROLLOUT: "python",
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
    # No surface consumes the Unified Harness rollout assignment anymore:
    # unflagged startup constructs the Unified Runtime unconditionally and
    # fails fast, so reporting an exposure would claim a treatment that is
    # never served. The variant still resolves and caches (harmless), and the
    # remote GrowthBook feature stays pinned to "unified" while older Vibe
    # releases that still read it are supported. The declaration is removed
    # with the rest of the rollout contract in the legacy-harness cutover.
    ExperimentName.UNIFIED_HARNESS_ROLLOUT: frozenset(),
    ExperimentName.RUST_TUI_ROLLOUT: frozenset(ExperimentSurface),
    # ``managed_shell_tools_enabled`` is read only by the legacy ToolManager; the
    # Harness owns the tool surface, so the variant can never apply on Unified.
    ExperimentName.MANAGED_SHELL_TOOLS: frozenset({ExperimentSurface.LEGACY}),
    # Smart approve's ``classify`` tool-gate lives in the Unified Harness runtime;
    # the legacy backend has no classifier gate, so the variant only applies there.
    ExperimentName.SMART_APPROVE: frozenset({ExperimentSurface.UNIFIED}),
    ExperimentName.SMART_APPROVE_DEFAULT: frozenset({ExperimentSurface.UNIFIED}),
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
