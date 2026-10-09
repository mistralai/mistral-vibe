from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest

from mistralai_vibe_local_harness.vibe._storage import SessionLease
from vibe.app_server import _session_lease_watch
from vibe.app_server._session_lease_watch import (
    LeaseHolder,
    read_lease_holder,
    watch_session_leases,
)

SESSION_ID = "11111111-2222-3333-4444-555555555555"
OTHER_SESSION_ID = "66666666-7777-8888-9999-000000000000"
HOLD_LEASE = """
import sys
from pathlib import Path
from mistralai_vibe_local_harness.vibe._storage import SessionLease

lease = SessionLease(Path(sys.argv[1]), sys.argv[2]).acquire()
print("held", flush=True)
sys.stdin.read()
"""
CAN_AWAIT_PROCESS_EXIT = sys.platform in {"linux", "darwin"}
pytestmark = pytest.mark.usefixtures("host_platform")


async def _hold_lease_in_another_process(
    root: Path, session_id: str = SESSION_ID
) -> asyncio.subprocess.Process:
    holder = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        HOLD_LEASE,
        str(root),
        session_id,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    assert holder.stdout is not None
    assert await holder.stdout.readline() == b"held\n"
    return holder


@pytest.mark.asyncio
async def test_reports_held_leases_then_each_change(tmp_path: Path) -> None:
    holder = await _hold_lease_in_another_process(tmp_path)
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {SESSION_ID: LeaseHolder(holder.pid)}

        lease = SessionLease(tmp_path, OTHER_SESSION_ID).acquire()
        try:
            assert await asyncio.wait_for(anext(updates), 5) == {
                OTHER_SESSION_ID: LeaseHolder(os.getpid())
            }
        finally:
            lease.release()
        assert await asyncio.wait_for(anext(updates), 5) == {OTHER_SESSION_ID: None}

        assert holder.stdin is not None
        holder.stdin.close()
        await holder.wait()
        assert await asyncio.wait_for(anext(updates), 5) == {SESSION_ID: None}
    finally:
        await updates.aclose()
        if holder.returncode is None:
            holder.kill()
            await holder.wait()


@pytest.mark.asyncio
async def test_ignores_stale_and_foreign_files(tmp_path: Path) -> None:
    lease_dir = tmp_path / "active"
    lease_dir.mkdir()
    (lease_dir / f"{SESSION_ID}.lock").touch()
    (lease_dir / "not-a-session.lock").touch()
    (lease_dir / ".registry").touch()
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {}
    finally:
        await updates.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(not CAN_AWAIT_PROCESS_EXIT, reason="no process exit notifications")
async def test_reports_a_holder_killed_without_releasing(tmp_path: Path) -> None:
    holder = await _hold_lease_in_another_process(tmp_path)
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {SESSION_ID: LeaseHolder(holder.pid)}

        holder.kill()
        await holder.wait()

        assert await asyncio.wait_for(anext(updates), 5) == {SESSION_ID: None}
        assert (tmp_path / "active" / f"{SESSION_ID}.lock").exists()
    finally:
        await updates.aclose()


@pytest.mark.asyncio
async def test_rechecks_a_holder_whose_process_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_session_lease_watch, "_HELD_LEASE_RECHECK_S", 0.2)
    holder = await _hold_lease_in_another_process(tmp_path)
    diagnostic = tmp_path / "active" / f"{SESSION_ID}.lock.json"
    diagnostic.write_text("not json", encoding="utf-8")
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {SESSION_ID: LeaseHolder(None)}

        holder.kill()
        await holder.wait()

        assert await asyncio.wait_for(anext(updates), 5) == {SESSION_ID: None}
    finally:
        await updates.aclose()


@pytest.mark.asyncio
async def test_reads_which_process_holds_a_lease(tmp_path: Path) -> None:
    free = read_lease_holder(tmp_path, SESSION_ID)
    holder = await _hold_lease_in_another_process(tmp_path)
    try:
        held = read_lease_holder(tmp_path, SESSION_ID)
    finally:
        holder.kill()
        await holder.wait()

    assert free is None
    assert held == LeaseHolder(holder.pid)


@pytest.mark.asyncio
async def test_reports_a_lease_taken_just_after_a_slow_watcher_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    awatch = _session_lease_watch.watchfiles.awatch

    async def slow_awatch(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        await asyncio.sleep(0.3)
        async for changes in awatch(*args, **kwargs):
            yield changes

    read_held = _session_lease_watch._read_held_leases
    taken: list[SessionLease] = []

    def read_then_take(*args: Any) -> dict[str, LeaseHolder]:
        held = read_held(*args)
        taken.append(SessionLease(tmp_path, SESSION_ID).acquire())
        return held

    monkeypatch.setattr(_session_lease_watch.watchfiles, "awatch", slow_awatch)
    monkeypatch.setattr(_session_lease_watch, "_read_held_leases", read_then_take)
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {}
        assert await asyncio.wait_for(anext(updates), 5) == {
            SESSION_ID: LeaseHolder(os.getpid())
        }
    finally:
        await updates.aclose()
        for lease in taken:
            lease.release()


@pytest.mark.asyncio
async def test_rechecks_a_holder_whose_diagnostic_names_another_live_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_session_lease_watch, "_HELD_LEASE_RECHECK_S", 0.2)
    holder = await _hold_lease_in_another_process(tmp_path)
    bystander = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; sys.stdin.read()",
        stdin=asyncio.subprocess.PIPE,
    )
    diagnostic = tmp_path / "active" / f"{SESSION_ID}.lock.json"
    diagnostic.write_text(json.dumps({"process_id": bystander.pid}), encoding="utf-8")
    await asyncio.sleep(0.5)
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {SESSION_ID: LeaseHolder(bystander.pid)}

        holder.kill()
        await holder.wait()

        assert await asyncio.wait_for(anext(updates), 5) == {SESSION_ID: None}
    finally:
        await updates.aclose()
        bystander.kill()
        await bystander.wait()


@pytest.mark.asyncio
async def test_does_not_spin_on_a_holder_this_process_cannot_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def invisible(_pid: int) -> None:
        raise ProcessLookupError

    reads = 0
    read_holders = _session_lease_watch._read_lease_holders

    def counted(*args: Any) -> Any:
        nonlocal reads
        reads += 1
        return read_holders(*args)

    monkeypatch.setattr(_session_lease_watch, "_process_exit_descriptor", invisible)
    monkeypatch.setattr(_session_lease_watch, "_read_lease_holders", counted)
    lease = SessionLease(tmp_path, SESSION_ID).acquire()
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {SESSION_ID: LeaseHolder(os.getpid())}
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(updates), 0.5)
    finally:
        await updates.aclose()
        lease.release()

    assert reads <= 3
