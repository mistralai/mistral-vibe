from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path

import pytest

from tests.app_server.storage_usage._tree import write
from vibe.app_server._storage_usage.sizes import Budget, SeenFiles, measure_excluding
from vibe.app_server._storage_usage.sizes_portable import measure_portable

_JUNCTION = "junction"


class _Entry:
    """A directory entry that reports itself a junction when named so, as a
    Windows directory junction does; everything else is the real entry's.
    """

    def __init__(self, entry: os.DirEntry[str]) -> None:
        self._entry = entry

    def __getattr__(self, name: str) -> object:
        return getattr(self._entry, name)

    def is_junction(self) -> bool:
        return self._entry.name == _JUNCTION


class _Listing:
    """``os.scandir``'s result, used as an iterator or a context manager."""

    def __init__(self, path: str | Path) -> None:
        with _real_scandir(path) as entries:
            self._entries = [_Entry(entry) for entry in entries]

    def __iter__(self) -> Iterator[_Entry]:
        return iter(self._entries)

    def __enter__(self) -> _Listing:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


_real_scandir = os.scandir


@pytest.fixture
def junctions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "scandir", _Listing)


@pytest.mark.usefixtures("junctions")
def test_the_portable_walk_never_enters_a_junction(tmp_path: Path) -> None:
    """*Prepare*: A folder holding a file and a directory junction to a big
    folder (``is_dir`` true, as on Windows).
    *Do*: Measure it with the portable walk.
    *Assert*: The file counts, the junction's target does not.
    """
    # Prepare
    write(tmp_path / "small.bin", 10_000)
    write(tmp_path / _JUNCTION / "big.bin", 400_000)

    # Do
    measured = measure_portable(tmp_path, Budget(), SeenFiles())

    # Assert
    assert 10_000 <= measured.bytes < 400_000


@pytest.mark.usefixtures("junctions")
def test_measuring_around_excluded_folders_never_enters_a_junction(
    tmp_path: Path,
) -> None:
    """*Prepare*: A folder with an excluded subfolder, a file and a directory
    junction to a big folder beside it.
    *Do*: Measure it without the excluded subfolder.
    *Assert*: The file counts, the junction's target does not.
    """
    # Prepare
    write(tmp_path / "excluded" / "owned.bin", 10)
    write(tmp_path / "small.bin", 10_000)
    write(tmp_path / _JUNCTION / "big.bin", 400_000)

    # Do
    measured = measure_excluding(
        tmp_path, frozenset({tmp_path / "excluded"}), Budget(), SeenFiles()
    )

    # Assert
    assert 10_000 <= measured.bytes < 400_000
