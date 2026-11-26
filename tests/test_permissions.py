from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from vibe.core.tools.utils import resolve_path_permission, with_resolved_path_grants
from vibe.core.workspace import Workspace
from vibe.permissions import path_grant_pattern_matches, path_pattern_matches


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("/var/tmp/secret", "tmp/*", False),
        ("/app/docs/keys", "docs/*", False),
        ("/foo/README.md", "README*", False),
        ("/foo/bar", "bar", False),
        ("/workdir/README.md", "*/README*", True),
        ("/tmp/pytest-0/test_x/README.md", "*/README*", True),
        ("/tmp/secret", "/tmp/*", True),
        ("/tmp/a/secret", "/tmp/*", False),
        ("/foo/bar", "*", True),
        (r"C:\var\tmp\secret", r"tmp\*", False),
        (r"C:\tmp\secret", r"C:\tmp\*", True),
        (r"C:\tmp\a\secret", r"C:\tmp\*", False),
    ],
)
def test_path_pattern_matches_legacy_globs(
    path: str, pattern: str, expected: bool
) -> None:
    assert path_pattern_matches(path, pattern) is expected


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("/etc/passwd", "*", False),
        ("/etc/passwd", "cat *", False),
        ("/etc/passwd", "npm *", False),
        ("/tmp/secret", "/tmp/*", True),
        ("/tmp/a/secret", "/tmp/*", False),
        ("/etc/passwd", "vibe-path:exact:/etc/passwd", True),
        ("/etc/passwd", "vibe-path:directory_recursive:/etc", True),
        ("/etc/passwd", "cat", False),
        (r"C:\tmp\secret", r"C:\tmp\*", True),
        (r"C:\Windows\passwd", "*", False),
    ],
)
def test_path_grant_pattern_matches_ignores_command_globs(
    path: str, pattern: str, expected: bool
) -> None:
    assert path_grant_pattern_matches(path, pattern) is expected


def _workspace_beside(tmp_path: Path) -> Workspace:
    """A workspace in ``tmp_path/work``, so the links the tests make lie outside it."""
    return Workspace.for_session(tmp_path / "work")


def _symlinked_dir(tmp_path: Path) -> tuple[Path, Path]:
    real = tmp_path / "real"
    (real / "nested").mkdir(parents=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("this host cannot create symlinks")
    return real, link


@pytest.mark.parametrize("scope", ["directory_recursive", "exact"])
def test_a_path_grant_written_through_a_symlink_covers_the_resolved_path(
    tmp_path: Path, scope: str
) -> None:
    _, link = _symlinked_dir(tmp_path)
    target = link / "nested" / "a.txt"
    granted = target if scope == "exact" else link / "nested"

    result = resolve_path_permission(
        str(target),
        workspace=_workspace_beside(tmp_path),
        allowlist=[f"vibe-path:{scope}:{granted}"],
        denylist=[],
    )

    assert result is not None
    assert result.permission == "always"


@pytest.mark.skipif(os.name == "nt", reason="POSIX grant paths")
@pytest.mark.parametrize("platform", ["darwin", "linux"])
@pytest.mark.parametrize(
    ("grant", "on_macos"),
    [
        (
            "vibe-path:directory_recursive:/tmp",
            "vibe-path:directory_recursive:/private/tmp",
        ),
        ("/tmp/*", "/private/tmp/*"),
    ],
)
def test_a_grant_naming_tmp_follows_it_only_to_macos_private_tmp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    grant: str,
    on_macos: str,
    platform: str,
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    followed = [on_macos] if platform == "darwin" else []

    assert with_resolved_path_grants([grant], _workspace_beside(tmp_path)) == [
        grant,
        *followed,
    ]


@pytest.mark.parametrize("denied", ["nested/a.txt", "nested/*.txt"])
def test_a_denylist_entry_written_through_a_symlink_outranks_a_resolved_grant(
    tmp_path: Path, denied: str
) -> None:
    _, link = _symlinked_dir(tmp_path)

    result = resolve_path_permission(
        str(link / "nested" / "a.txt"),
        workspace=_workspace_beside(tmp_path),
        allowlist=[f"vibe-path:directory_recursive:{link / 'nested'}"],
        denylist=[f"{link}/{denied}"],
    )

    assert result is not None
    assert result.permission == "never"


def test_a_path_grant_through_a_link_inside_the_workspace_does_not_follow_it(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    try:
        (work / "link").symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("this host cannot create symlinks")

    result = resolve_path_permission(
        str(real / "sub" / "a.txt"),
        workspace=Workspace.for_session(work),
        allowlist=[f"vibe-path:directory_recursive:{work / 'link' / 'sub'}"],
        denylist=[],
    )

    assert result is None


def test_a_legacy_glob_written_through_a_symlink_covers_the_resolved_path(
    tmp_path: Path,
) -> None:
    _, link = _symlinked_dir(tmp_path)

    result = resolve_path_permission(
        str(link / "nested" / "a.txt"),
        workspace=_workspace_beside(tmp_path),
        allowlist=[f"{link}/nested/*"],
        denylist=[],
    )

    assert result is not None
    assert result.permission == "always"


def test_a_legacy_glob_through_a_link_inside_the_workspace_does_not_follow_it(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    try:
        (work / "link").symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("this host cannot create symlinks")

    result = resolve_path_permission(
        str(real / "sub" / "a.txt"),
        workspace=Workspace.for_session(work),
        allowlist=[f"{work}/link/sub/*"],
        denylist=[],
    )

    assert result is None


def test_a_path_grant_naming_a_user_link_does_not_follow_it(tmp_path: Path) -> None:
    real, link = _symlinked_dir(tmp_path)

    result = resolve_path_permission(
        str(real / "nested" / "a.txt"),
        workspace=_workspace_beside(tmp_path),
        allowlist=[f"vibe-path:directory_recursive:{link}"],
        denylist=[],
    )

    assert result is None


def test_a_path_grant_through_a_symlink_does_not_cover_its_siblings(
    tmp_path: Path,
) -> None:
    _, link = _symlinked_dir(tmp_path)
    sibling = tmp_path / "sibling"
    sibling.mkdir()

    result = resolve_path_permission(
        str(sibling / "a.txt"),
        workspace=_workspace_beside(tmp_path),
        allowlist=[f"vibe-path:directory_recursive:{link}"],
        denylist=[],
    )

    assert result is None


def test_relative_allowlist_does_not_authorize_path_suffix(tmp_path: Path) -> None:
    result = resolve_path_permission(
        "/var/tmp/secret",
        workspace=Workspace.for_session(tmp_path),
        allowlist=["tmp/*"],
        denylist=[],
    )

    assert result is None
