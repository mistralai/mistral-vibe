from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest

from mistralai_vibe_local_harness.vibe import _storage
from mistralai_vibe_local_harness.vibe._storage import SessionLease
from vibe.app_server import _session_lease_poll
from vibe.app_server._session_lease_poll import LeaseHolder, watch_session_leases

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


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_session_lease_poll, "_LEASE_POLL_S", 0.1)


def _poll_rarely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_session_lease_poll, "_LEASE_POLL_S", 30.0)


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
async def test_removes_unheld_lease_files_and_keeps_foreign_files(
    tmp_path: Path,
) -> None:
    lease_dir = tmp_path / "active"
    lease_dir.mkdir()
    stale = [lease_dir / f"{SESSION_ID}.lock", lease_dir / f"{SESSION_ID}.lock.json"]
    foreign = [lease_dir / "not a session.lock", lease_dir / ".registry"]
    for path in [*stale, *foreign]:
        path.touch()
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {}
    finally:
        await updates.aclose()

    assert [path.exists() for path in stale] == [False, False]
    assert [path.exists() for path in foreign] == [True, True]


@pytest.mark.asyncio
@pytest.mark.skipif(not CAN_AWAIT_PROCESS_EXIT, reason="no process exit notifications")
async def test_reports_a_holder_killed_without_releasing_and_removes_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _poll_rarely(monkeypatch)
    holder = await _hold_lease_in_another_process(tmp_path)
    lease = SessionLease(tmp_path, SESSION_ID)
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {SESSION_ID: LeaseHolder(holder.pid)}

        holder.kill()
        await holder.wait()

        assert await asyncio.wait_for(anext(updates), 5) == {SESSION_ID: None}
    finally:
        await updates.aclose()

    assert not lease.path.exists()
    assert not lease.diagnostic_path.exists()


@pytest.mark.asyncio
async def test_rechecks_a_holder_whose_process_cannot_be_read(tmp_path: Path) -> None:
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
async def test_reports_a_new_holder_process_of_a_held_lease(tmp_path: Path) -> None:
    holder = await _hold_lease_in_another_process(tmp_path)
    bystander = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; sys.stdin.read()",
        stdin=asyncio.subprocess.PIPE,
    )
    diagnostic = tmp_path / "active" / f"{SESSION_ID}.lock.json"
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {SESSION_ID: LeaseHolder(holder.pid)}

        replacement = diagnostic.with_suffix(".tmp")
        replacement.write_text(
            json.dumps({"process_id": bystander.pid}), encoding="utf-8"
        )
        replacement.replace(diagnostic)

        assert await asyncio.wait_for(anext(updates), 5) == {
            SESSION_ID: LeaseHolder(bystander.pid)
        }
    finally:
        await updates.aclose()
        for process in (holder, bystander):
            process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_reports_a_lease_taken_after_starting_without_a_lease_directory(
    tmp_path: Path,
) -> None:
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {}

        lease = SessionLease(tmp_path, SESSION_ID).acquire()
        try:
            assert await asyncio.wait_for(anext(updates), 5) == {
                SESSION_ID: LeaseHolder(os.getpid())
            }
        finally:
            lease.release()
    finally:
        await updates.aclose()


@pytest.mark.asyncio
async def test_keeps_watching_after_a_failed_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = _session_lease_poll.sweep_session_leases
    sweeps = 0
    warnings: list[str] = []

    def failing_twice(root: Path) -> dict[str, int | None]:
        nonlocal sweeps
        sweeps += 1
        if sweeps in {2, 3}:
            raise BlockingIOError("lease directory busy")
        return sweep(root)

    monkeypatch.setattr(_session_lease_poll, "sweep_session_leases", failing_twice)
    monkeypatch.setattr(
        _session_lease_poll.logger,
        "warning",
        lambda message, *_args, **_kwargs: warnings.append(message),
    )
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {}

        lease = SessionLease(tmp_path, SESSION_ID).acquire()
        try:
            assert await asyncio.wait_for(anext(updates), 5) == {
                SESSION_ID: LeaseHolder(os.getpid())
            }
        finally:
            lease.release()
    finally:
        await updates.aclose()

    assert sweeps > 3
    assert warnings == ["Session lease poll failed"]


@pytest.mark.asyncio
async def test_starts_empty_when_the_first_poll_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweep = _session_lease_poll.sweep_session_leases
    sweeps = 0

    def failing_first(root: Path) -> dict[str, int | None]:
        nonlocal sweeps
        sweeps += 1
        if sweeps == 1:
            raise BlockingIOError("lease directory busy")
        return sweep(root)

    monkeypatch.setattr(_session_lease_poll, "sweep_session_leases", failing_first)
    lease = SessionLease(tmp_path, SESSION_ID).acquire()
    updates = watch_session_leases(tmp_path)
    try:
        assert await anext(updates) == {}
        assert await asyncio.wait_for(anext(updates), 5) == {
            SESSION_ID: LeaseHolder(os.getpid())
        }
    finally:
        await updates.aclose()
        lease.release()


@pytest.mark.asyncio
async def test_lease_file_changes_do_not_trigger_a_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _poll_rarely(monkeypatch)
    lease = SessionLease(tmp_path, SESSION_ID).acquire()
    updates = watch_session_leases(tmp_path)
    directory_locks = 0
    directory_lock = _storage._lease_directory_lock

    def counted(directory: Path) -> Any:
        nonlocal directory_locks
        directory_locks += 1
        return directory_lock(directory)

    try:
        assert await anext(updates) == {SESSION_ID: LeaseHolder(os.getpid())}
        monkeypatch.setattr(_storage, "_lease_directory_lock", counted)

        for _ in range(5):
            lease.diagnostic_path.write_bytes(lease.diagnostic_path.read_bytes())
            await asyncio.sleep(0.05)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(updates), 0.5)
        rechecks = directory_locks
    finally:
        await updates.aclose()
        lease.release()

    assert rechecks == 0


@pytest.mark.asyncio
async def test_rechecks_a_holder_whose_diagnostic_names_another_live_process(
    tmp_path: Path,
) -> None:
    holder = await _hold_lease_in_another_process(tmp_path)
    bystander = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; sys.stdin.read()",
        stdin=asyncio.subprocess.PIPE,
    )
    diagnostic = tmp_path / "active" / f"{SESSION_ID}.lock.json"
    diagnostic.write_text(json.dumps({"process_id": bystander.pid}), encoding="utf-8")
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

    _poll_rarely(monkeypatch)
    reads = 0
    read_holders = _session_lease_poll._read_held_leases

    def counted(*args: Any) -> Any:
        nonlocal reads
        reads += 1
        return read_holders(*args)

    monkeypatch.setattr(_session_lease_poll, "_process_exit_descriptor", invisible)
    monkeypatch.setattr(_session_lease_poll, "_read_held_leases", counted)
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


@pytest.mark.skipif(sys.platform != "linux", reason="pidfd_open is Linux-only")
def test_process_exit_descriptor_falls_back_to_syscall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Some python-build-standalone 3.14 builds ship no os.pidfd_open; the
    # ctypes syscall fallback must still yield a usable pidfd.
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    watched = _session_lease_poll._process_exit_descriptor(os.getpid())
    assert watched is not None
    descriptor, close = watched
    try:
        os.fstat(descriptor)
    finally:
        close()
    with pytest.raises(ProcessLookupError):
        _session_lease_poll._process_exit_descriptor(1 << 30)
