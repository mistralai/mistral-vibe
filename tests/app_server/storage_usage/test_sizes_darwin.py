from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from vibe.app_server._storage_usage.sizes import Budget, SeenFiles
from vibe.app_server._storage_usage.sizes_portable import measure_portable

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="getattrlistbulk is macOS only"
)

_FILE_BYTES = 1_000_000


def _measure_darwin(root: Path):
    from vibe.app_server._storage_usage.sizes_darwin import measure_darwin

    measured = measure_darwin(root, Budget(), SeenFiles())
    assert measured is not None, "this macOS has no bulk reader"
    return measured


def _tree(root: Path) -> Path:
    """A folder with one file of known content and a hard link to it."""
    folder = root / "tree"
    (folder / "nested").mkdir(parents=True)
    original = folder / "nested" / "data.bin"
    original.write_bytes(os.urandom(_FILE_BYTES))
    os.link(original, folder / "link.bin")
    return original


def test_the_bulk_reader_agrees_with_the_portable_walk_on_plain_files(
    tmp_path: Path,
) -> None:
    """*Prepare*: A file of known size and a hard link to it.
    *Do*: Measure the folder with the bulk reader and the portable walk.
    *Assert*: Both count the file once, and agree on its allocated size.
    """
    # Prepare
    _tree(tmp_path)
    folder = tmp_path / "tree"

    # Do
    darwin = _measure_darwin(folder)
    portable = measure_portable(folder, Budget(), SeenFiles())

    # Assert
    assert darwin.complete and portable.complete
    assert darwin.skipped == 0
    assert _FILE_BYTES <= darwin.bytes < 2 * _FILE_BYTES
    assert darwin.bytes == portable.bytes


def test_an_apfs_clone_adds_almost_nothing_to_the_private_size(tmp_path: Path) -> None:
    """*Prepare*: A file, then a copy-on-write clone of it (`cp -c`).
    *Do*: Measure the folder before and after the clone.
    *Assert*: The clone shares its blocks: the private size barely grows.
    """
    # Prepare
    original = _tree(tmp_path)
    folder = tmp_path / "tree"
    before = _measure_darwin(folder)
    clone = subprocess.run(
        ["cp", "-c", str(original), str(folder / "clone.bin")],
        capture_output=True,
        check=False,
    )
    if clone.returncode != 0:
        pytest.skip("this volume cannot clone files (not APFS)")

    # Do
    after = _measure_darwin(folder)

    # Assert
    assert after.private
    assert after.bytes - before.bytes < _FILE_BYTES // 10
