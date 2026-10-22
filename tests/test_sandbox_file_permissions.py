from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

HELPER = (
    Path(__file__).parents[1]
    / "harness/runtimes/python/python/mistralai_vibe_local_harness/vibe/_sandbox_helper.py"
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")


def _write_file(
    path: Path, *, umask: int, content: str = "updated\n", encoding: str = "utf-8"
) -> subprocess.CompletedProcess[str]:
    script = """
import runpy
import sys
from pathlib import Path

helper = runpy.run_path(sys.argv[1])
helper['_atomic_write_text'](Path(sys.argv[2]), sys.argv[3], sys.argv[4])
"""
    # Set the mask only in the child, leaving concurrent sessions unaffected.
    return subprocess.run(
        [sys.executable, "-I", "-c", script, str(HELPER), str(path), content, encoding],
        umask=umask,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    "umask,expected", [(0o022, 0o644), (0o027, 0o640), (0o077, 0o600)]
)
def test_new_file_respects_umask(tmp_path: Path, umask: int, expected: int) -> None:
    path = tmp_path / "created.txt"

    result = _write_file(path, umask=umask)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(path.stat().st_mode) == expected
    assert path.read_text() == "updated\n"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("mode,umask", [(0o644, 0o077), (0o600, 0o022), (0o755, 0o077)])
def test_replacement_preserves_mode(tmp_path: Path, mode: int, umask: int) -> None:
    path = tmp_path / "existing.txt"
    path.write_text("original\n")
    path.chmod(mode)

    result = _write_file(path, umask=umask)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(path.stat().st_mode) == mode
    assert path.read_text() == "updated\n"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("name_length", [240, 255])
def test_long_destination_name(tmp_path: Path, name_length: int) -> None:
    path = tmp_path / ("a" * name_length)

    result = _write_file(path, umask=0o022)

    assert result.returncode == 0, result.stderr
    assert path.read_text() == "updated\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("existing", [False, True])
def test_failed_write_preserves_destination_and_cleans_temporary(
    tmp_path: Path, existing: bool
) -> None:
    path = tmp_path / "destination.txt"
    if existing:
        path.write_text("original\n")
        path.chmod(0o600)
    result = _write_file(path, umask=0o022, content="\u00e9", encoding="ascii")

    assert result.returncode != 0
    assert "UnicodeEncodeError" in result.stderr
    assert list(tmp_path.iterdir()) == ([path] if existing else [])
    if existing:
        assert path.read_text() == "original\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
