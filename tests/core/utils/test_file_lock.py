from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import vibe.core.utils.file_lock as file_lock_module
from vibe.core.utils.file_lock import async_file_lock


@pytest.mark.parametrize(("blocking", "expected_mode"), [(False, 2), (True, 1)])
def test_windows_locking_uses_a_one_byte_region(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, blocking: bool, expected_mode: int
) -> None:
    calls: list[tuple[int, int]] = []
    fake_msvcrt = SimpleNamespace(
        LK_LOCK=1,
        LK_NBLCK=2,
        LK_UNLCK=0,
        locking=lambda _descriptor, mode, length: calls.append((mode, length)),
    )
    monkeypatch.setattr(file_lock_module, "_is_windows", lambda: True)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    path = tmp_path / "lease.lock"

    with path.open("w+b") as file:
        file_lock_module.acquire_file_lock(file, blocking=blocking)
        file_lock_module.release_file_lock(file)

    assert calls == [(expected_mode, 1), (fake_msvcrt.LK_UNLCK, 1)]


@pytest.mark.asyncio
async def test_async_file_lock_times_out_while_another_holder_keeps_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "config.lock"

    async with async_file_lock(path, timeout=1):
        with pytest.raises(TimeoutError):
            async with async_file_lock(path, timeout=0.05):
                pass

    async with async_file_lock(path, timeout=0.05):
        pass


@pytest.mark.asyncio
async def test_async_file_lock_serializes_holders(tmp_path: Path) -> None:
    path = tmp_path / "config.lock"
    order: list[str] = []

    async def hold(name: str) -> None:
        async with async_file_lock(path, timeout=5):
            order.append(f"{name} in")
            await asyncio.sleep(0.02)
            order.append(f"{name} out")

    await asyncio.gather(hold("a"), hold("b"))

    assert order in (
        ["a in", "a out", "b in", "b out"],
        ["b in", "b out", "a in", "a out"],
    )
