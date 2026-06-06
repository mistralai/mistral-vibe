from __future__ import annotations

from pathlib import Path

import pytest

from tests.snapshots.snap_compare import SnapCompare
from vibe.setup.trusted_folders.trust_folder_dialog import TrustFolderApp


@pytest.fixture(autouse=True)
def _pin_vibe_home(monkeypatch: pytest.MonkeyPatch) -> None:
    # The dialog renders TRUSTED_FOLDERS_FILE.path in its footer. Pin the
    # default so the path is stable across runs. Setting the VIBE_HOME env
    # var is not enough: _get_vibe_home() calls Path.resolve(), which on
    # macOS rewrites /home/user via the /System/Volumes/Data firmlink.
    monkeypatch.delenv("VIBE_HOME", raising=False)
    monkeypatch.setattr(
        "vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", Path("/home/user/.vibe")
    )


_DIALOG_CSS = str(
    Path(__file__).parents[2]
    / "vibe"
    / "setup"
    / "trusted_folders"
    / "trust_folder_dialog.tcss"
)


class TrustFolderDialogSnapshotApp(TrustFolderApp):
    """cwd is itself the trust target (two-option dialog)."""

    CSS_PATH = _DIALOG_CSS

    def __init__(self) -> None:
        super().__init__(
            cwd=Path("/home/user/projects/my-project"),
            repo_root=None,
            detected_files=["AGENTS.md", ".vibe/"],
        )


class TrustFolderDialogWithRepoSnapshotApp(TrustFolderApp):
    """cwd inside a git repo (three-option dialog)."""

    CSS_PATH = _DIALOG_CSS

    def __init__(self) -> None:
        super().__init__(
            cwd=Path("/home/user/projects/my-project/src/pkg"),
            repo_root=Path("/home/user/projects/my-project"),
            detected_files=["AGENTS.md"],
            repo_detected_files=[".vibe/", "src/AGENTS.md"],
            offer_repo_trust=True,
        )


class TrustFolderDialogUntrustedRepoSnapshotApp(TrustFolderApp):
    """cwd inside a git repo that was previously marked untrusted."""

    CSS_PATH = _DIALOG_CSS

    def __init__(self) -> None:
        super().__init__(
            cwd=Path("/home/user/projects/my-project/src/pkg"),
            repo_root=Path("/home/user/projects/my-project"),
            offer_repo_trust=False,
            repo_explicitly_untrusted=True,
            detected_files=["AGENTS.md"],
        )


class TrustFolderDialogManyFilesSnapshotApp(TrustFolderApp):
    CSS_PATH = _DIALOG_CSS

    def __init__(self) -> None:
        detected = [f"sub{i}/AGENTS.md" for i in range(20)] + [".vibe/", ".agents/"]
        super().__init__(
            cwd=Path("/home/user/projects/my-project"),
            repo_root=None,
            detected_files=detected,
        )


def test_snapshot_trust_folder_dialog(snap_compare: SnapCompare) -> None:
    assert snap_compare(
        "test_ui_snapshot_trust_folder_dialog.py:TrustFolderDialogSnapshotApp",
        terminal_size=(80, 40),
    )


def test_snapshot_trust_folder_dialog_with_repo(snap_compare: SnapCompare) -> None:
    assert snap_compare(
        "test_ui_snapshot_trust_folder_dialog.py:TrustFolderDialogWithRepoSnapshotApp",
        terminal_size=(80, 40),
    )


def test_snapshot_trust_folder_dialog_untrusted_repo(snap_compare: SnapCompare) -> None:
    assert snap_compare(
        "test_ui_snapshot_trust_folder_dialog.py:TrustFolderDialogUntrustedRepoSnapshotApp",
        terminal_size=(80, 40),
    )


def test_snapshot_trust_folder_dialog_small_terminal(snap_compare: SnapCompare) -> None:
    assert snap_compare(
        "test_ui_snapshot_trust_folder_dialog.py:TrustFolderDialogSnapshotApp",
        terminal_size=(80, 24),
    )


def test_snapshot_trust_folder_dialog_many_files(snap_compare: SnapCompare) -> None:
    assert snap_compare(
        "test_ui_snapshot_trust_folder_dialog.py:TrustFolderDialogManyFilesSnapshotApp",
        terminal_size=(80, 40),
    )
