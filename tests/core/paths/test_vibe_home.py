from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from vibe.core.config import harness_files
from vibe.core.paths import HISTORY_FILE
from vibe.core.paths._vibe_home import (
    bootstrap_vibe_home,
    restrict_vibe_home_permissions,
)
from vibe.utils.paths import GlobalPath


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _patched_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> Path:
    home = tmp_path / name
    monkeypatch.setattr(
        "vibe.core.paths._vibe_home.VIBE_HOME", GlobalPath(lambda: home)
    )
    return home


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
class TestRestrictVibeHomePermissions:
    def test_creates_missing_home_owner_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = _patched_home(tmp_path, monkeypatch, "fresh-home")

        restrict_vibe_home_permissions()

        assert _mode(home) & 0o077 == 0

    def test_tightens_permissive_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = _patched_home(tmp_path, monkeypatch, "permissive-home")
        home.mkdir()
        home.chmod(0o755)

        restrict_vibe_home_permissions()

        assert _mode(home) & 0o077 == 0

    def test_failure_never_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patched_home(tmp_path, monkeypatch, "failing-home")

        def failing_chmod(path: object, mode: int) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr("vibe.core.paths._vibe_home.os.chmod", failing_chmod)

        restrict_vibe_home_permissions()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
class TestBootstrapVibeHome:
    def test_seeds_owner_only_history_greeting(self) -> None:
        bootstrap_vibe_home()

        history_file = HISTORY_FILE.path
        assert history_file.read_text(encoding="utf-8") == "Hello Vibe!\n"
        assert _mode(history_file) & 0o077 == 0

    def test_does_not_create_config_file(self) -> None:
        config_file = harness_files.get_harness_files_manager().user_config_file
        config_file.unlink(missing_ok=True)

        bootstrap_vibe_home()

        # Defaults come from DefaultConfigLayer at merge time; bootstrap no
        # longer seeds a config.toml — it is created on the first persisted
        # change.
        assert not config_file.exists()

    def test_keeps_existing_history_content(self) -> None:
        history_file = HISTORY_FILE.path
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.write_text("existing\n", encoding="utf-8")

        bootstrap_vibe_home()

        assert history_file.read_text(encoding="utf-8") == "existing\n"

    def test_history_failure_never_blocks_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing history write is logged, not fatal, at entrypoint time."""

        def failing_write(*_args: object, **_kwargs: object) -> int:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", failing_write)

        bootstrap_vibe_home()
