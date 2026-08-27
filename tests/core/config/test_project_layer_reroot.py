from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest
import tomli_w

from vibe.core.config import build_default_orchestrator
from vibe.core.config.layers import project as project_layer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.trusted_folders import trusted_folders_manager


def _project_config(root: Path, extra: dict[str, object]) -> None:
    vibe_dir = root / ".vibe"
    vibe_dir.mkdir(parents=True, exist_ok=True)
    with (vibe_dir / "config.toml").open("wb") as f:
        tomli_w.dump(extra, f)


async def _reroot(orchestrator, root: Path) -> None:
    """What a move does to the config layer."""
    layer = orchestrator.get_layer(ProjectConfigLayer.NAME)
    assert isinstance(layer, ProjectConfigLayer)
    await layer.reroot(root)


@pytest.mark.asyncio
async def test_re_rooting_moves_which_project_config_is_read(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    # The layer is rooted once when the orchestrator is built, so without this
    # a moved session keeps reading the config of the directory it left.
    destination = tmp_working_directory / "worktree"
    destination.mkdir()
    _project_config(tmp_working_directory, {"theme": "origin-theme"})
    _project_config(destination, {"theme": "destination-theme"})
    trusted_folders_manager.add_trusted(tmp_working_directory / ".vibe")
    trusted_folders_manager.add_trusted(destination / ".vibe")

    orchestrator = await build_default_orchestrator()
    assert orchestrator.config.theme == "origin-theme"

    await _reroot(orchestrator, destination)
    await orchestrator.reload()

    assert orchestrator.config.theme == "destination-theme"


@pytest.mark.asyncio
async def test_re_rooting_moves_where_project_config_is_written(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    # The half that matters most. A worktree session persisting project config
    # must not reach back into the checkout it came from.
    destination = tmp_working_directory / "worktree"
    destination.mkdir()
    _project_config(tmp_working_directory, {"theme": "origin-theme"})
    _project_config(destination, {"theme": "destination-theme"})
    trusted_folders_manager.add_trusted(tmp_working_directory / ".vibe")
    trusted_folders_manager.add_trusted(destination / ".vibe")

    orchestrator = await build_default_orchestrator()
    await _reroot(orchestrator, destination)
    await orchestrator.reload()

    layer = orchestrator.get_layer(ProjectConfigLayer.NAME)
    assert isinstance(layer, ProjectConfigLayer)
    assert layer.config_file_path == destination / ".vibe" / "config.toml"
    # The layer has to stay the same object. The orchestrator's default-layer
    # resolver closes over the one built at startup, so swapping in a
    # replacement leaves it resolving a layer that is no longer in the stack
    # and every implicit write fails.
    assert orchestrator.writable_layer_name == ProjectConfigLayer.NAME


@pytest.mark.asyncio
async def test_an_untrusted_destination_contributes_nothing(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    # Why the move grants session trust rather than only re-rooting: an
    # untrusted layer is inert, so re-rooting alone would swap the origin's
    # config for no project config at all.
    destination = tmp_working_directory / "worktree"
    destination.mkdir()
    _project_config(tmp_working_directory, {"theme": "origin-theme"})
    _project_config(destination, {"theme": "destination-theme"})
    trusted_folders_manager.add_trusted(tmp_working_directory / ".vibe")

    orchestrator = await build_default_orchestrator()
    await _reroot(orchestrator, destination)
    await orchestrator.reload()

    assert orchestrator.config.theme != "destination-theme"

    trusted_folders_manager.trust_for_session(destination / ".vibe")
    await _reroot(orchestrator, destination)
    await orchestrator.reload()

    assert orchestrator.config.theme == "destination-theme"


@pytest.mark.asyncio
async def test_a_find_already_running_cannot_undo_the_move(
    config_dir: Path, tmp_working_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Discovery holds its lock across a thread hop. A find that started before
    # the move lands after it, and left to write its answer it would restore
    # the departure directory and mark discovery done, so the move's own find
    # would return early and the session would keep reading the project it
    # left. That is the isolation failing in the direction re-rooting exists to
    # prevent, so the reset takes the same lock.
    destination = tmp_working_directory / "worktree"
    destination.mkdir()
    _project_config(tmp_working_directory, {"theme": "origin-theme"})
    _project_config(destination, {"theme": "destination-theme"})
    trusted_folders_manager.trust_for_session(tmp_working_directory / ".vibe")
    trusted_folders_manager.trust_for_session(destination / ".vibe")

    layer = ProjectConfigLayer(path=tmp_working_directory)
    discover = project_layer._discover_config_file
    entered = threading.Event()
    release = threading.Event()

    def stall(root: Path, stop: Path) -> Path | None:
        entered.set()
        release.wait(5)
        return discover(root, stop)

    monkeypatch.setattr(project_layer, "_discover_config_file", stall)
    in_flight = asyncio.create_task(layer._find_config_file())
    await asyncio.to_thread(entered.wait, 5)

    monkeypatch.setattr(project_layer, "_discover_config_file", discover)
    moved = asyncio.create_task(layer.reroot(destination))
    # Long enough for the move to reach the lock, so the test describes a
    # reset racing a live find rather than one arriving after it.
    await asyncio.sleep(0)

    release.set()
    await in_flight
    await moved

    assert layer.config_file_path == destination / ".vibe" / "config.toml"
