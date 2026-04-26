from __future__ import annotations

from pathlib import Path
import uuid

import pytest


@pytest.fixture(autouse=True)
def tmp_working_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    tmp_dir = tmp_path / f"test_cwd_{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_dir)
    return tmp_dir


@pytest.fixture(autouse=True)
def config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    tmp_dir = tmp_path / f".vibe_{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", tmp_dir)
    return tmp_dir


@pytest.fixture(autouse=True)
def _reset_trusted_folders_manager(config_dir: Path) -> None:
    from vibe.core.trusted_folders import TrustedFoldersManager

    TrustedFoldersManager.reset()
    yield
    TrustedFoldersManager.reset()