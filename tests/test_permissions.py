from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.tools.utils import resolve_path_permission
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


def test_relative_allowlist_does_not_authorize_path_suffix(tmp_path: Path) -> None:
    result = resolve_path_permission(
        "/var/tmp/secret", cwd=tmp_path, allowlist=["tmp/*"], denylist=[]
    )

    assert result is None
