from __future__ import annotations

from datetime import UTC, datetime, timedelta
import errno
import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from vibe.core.skills.registry import _store
from vibe.core.skills.registry._leases import (
    StoreLease,
    clear_dead_leases,
    held_versions,
    leases_root,
)
from vibe.core.utils import file_lock
from vibe.core.utils.file_lock import acquire_file_lock


def _stored(skill_id: str, version: int) -> None:
    dest = _store.skill_dir(skill_id, version)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text(f"# {skill_id} v{version}\n", encoding="utf-8")


def _age(path: Path, days_ago: float) -> None:
    stamp = (datetime.now(UTC) - timedelta(days=days_ago)).timestamp()
    os.utime(path, (stamp, stamp))


def _fresh(path: Path) -> bool:
    return path.stat().st_mtime > (datetime.now(UTC) - timedelta(minutes=1)).timestamp()


def _lease_files() -> list[str]:
    return sorted(path.suffix for path in leases_root().iterdir())


def test_a_lease_holds_its_versions_marks_them_used_and_leaves_nothing() -> None:
    _stored("cid", 1)
    lease = StoreLease()

    lease.hold([("cid", 1), ("cid", 1)])

    assert held_versions() == {("cid", 1)}
    assert _fresh(_store.used_marker("cid", 1))
    lease.hold([])
    assert held_versions() == set()
    lease.release()
    assert list(leases_root().iterdir()) == []


def test_holding_what_it_holds_already_writes_nothing() -> None:
    for version in (1, 2):
        _stored("cid", version)
    lease = StoreLease()
    lease.hold([("cid", 1)])
    (record,) = leases_root().glob("*.json")
    for path in (record, _store.used_marker("cid", 1)):
        _age(path, 2)

    lease.hold([("cid", 1)])

    assert not _fresh(record)
    assert not _fresh(_store.used_marker("cid", 1))

    lease.hold([("cid", 1), ("cid", 2)])

    assert _fresh(record)
    assert not _fresh(_store.used_marker("cid", 1))
    assert _fresh(_store.used_marker("cid", 2))
    assert held_versions() == {("cid", 1), ("cid", 2)}
    lease.release()


def test_letting_go_marks_what_the_lease_held_used() -> None:
    _stored("cid", 1)
    lease = StoreLease()
    lease.hold([("cid", 1)])
    _age(_store.used_marker("cid", 1), 40)

    lease.release()

    assert _fresh(_store.used_marker("cid", 1))


def test_a_version_the_lease_drops_is_marked_used() -> None:
    for version in (1, 2):
        _stored("cid", version)
    lease = StoreLease()
    lease.hold([("cid", 1)])
    _age(_store.used_marker("cid", 1), 40)

    lease.hold([("cid", 2)])

    assert _fresh(_store.used_marker("cid", 1))
    assert held_versions() == {("cid", 2)}
    lease.release()


def test_a_record_that_cannot_be_written_is_written_by_the_next_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stored("cid", 1)
    replace = os.replace

    def refuse(*_args: object) -> None:
        raise PermissionError(errno.EACCES, "sharing violation")

    monkeypatch.setattr(os, "replace", refuse)
    lease = StoreLease()

    lease.hold([("cid", 1)])

    assert _fresh(_store.used_marker("cid", 1))
    assert _lease_files() == [".lock"]
    monkeypatch.setattr(os, "replace", replace)
    lease.hold([("cid", 1)])
    assert held_versions() == {("cid", 1)}
    lease.release()


def test_releasing_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _stored("cid", 1)
    lease = StoreLease()
    lease.hold([("cid", 1)])

    def fails(*_args: object) -> None:
        raise OSError(errno.EIO, "unlock failed")

    monkeypatch.setattr(file_lock, "release_file_lock", fails)

    with caplog.at_level(logging.WARNING):
        lease.release()

    assert "Could not release the registry skills lease" in caplog.text
    assert list(leases_root().iterdir()) == []


@pytest.mark.parametrize(
    "error", [errno.EMFILE, errno.EACCES], ids=["out-of-handles", "refused"]
)
def test_a_lease_whose_lock_cannot_be_opened_still_holds_its_versions(
    monkeypatch: pytest.MonkeyPatch, error: int
) -> None:
    root = leases_root()
    root.mkdir(parents=True)
    record = root / "1-busy.json"
    record.write_text(json.dumps([["cid", 1]]), encoding="utf-8")
    lock = record.with_suffix(".lock")
    lock.touch()
    open_path = Path.open

    def refuse_the_lock(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == lock:
            raise OSError(error, os.strerror(error))
        return open_path(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_the_lock)

    clear_dead_leases()

    assert held_versions() == {("cid", 1)}
    assert record.exists()
    assert lock.exists()


def test_a_lease_whose_lock_file_is_gone_is_cleared() -> None:
    root = leases_root()
    root.mkdir(parents=True)
    (root / "1-gone.json").write_text(json.dumps([["cid", 1]]), encoding="utf-8")

    clear_dead_leases()

    assert held_versions() == set()
    assert list(root.iterdir()) == []


def test_a_lease_entry_that_names_no_version_is_skipped() -> None:
    _stored("cid", 1)
    root = leases_root()
    root.mkdir(parents=True)
    entries = [
        ["cid", 1],
        ["cid", "/../../escaped"],
        ["../out", 1],
        ["cid", True],
        ["cid", -1],
        ["cid"],
        "junk",
    ]
    (root / "1-dead.json").write_text(json.dumps(entries), encoding="utf-8")

    clear_dead_leases()

    assert held_versions() == set()

    assert _fresh(_store.used_marker("cid", 1))
    assert not (_store.store_root().parent / "escaped.used").exists()
    assert sorted(
        path.name for path in _store.skill_dir("cid", 1).parent.iterdir()
    ) == [".1.used", "1"]


def test_old_leftovers_of_a_failed_write_or_release_are_cleared() -> None:
    root = leases_root()
    root.mkdir(parents=True)
    staging = root / ".1-crashed.json.tmp-0123"
    orphan = root / "1-orphan.lock"
    held = root / "1-held.lock"
    starting = root / "1-starting.lock"
    for path in (staging, orphan, held, starting):
        path.touch()
    for path in (staging, orphan, held):
        _age(path, 2)

    with held.open("a+b") as holder:
        acquire_file_lock(holder)
        clear_dead_leases()
        remaining = sorted(path.name for path in root.iterdir())

    assert remaining == [held.name, starting.name]
