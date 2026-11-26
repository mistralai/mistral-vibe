from __future__ import annotations

import os
from pathlib import Path
import time

import pytest

from vibe.app_server._continue_target import (
    POINTERS_DIR,
    STALE_AFTER_SECONDS,
    TerminalSeat,
    continue_target,
)


def _seat(
    tmp_path: Path, terminal_key: str = "ttys003", cwd: str = "/work/a"
) -> TerminalSeat:
    return TerminalSeat(storage_root=tmp_path, terminal_key=terminal_key, cwd=cwd)


@pytest.mark.asyncio
async def test_a_recorded_session_is_read_back_by_the_same_terminal_and_folder(
    tmp_path: Path,
) -> None:
    await _seat(tmp_path).record("session-1")

    assert await _seat(tmp_path).session_id() == "session-1"


@pytest.mark.asyncio
async def test_other_terminals_and_folders_have_their_own_pointer(
    tmp_path: Path,
) -> None:
    await _seat(tmp_path).record("session-1")

    assert await _seat(tmp_path, terminal_key="ttys004").session_id() is None
    assert await _seat(tmp_path, cwd="/work/b").session_id() is None


@pytest.mark.asyncio
async def test_the_pointer_file_name_reveals_neither_the_key_nor_the_folder(
    tmp_path: Path,
) -> None:
    seat = _seat(tmp_path, terminal_key="../../escape", cwd="/work/a")

    await seat.record("session-1")

    (pointer,) = (tmp_path / POINTERS_DIR).iterdir()
    assert pointer == seat.pointer_path
    assert "escape" not in pointer.name
    assert "work" not in pointer.name


@pytest.mark.asyncio
async def test_recording_prunes_pointers_unused_for_thirty_days(tmp_path: Path) -> None:
    stale = _seat(tmp_path, terminal_key="old")
    await stale.record("session-old")
    old = time.time() - STALE_AFTER_SECONDS - 60
    os.utime(stale.pointer_path, (old, old))

    await _seat(tmp_path).record("session-1")

    assert not stale.pointer_path.exists()
    assert await _seat(tmp_path).session_id() == "session-1"


@pytest.mark.asyncio
async def test_an_eligible_pointed_session_wins_over_the_latest(tmp_path: Path) -> None:
    seat = _seat(tmp_path)
    await seat.record("session-1")

    assert await continue_target(seat, {"session-1", "session-2"}, "session-2") == (
        "session-1"
    )


@pytest.mark.asyncio
async def test_an_ineligible_pointed_session_falls_back_to_the_latest(
    tmp_path: Path,
) -> None:
    seat = _seat(tmp_path)
    await seat.record("session-deleted")

    assert await continue_target(seat, {"session-2"}, "session-2") == "session-2"
    assert await continue_target(None, {"session-2"}, "session-2") == "session-2"
    assert await continue_target(_seat(tmp_path, "new"), set(), None) is None
