from __future__ import annotations

import asyncio
import os
from pathlib import Path
import threading

import pytest

from vibe.utils.io import (
    ATOMIC_WRITE_TEMP,
    _temp_path,
    atomic_replace,
    atomic_write_bytes,
)


def test_generated_temp_name_is_recognised_as_a_leftover(tmp_path: Path) -> None:
    name = _temp_path(tmp_path / ("a" * 255)).name
    assert ATOMIC_WRITE_TEMP.fullmatch(name)


def test_atomic_write_bytes_replaces_content_and_keeps_mode(tmp_path: Path) -> None:
    target = tmp_path / "run.sh"
    target.write_bytes(b"old")
    target.chmod(0o751)
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"
    assert target.stat().st_mode & 0o777 == 0o751
    assert os.listdir(tmp_path) == ["run.sh"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("newline", "expected"),
    [
        (None, f"a{os.linesep}é{os.linesep}".encode("latin-1")),
        ("", "a\né\n".encode("latin-1")),
        ("\r\n", "a\r\né\r\n".encode("latin-1")),
    ],
)
async def test_atomic_replace_encodes_and_translates_newlines(
    tmp_path: Path, newline: str | None, expected: bytes
) -> None:
    target = tmp_path / "a.txt"
    await atomic_replace(target, "a\né\n", encoding="latin-1", newline=newline)
    assert target.read_bytes() == expected


@pytest.mark.asyncio
async def test_cancelled_atomic_replace_never_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"old")
    syncing, release = threading.Event(), threading.Event()
    real_fsync = os.fsync

    def slow_fsync(fd: int) -> None:
        syncing.set()
        release.wait(5)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", slow_fsync)
    task = asyncio.create_task(atomic_replace(target, "new"))
    await asyncio.to_thread(syncing.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    for _ in range(200):
        if os.listdir(tmp_path) == ["a.txt"]:
            break
        await asyncio.sleep(0.01)
    assert target.read_bytes() == b"old"
    assert os.listdir(tmp_path) == ["a.txt"]
