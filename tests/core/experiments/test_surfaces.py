from __future__ import annotations

import logging

import pytest

from vibe.core.experiments.active import (
    EXPERIMENT_SURFACES,
    ExperimentName,
    ExperimentSurface,
    is_exposure_eligible,
)


def test_every_experiment_declares_the_surfaces_it_applies_on() -> None:
    assert set(EXPERIMENT_SURFACES) == set(ExperimentName)


@pytest.mark.parametrize(
    "name",
    [
        ExperimentName.SYSTEM_PROMPT,
        ExperimentName.CLI_MODEL_ROUTING,
        ExperimentName.CLI_EXTRA_MODELS,
    ],
)
@pytest.mark.parametrize("surface", list(ExperimentSurface))
def test_shared_experiments_are_eligible_on_both_surfaces(
    name: ExperimentName, surface: ExperimentSurface
) -> None:
    assert is_exposure_eligible(name, surface)


def test_managed_shell_tools_is_inert_on_the_unified_surface() -> None:
    assert not is_exposure_eligible(
        ExperimentName.MANAGED_SHELL_TOOLS, ExperimentSurface.UNIFIED
    )
    assert is_exposure_eligible(
        ExperimentName.MANAGED_SHELL_TOOLS, ExperimentSurface.LEGACY
    )


def test_a_suppressed_exposure_says_which_experiment_and_surface(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="vibe"):
        is_exposure_eligible(
            ExperimentName.MANAGED_SHELL_TOOLS, ExperimentSurface.UNIFIED
        )

    assert any(
        ExperimentName.MANAGED_SHELL_TOOLS.value in record.getMessage()
        and ExperimentSurface.UNIFIED.value in record.getMessage()
        for record in caplog.records
    )


def test_an_eligible_exposure_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="vibe"):
        is_exposure_eligible(ExperimentName.SYSTEM_PROMPT, ExperimentSurface.UNIFIED)

    assert not caplog.records
