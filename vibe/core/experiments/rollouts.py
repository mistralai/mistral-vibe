from __future__ import annotations

from typing import TYPE_CHECKING, Final

from vibe.core.experiments.active import ExperimentName

if TYPE_CHECKING:
    from vibe.core.experiments.manager import ExperimentManager

MANAGED_SHELL_TOOLS_LEGACY: Final = "legacy"
MANAGED_SHELL_TOOLS_MANAGED: Final = "managed"


def managed_shell_tools_enabled(experiment_manager: ExperimentManager | None) -> bool:
    if experiment_manager is None:
        return False
    return (
        experiment_manager.get_variant(ExperimentName.MANAGED_SHELL_TOOLS)
        == MANAGED_SHELL_TOOLS_MANAGED
    )
