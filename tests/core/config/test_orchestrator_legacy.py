from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
import tomllib

import pytest

from vibe.core.config import AnyVibeConfig
from vibe.core.config._settings import VibeConfig
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator_legacy import (
    LegacyConfigOrchestrator,
    _pointer_to_nested_update,
    _set_pointer_in_place,
    load_config_orchestrator,
)
from vibe.core.config.orchestrator_port import ConfigOrchestratorPort

OrchestratorFactory = Callable[[], Awaitable[ConfigOrchestratorPort[AnyVibeConfig]]]


@pytest.fixture(params=["legacy", "default"])
def make_orchestrator(request: pytest.FixtureRequest) -> OrchestratorFactory:
    async def _legacy() -> ConfigOrchestratorPort[AnyVibeConfig]:
        return LegacyConfigOrchestrator(VibeConfig.load())

    async def _default() -> ConfigOrchestratorPort[AnyVibeConfig]:
        return await build_default_orchestrator()

    return _legacy if request.param == "legacy" else _default


@pytest.mark.asyncio
async def test_set_field_persists_and_survives_reload(
    make_orchestrator: OrchestratorFactory,
) -> None:
    orchestrator = await make_orchestrator()

    result = await orchestrator.set_field("/displayed_workdir", "persisted")
    await orchestrator.reload()

    assert result == []
    assert orchestrator.config.displayed_workdir == "persisted"


@pytest.mark.asyncio
async def test_set_field_nested_pointer_persists_and_survives_reload(
    make_orchestrator: OrchestratorFactory,
) -> None:
    orchestrator = await make_orchestrator()

    await orchestrator.set_field("/experiment_overrides", {})
    result = await orchestrator.set_field("/experiment_overrides/flag", "on")
    await orchestrator.reload()

    assert result == []
    assert orchestrator.config.experiment_overrides == {"flag": "on"}


@pytest.mark.asyncio
async def test_reload_reflects_external_disk_changes(
    make_orchestrator: OrchestratorFactory,
) -> None:
    orchestrator = await make_orchestrator()
    assert orchestrator.config.displayed_workdir == ""

    VibeConfig.save_updates({"displayed_workdir": "from-disk"})
    await orchestrator.reload()

    assert orchestrator.config.displayed_workdir == "from-disk"


# --- Legacy-specific behaviour ---


@pytest.mark.asyncio
async def test_set_field_overrides_layer_mutates_in_memory_without_persisting(
    config_dir: Path,
) -> None:
    orchestrator = LegacyConfigOrchestrator(VibeConfig.load())

    result = await orchestrator.set_field(
        "/displayed_workdir", "in-memory", target_layer=OverridesLayer.NAME
    )

    assert result == []
    assert orchestrator.config.displayed_workdir == "in-memory"
    with (config_dir / "config.toml").open("rb") as file:
        assert "displayed_workdir" not in tomllib.load(file)


@pytest.mark.asyncio
async def test_set_field_persist_path_writes_to_config_file(config_dir: Path) -> None:
    orchestrator = LegacyConfigOrchestrator(VibeConfig.load())

    result = await orchestrator.set_field("/displayed_workdir", "persisted")

    assert result == []
    with (config_dir / "config.toml").open("rb") as file:
        assert tomllib.load(file)["displayed_workdir"] == "persisted"


@pytest.mark.asyncio
async def test_load_config_orchestrator_returns_legacy_when_flag_disabled(
    config_dir: Path,
) -> None:
    orchestrator = await load_config_orchestrator()

    assert isinstance(orchestrator, LegacyConfigOrchestrator)


@pytest.mark.asyncio
async def test_load_config_orchestrator_builds_default_when_flag_enabled(
    config_dir: Path,
) -> None:
    VibeConfig.save_updates({"enable_config_orchestrator": True})

    orchestrator = await load_config_orchestrator()

    expected = await build_default_orchestrator()
    assert isinstance(orchestrator, type(expected))
    assert not isinstance(orchestrator, LegacyConfigOrchestrator)


# --- JSON Pointer helpers ---


class TestPointerToNestedUpdate:
    @pytest.mark.parametrize(
        ("path", "value", "expected"),
        [
            ("/displayed_workdir", "x", {"displayed_workdir": "x"}),
            (
                "/tools/bash/allowlist",
                ["ls"],
                {"tools": {"bash": {"allowlist": ["ls"]}}},
            ),
            (
                "/experiment_overrides/flag",
                "on",
                {"experiment_overrides": {"flag": "on"}},
            ),
        ],
    )
    def test_builds_nested_dict(
        self, path: str, value: object, expected: dict[str, object]
    ) -> None:
        assert _pointer_to_nested_update(path, value) == expected


class TestSetPointerInPlace:
    def test_top_level_attribute(self) -> None:
        class Root:
            displayed_workdir = ""

        root = Root()
        _set_pointer_in_place(root, "/displayed_workdir", "here")

        assert root.displayed_workdir == "here"

    def test_nested_through_dict(self) -> None:
        class Root:
            def __init__(self) -> None:
                self.tools: dict[str, dict[str, object]] = {"bash": {}}

        root = Root()
        _set_pointer_in_place(root, "/tools/bash/allowlist", ["ls"])

        assert root.tools == {"bash": {"allowlist": ["ls"]}}

    def test_raises_on_missing_attribute(self) -> None:
        class Root:
            __slots__ = ()

        with pytest.raises(AttributeError):
            _set_pointer_in_place(Root(), "/nope", 1)
