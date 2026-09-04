"""Loop integration for the background registry-skills sync.

Covers the seam wired in the agent loop (not the sync engine itself, which is
tested in tests/skills/registry): rebuild-on-OK, skip-on-non-OK, and the
generation guard that prevents clobbering a concurrent reload.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
import vibe.core.agent_loop._loop as agent_loop_module
from vibe.core.skills.registry._service import RegistrySyncResult, RegistrySyncStatus


def _loop():
    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    return build_test_agent_loop(config=config)


@pytest.mark.asyncio
async def test_refresh_rebuilds_manager_and_refreshes_prompt() -> None:
    loop = _loop()
    before = loop.skill_manager
    with (
        patch.object(
            agent_loop_module,
            "refresh_registry_skills",
            AsyncMock(return_value=RegistrySyncResult(RegistrySyncStatus.OK)),
        ),
        patch.object(
            loop, "_refresh_system_prompt_unless_reloaded", AsyncMock()
        ) as prompt,
    ):
        await loop._refresh_registry_skills()

    assert loop.skill_manager is not before
    prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_skips_rebuild_when_not_ok() -> None:
    loop = _loop()
    before = loop.skill_manager
    with (
        patch.object(
            agent_loop_module,
            "refresh_registry_skills",
            AsyncMock(return_value=RegistrySyncResult(RegistrySyncStatus.FAILED)),
        ),
        patch.object(
            loop, "_refresh_system_prompt_unless_reloaded", AsyncMock()
        ) as prompt,
    ):
        await loop._refresh_registry_skills()

    assert loop.skill_manager is before
    prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_generation_guard_skips_swap() -> None:
    loop = _loop()
    before = loop.skill_manager

    # A concurrent reload bumps the generation while the new manager is built
    # off-thread; the guard must skip the swap and the prompt refresh.
    def _build_and_bump(*_args: object, **_kwargs: object) -> MagicMock:
        loop._reload_generation += 1
        return MagicMock()

    with (
        patch.object(
            agent_loop_module,
            "refresh_registry_skills",
            AsyncMock(return_value=RegistrySyncResult(RegistrySyncStatus.OK)),
        ),
        patch.object(agent_loop_module, "SkillManager", side_effect=_build_and_bump),
        patch.object(
            loop, "_refresh_system_prompt_unless_reloaded", AsyncMock()
        ) as prompt,
    ):
        await loop._refresh_registry_skills()

    assert loop.skill_manager is before
    prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_experiment_hydration_kicks_registry_sync() -> None:
    loop = _loop()
    loop._await_experiment_model = True
    with (
        patch.object(
            agent_loop_module,
            "session_initialize_experiments",
            AsyncMock(return_value=(True, None)),
        ),
        patch.object(loop, "_sync_growthbook_layer_variants", MagicMock()),
        patch.object(loop, "refresh_config", AsyncMock()),
        patch.object(loop, "refresh_system_prompt", AsyncMock()),
        patch.object(loop, "_start_refresh_registry_skills", MagicMock()) as start,
    ):
        await loop.initialize_experiments()

    start.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_retries_past_a_single_racing_reload() -> None:
    loop = _loop()
    before = loop.skill_manager
    builds = 0

    def _bump_once(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal builds
        builds += 1
        if builds == 1:
            loop._reload_generation += 1
        return MagicMock()

    with (
        patch.object(
            agent_loop_module,
            "refresh_registry_skills",
            AsyncMock(return_value=RegistrySyncResult(RegistrySyncStatus.OK)),
        ),
        patch.object(agent_loop_module, "SkillManager", side_effect=_bump_once),
        patch.object(
            loop, "_refresh_system_prompt_unless_reloaded", AsyncMock()
        ) as prompt,
    ):
        await loop._refresh_registry_skills()

    assert loop.skill_manager is not before
    assert builds == 2
    prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_prompt_refresh_applies_when_no_reload_lands() -> None:
    loop = _loop()
    with (
        patch.object(loop, "_build_system_prompt", MagicMock(return_value="fresh")),
        patch.object(loop.messages, "update_system_prompt", MagicMock()) as update,
    ):
        await loop._refresh_system_prompt_unless_reloaded()

    update.assert_called_once_with("fresh")


@pytest.mark.asyncio
async def test_prompt_refresh_discards_prompt_when_reload_lands_mid_build() -> None:
    loop = _loop()

    def _build_and_bump() -> str:
        loop._reload_generation += 1
        return "stale"

    with (
        patch.object(loop, "_build_system_prompt", side_effect=_build_and_bump),
        patch.object(loop.messages, "update_system_prompt", MagicMock()) as update,
    ):
        await loop._refresh_system_prompt_unless_reloaded()

    update.assert_not_called()


@pytest.mark.asyncio
async def test_reload_prepared_before_a_sync_keeps_the_adopted_manager() -> None:
    loop = _loop()
    adopted = MagicMock()

    with (
        patch.object(
            agent_loop_module,
            "refresh_registry_skills",
            AsyncMock(return_value=RegistrySyncResult(RegistrySyncStatus.OK)),
        ),
        patch.object(agent_loop_module, "SkillManager", return_value=adopted),
        patch.object(loop, "_refresh_system_prompt_unless_reloaded", AsyncMock()),
    ):
        stale = loop._prepare_reload(loop.config, False)
        await loop._refresh_registry_skills()

    assert loop.skill_manager is adopted

    loop._commit_reload(stale, reset_middleware=False, switch_to_agent=None)

    assert loop.skill_manager is adopted
    assert loop.tool_manager is stale.tool_manager


@pytest.mark.asyncio
async def test_a_sync_during_prepare_still_keeps_the_adopted_manager() -> None:
    loop = _loop()
    adopted = MagicMock()

    def _adopt_midway(*_args: object, **_kwargs: object) -> MagicMock:
        loop.skill_manager = adopted
        loop._skills_adopted += 1
        return MagicMock()

    with patch.object(agent_loop_module, "SkillManager", side_effect=_adopt_midway):
        stale = loop._prepare_reload(loop.config, False)

    loop._commit_reload(stale, reset_middleware=False, switch_to_agent=None)

    assert loop.skill_manager is adopted
