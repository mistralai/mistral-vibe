from __future__ import annotations

from enum import StrEnum
from typing import Final


class ExperimentName(StrEnum):
    SYSTEM_PROMPT = "vibe_cli_system_prompt"
    MANAGED_SHELL_TOOLS = "vibe_cli_managed_shell_tools"
    CLI_MODEL_ROUTING = "vibe_cli_default_routing_model"
    # Adds models to the dropdown for exposure/rollout without making any of them
    # the default (unlike CLI_MODEL_ROUTING, which couples add-to-dropdown with
    # make-default).
    CLI_EXTRA_MODELS = "vibe_cli_extra_models"


DEFAULT_VARIANTS: Final[dict[ExperimentName, str]] = {
    ExperimentName.SYSTEM_PROMPT: "cli",
    ExperimentName.MANAGED_SHELL_TOOLS: "legacy",
    ExperimentName.CLI_MODEL_ROUTING: "{}",
    ExperimentName.CLI_EXTRA_MODELS: "{}",
}

assert all(name in DEFAULT_VARIANTS for name in ExperimentName), (
    "Every ExperimentName must have a default in DEFAULT_VARIANTS"
)
