from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from vibe.utils import file_lock


@pytest.mark.parametrize("initial", [b"", b"\0"])
def test_windows_lock_locks_byte_zero_of_a_one_byte_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, initial: bytes
) -> None:
    calls: list[tuple[int, int, int]] = []

    def locking(descriptor: int, mode: int, length: int) -> None:
        calls.append((mode, length, os.lseek(descriptor, 0, os.SEEK_CUR)))

    fake_msvcrt = SimpleNamespace(LK_LOCK=1, LK_UNLCK=0, locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(file_lock.os, "name", "nt")
    path = tmp_path / "store.lock"
    path.write_bytes(initial)

    with path.open("a+b") as file:
        file_lock._acquire(file)
        file_lock._release(file)

    assert path.read_bytes() == b"\0"
    assert calls == [(fake_msvcrt.LK_LOCK, 1, 0), (fake_msvcrt.LK_UNLCK, 1, 0)]
