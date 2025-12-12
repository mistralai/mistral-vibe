from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config_path import CONFIG_FILE, GLOBAL_CONFIG_FILE, VIBE_HOME


class TestResolveConfigFile:
    def test_resolves_local_config_when_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that local .vibe/config.toml is found when it exists."""
        monkeypatch.chdir(tmp_path)
        local_config_dir = tmp_path / ".vibe"
        local_config_dir.mkdir()
        local_config = local_config_dir / "config.toml"
        local_config.write_text('active_model = "test"', encoding="utf-8")

        assert CONFIG_FILE.path == local_config
        assert CONFIG_FILE.path.is_file()
        assert CONFIG_FILE.path.read_text(encoding="utf-8") == 'active_model = "test"'

    def test_falls_back_to_global_config_when_local_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that global config is returned when local config doesn't exist."""
        monkeypatch.chdir(tmp_path)
        # Ensure no local config exists
        assert not (tmp_path / ".vibe" / "config.toml").exists()

        assert CONFIG_FILE.path == GLOBAL_CONFIG_FILE.path

    def test_respects_vibe_home_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that VIBE_HOME environment variable affects VIBE_HOME.path."""
        assert VIBE_HOME.path != tmp_path
        monkeypatch.setenv("VIBE_HOME", str(tmp_path))
        assert VIBE_HOME.path == tmp_path
