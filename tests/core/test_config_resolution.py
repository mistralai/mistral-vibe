from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.config import ModelConfig
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.paths import VIBE_HOME
from vibe.core.trusted_folders import trusted_folders_manager


class TestResolveConfigFile:
    def test_resolves_local_config_when_exists_and_folder_is_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        local_config_dir = tmp_path / ".vibe"
        local_config_dir.mkdir()
        local_config = local_config_dir / "config.toml"
        local_config.write_text('active_model = "test"', encoding="utf-8")

        monkeypatch.setattr(trusted_folders_manager, "is_trusted", lambda _: True)

        reset_harness_files_manager()
        init_harness_files_manager("user", "project")
        from vibe.core.config.harness_files import get_harness_files_manager

        mgr = get_harness_files_manager()
        resolved = mgr.config_file
        assert resolved is not None
        assert resolved == local_config
        assert resolved.is_file()
        assert resolved.read_text(encoding="utf-8") == 'active_model = "test"'

    def test_resolves_global_config_when_folder_is_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        local_config_dir = tmp_path / ".vibe"
        local_config_dir.mkdir()
        local_config = local_config_dir / "config.toml"
        local_config.write_text('active_model = "test"', encoding="utf-8")

        reset_harness_files_manager()
        init_harness_files_manager("user", "project")
        from vibe.core.config.harness_files import get_harness_files_manager

        mgr = get_harness_files_manager()
        assert mgr.config_file == VIBE_HOME.path / "config.toml"

    def test_falls_back_to_global_config_when_local_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        # Ensure no local config exists
        assert not (tmp_path / ".vibe" / "config.toml").exists()

        reset_harness_files_manager()
        init_harness_files_manager("user", "project")
        from vibe.core.config.harness_files import get_harness_files_manager

        mgr = get_harness_files_manager()
        assert mgr.config_file == VIBE_HOME.path / "config.toml"

    def test_respects_vibe_home_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert VIBE_HOME.path != tmp_path
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        assert VIBE_HOME.path == tmp_path

    def test_returns_none_when_no_sources(self) -> None:
        mgr = HarnessFilesManager(sources=())
        assert mgr.config_file is None

    def test_user_only_returns_global_config(self) -> None:
        mgr = HarnessFilesManager(sources=("user",))
        assert mgr.config_file == VIBE_HOME.path / "config.toml"


class TestAutoCompactThresholdFallback:
    def test_model_without_explicit_threshold_inherits_global(self) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")
        cfg = build_test_vibe_config(
            auto_compact_threshold=42_000, models=[model], active_model="m"
        )
        assert cfg.get_active_model().auto_compact_threshold == 42_000

    def test_model_with_explicit_threshold_keeps_own_value(self) -> None:
        model = ModelConfig(
            name="m", provider="p", alias="m", auto_compact_threshold=99_000
        )
        cfg = build_test_vibe_config(
            auto_compact_threshold=42_000, models=[model], active_model="m"
        )
        assert cfg.get_active_model().auto_compact_threshold == 99_000

    def test_default_global_threshold_used_when_nothing_set(self) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")
        cfg = build_test_vibe_config(models=[model], active_model="m")
        assert cfg.get_active_model().auto_compact_threshold == 200_000

    def test_changed_global_threshold_propagates_on_reload(self) -> None:
        model = ModelConfig(name="m", provider="p", alias="m")

        cfg1 = build_test_vibe_config(
            auto_compact_threshold=50_000, models=[model], active_model="m"
        )
        assert cfg1.get_active_model().auto_compact_threshold == 50_000

        # Simulate config reload with a different global threshold
        cfg2 = build_test_vibe_config(
            auto_compact_threshold=75_000, models=[model], active_model="m"
        )
        assert cfg2.get_active_model().auto_compact_threshold == 75_000
