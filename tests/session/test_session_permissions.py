from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.session import session_permissions
from vibe.core.session.session_permissions import (
    restrict_session_log_permissions,
    start_restrict_session_log_permissions,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _make(path: Path, mode: int, *, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


@pytest.fixture
def session_config(tmp_path: Path) -> SessionLoggingConfig:
    return SessionLoggingConfig(
        save_dir=str(tmp_path / "session"), session_prefix="session", enabled=True
    )


@pytest.fixture
def permissive_root(session_config: SessionLoggingConfig) -> Path:
    """A log root holding every artifact kind the sweep must tighten.

    Every mode is set explicitly so the fixture does not depend on the
    ambient umask.
    """
    root = Path(session_config.save_dir)
    root.mkdir()
    root.chmod(0o755)
    session_dir = root / "session_20260101_120000_abcd1234"
    session_dir.mkdir()
    session_dir.chmod(0o755)
    _make(session_dir / "messages.jsonl", 0o644)
    _make(session_dir / "meta.json", 0o644)
    _make(root / "session_20250101_120000_old0.json", 0o644)
    _make(root / ".last_session" / "ttys000", 0o644)
    _make(root / "active" / "abcd1234.lock", 0o644)
    _make(root / "active" / ".registry", 0o644)
    _make(root / "capabilities" / "subagent-profiles" / "explore-d1gest.md", 0o644)
    _make(root / "plugins" / "packages" / "p" / "run.sh", 0o755)
    unified_session = root / "unified" / "5811c85f-5cdc-8467-3c5d-b229c03c16d5"
    unified_session.mkdir(parents=True)
    # A permissive session directory simulates a store regression the sweep
    # must catch even though it skips the large interiors.
    unified_session.chmod(0o755)
    _make(unified_session / "scheduled-loops.json", 0o644)
    _make(unified_session / "tool-results" / "offloaded.json", 0o644)
    (unified_session / "tool-results").chmod(0o755)
    for directory in (
        root / ".last_session",
        root / "active",
        root / "capabilities",
        root / "capabilities" / "subagent-profiles",
        root / "plugins",
        root / "plugins" / "packages",
        root / "plugins" / "packages" / "p",
        root / "unified",
    ):
        directory.chmod(0o755)
    return root


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_start_restrict_session_log_permissions_runs_once_per_process(
    session_config: SessionLoggingConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session-config build starts the sweep per session; only the first wins."""
    started: list[object] = []

    class RecordingThread:
        def __init__(self, *, args: tuple[object, ...], **_unused: object) -> None:
            self._args = args

        def start(self) -> None:
            started.append(self._args[0])

    monkeypatch.setattr(session_permissions, "_STARTED", False)
    monkeypatch.setattr(session_permissions, "Thread", RecordingThread)

    start_restrict_session_log_permissions(session_config)
    start_restrict_session_log_permissions(session_config)

    assert started == [session_config]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
class TestRestrictSessionLogPermissions:
    def test_sweep_tightens_every_artifact_kind(
        self, session_config: SessionLoggingConfig, permissive_root: Path
    ) -> None:
        restrict_session_log_permissions(session_config)

        assert _mode(permissive_root) & 0o077 == 0
        session_dir = permissive_root / "session_20260101_120000_abcd1234"
        assert _mode(session_dir) & 0o077 == 0
        assert _mode(session_dir / "messages.jsonl") & 0o077 == 0
        assert _mode(session_dir / "meta.json") & 0o077 == 0
        assert _mode(permissive_root / "session_20250101_120000_old0.json") & 0o077 == 0
        assert _mode(permissive_root / ".last_session") & 0o077 == 0
        assert _mode(permissive_root / ".last_session" / "ttys000") & 0o077 == 0
        assert _mode(permissive_root / "active" / "abcd1234.lock") & 0o077 == 0
        assert _mode(permissive_root / "active" / ".registry") & 0o077 == 0
        profile = (
            permissive_root / "capabilities" / "subagent-profiles" / "explore-d1gest.md"
        )
        assert _mode(profile) & 0o077 == 0
        script = permissive_root / "plugins" / "packages" / "p" / "run.sh"
        assert _mode(script) & 0o077 == 0
        # The unified session directory is swept (regression check) but its
        # large interior is not: the store creates session directories
        # owner-only, so interior modes cannot grant access.
        unified_session = (
            permissive_root / "unified" / ("5811c85f-5cdc-8467-3c5d-b229c03c16d5")
        )
        assert _mode(permissive_root / "unified") & 0o077 == 0
        assert _mode(unified_session) & 0o077 == 0
        assert _mode(unified_session / "scheduled-loops.json") & 0o077 == 0
        assert _mode(unified_session / "tool-results") == 0o755
        assert _mode(unified_session / "tool-results" / "offloaded.json") == 0o644
        # Nothing else under the root keeps group/other bits.
        still_permissive = {
            path for path in permissive_root.rglob("*") if _mode(path) & 0o077
        }
        assert still_permissive == {
            unified_session / "tool-results",
            unified_session / "tool-results" / "offloaded.json",
        }

    def test_sweep_preserves_owner_execute(
        self, session_config: SessionLoggingConfig, permissive_root: Path
    ) -> None:
        restrict_session_log_permissions(session_config)

        script = permissive_root / "plugins" / "packages" / "p" / "run.sh"
        assert _mode(script) == 0o700

    def test_sweep_is_idempotent(
        self, session_config: SessionLoggingConfig, permissive_root: Path
    ) -> None:
        restrict_session_log_permissions(session_config)

        assert restrict_session_log_permissions(session_config) == 0

    def test_sweep_skips_symlinks(
        self,
        session_config: SessionLoggingConfig,
        permissive_root: Path,
        tmp_path: Path,
    ) -> None:
        target = _make(tmp_path / "outside.txt", 0o644)
        link = permissive_root / "session_20260101_120000_abcd1234" / "link.txt"
        link.symlink_to(target)
        dir_link = permissive_root / "linked_dir"
        dir_link.symlink_to(tmp_path)

        restrict_session_log_permissions(session_config)

        assert link.is_symlink()
        assert _mode(target) == 0o644

    def test_sweep_runs_when_logging_is_disabled(self, permissive_root: Path) -> None:
        """Disabling logging leaves old logs on disk, so they are swept anyway."""
        config = SessionLoggingConfig(
            save_dir=str(permissive_root), session_prefix="session", enabled=False
        )

        restrict_session_log_permissions(config)

        assert _mode(permissive_root) & 0o077 == 0

    def test_sweep_never_creates_the_root(
        self, session_config: SessionLoggingConfig
    ) -> None:
        assert restrict_session_log_permissions(session_config) == 0
        assert not Path(session_config.save_dir).exists()

    def test_sweep_tolerates_chmod_failures(
        self,
        session_config: SessionLoggingConfig,
        permissive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def failing_chmod(path: object, mode: int) -> None:
            raise OSError("permission denied")

        monkeypatch.setattr(session_permissions.os, "chmod", failing_chmod)

        assert restrict_session_log_permissions(session_config) == 0
        assert _mode(permissive_root) == 0o755
