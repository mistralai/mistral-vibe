from __future__ import annotations

from collections.abc import Iterator, Set
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import errno
import json
import os
from pathlib import Path
import threading
from typing import Any

import pytest

from tests.skills.registry.conftest import held_by_another_process, make_item
from vibe.core.skills.registry import _eviction, _leases, _store
from vibe.core.skills.registry._eviction import (
    claim_cleanup,
    evict_versions,
    last_cleanup,
    unused_versions,
)
from vibe.core.skills.registry._leases import StoreLease
from vibe.core.utils import file_lock


def _version(skill_id: str, version: int, *, used_days_ago: int | None = 40) -> Path:
    """``used_days_ago`` None leaves it never marked used."""
    dest = _store.skill_dir(skill_id, version)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text(f"# {skill_id} v{version}\n", encoding="utf-8")
    if used_days_ago is not None:
        _used(skill_id, version, days_ago=used_days_ago)
    return dest


def _used(skill_id: str, version: int, *, days_ago: int) -> None:
    marker = _store.used_marker(skill_id, version)
    marker.touch()
    _age(marker, days_ago)


def _age(path: Path, days_ago: float) -> None:
    stamp = (datetime.now(UTC) - timedelta(days=days_ago)).timestamp()
    os.utime(path, (stamp, stamp))


def _fresh(path: Path) -> bool:
    return path.stat().st_mtime > (datetime.now(UTC) - timedelta(minutes=1)).timestamp()


def _cleanup(pinned: Set[tuple[str, int]] = frozenset()) -> list[tuple[str, int]]:
    """A cleanup whose every candidate the registry still serves."""
    return evict_versions(unused_versions(pinned))


def test_an_old_unused_version_is_evicted_and_nothing_is_left_behind() -> None:
    old = _version("cid", 1)
    newest = _version("cid", 2)

    assert _cleanup() == [("cid", 1)]

    assert not old.exists()
    assert not _store.used_marker("cid", 1).exists()
    assert newest.is_dir()
    assert [entry.name for entry in old.parent.iterdir() if "trash" in entry.name] == []


def test_the_newest_version_of_a_skill_is_kept() -> None:
    only = _version("cid", 1, used_days_ago=400)

    assert _cleanup() == []
    assert only.is_dir()


def test_a_version_a_manifest_pins_is_kept() -> None:
    pinned = _version("cid", 1)
    _version("cid", 2)

    assert _cleanup({("cid", 1)}) == []
    assert pinned.is_dir()


@pytest.mark.parametrize("days_ago", [2, 29])
def test_a_version_a_session_used_within_30_days_is_kept(days_ago: int) -> None:
    used = _version("cid", 1, used_days_ago=days_ago)
    _version("cid", 2)

    assert _cleanup() == []
    assert used.is_dir()


def test_a_version_never_marked_is_marked_and_kept_however_old_it_is() -> None:
    old = _version("cid", 1, used_days_ago=None)
    _age(old, 45)
    _version("cid", 2, used_days_ago=None)

    assert unused_versions(set()) == {}
    assert old.is_dir()
    assert _fresh(_store.used_marker("cid", 1))


def test_when_a_version_was_written_does_not_count_as_use() -> None:
    written_now = _version("cid", 1)
    _version("cid", 2)

    assert _fresh(written_now)
    assert _cleanup() == [("cid", 1)]


def _held_for_40_days(skill_id: str, version: int) -> StoreLease:
    lease = StoreLease()
    lease.hold([(skill_id, version)])
    _used(skill_id, version, days_ago=40)
    return lease


def test_a_held_version_is_kept_until_30_days_after_its_session_lets_go() -> None:
    held = _version("cid", 1)
    _version("cid", 2)
    lease = _held_for_40_days("cid", 1)

    assert _cleanup() == []
    lease.release()
    assert _cleanup() == []
    assert held.is_dir()

    _used("cid", 1, days_ago=31)
    assert _cleanup() == [("cid", 1)]


def test_a_version_a_live_session_holds_is_no_candidate() -> None:
    _version("cid", 1)
    _version("cid", 2)
    lease = _held_for_40_days("cid", 1)

    assert unused_versions(set()) == {}
    lease.release()


def test_a_version_a_session_holds_once_the_scan_is_done_is_kept() -> None:
    kept = _version("cid", 1)
    _version("cid", 2)
    unused = unused_versions(set())
    lease = _held_for_40_days("cid", 1)

    assert unused == {"cid": [1]}
    assert evict_versions(unused) == []
    assert kept.is_dir()
    lease.release()


def test_a_session_letting_go_as_the_cleanup_reads_the_marker_keeps_its_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kept = _version("cid", 1)
    _version("cid", 2)
    lease = _held_for_40_days("cid", 1)
    unused_since = _eviction._unused_since

    def then_let_go(skill_id: str, version: int, cutoff: float) -> bool:
        unused = unused_since(skill_id, version, cutoff)
        lease.release()
        return unused

    monkeypatch.setattr(_eviction, "_unused_since", then_let_go)

    assert evict_versions({"cid": [1]}) == []
    assert kept.is_dir()
    lease.release()


def test_a_version_a_session_marks_as_the_cleanup_moves_it_aside_comes_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kept = _version("cid", 1)
    _version("cid", 2)
    move_aside = _store.move_aside

    def marked_as_it_goes(dest: Path) -> Path:
        trash = move_aside(dest)
        _store.mark_used("cid", 1)
        return trash

    monkeypatch.setattr(_store, "move_aside", marked_as_it_goes)

    assert evict_versions({"cid": [1]}) == []
    assert kept.is_dir()
    assert [path.name for path in kept.parent.iterdir() if "trash" in path.name] == []


def test_a_version_a_session_loads_as_the_cleanup_runs_stays_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kept = _version("cid", 1)
    _version("cid", 2)
    is_materialized = _store._is_materialized

    def checked_then_cleaned(skill_id: str, version: int) -> bool:
        stored = is_materialized(skill_id, version)
        evict_versions({"cid": [1]})
        return stored

    monkeypatch.setattr(_store, "_is_materialized", checked_then_cleaned)

    assert _store.mark_versions_used([("cid", 1)])
    assert kept.is_dir()


def test_a_version_used_again_before_its_delete_is_kept() -> None:
    kept = _version("cid", 1)
    _version("cid", 2)
    unused = unused_versions(set())
    _store._materialize(make_item(skill_id="cid", version=1), "cid")

    assert unused == {"cid": [1]}
    assert evict_versions(unused) == []
    assert kept.is_dir()


def test_a_dead_sessions_lease_is_cleared_and_its_versions_count_as_just_used() -> None:
    held = _version("cid", 1)
    _version("cid", 2)
    leases = _leases.leases_root()
    leases.mkdir(parents=True)
    (leases / "1-dead.json").write_text(json.dumps([["cid", 1]]), encoding="utf-8")
    (leases / "1-dead.lock").touch()

    assert _cleanup() == []

    assert held.exists()
    assert _fresh(_store.used_marker("cid", 1))
    assert list(leases.iterdir()) == []


def test_a_session_dying_mid_cleanup_holds_its_version_until_the_next() -> None:
    kept = _version("cid", 1)
    _version("cid", 2)
    unused = unused_versions(set())
    leases = _leases.leases_root()
    leases.mkdir(parents=True)
    (leases / "1-dead.json").write_text(json.dumps([["cid", 1]]), encoding="utf-8")
    (leases / "1-dead.lock").touch()

    assert evict_versions(unused) == []
    assert kept.is_dir()
    assert sorted(path.name for path in leases.iterdir()) == [
        "1-dead.json",
        "1-dead.lock",
    ]
    assert not _fresh(_store.used_marker("cid", 1))

    assert _cleanup() == []
    assert list(leases.iterdir()) == []
    assert _fresh(_store.used_marker("cid", 1))


def test_a_cleanup_deletes_what_an_earlier_eviction_moved_aside(tmp_path: Path) -> None:
    trash = _store.store_root() / "cid" / ".1.trash-0123"
    trash.mkdir(parents=True)
    (trash / "SKILL.md").write_text("# cid v1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("# not the store's\n", encoding="utf-8")
    link = trash.parent / ".3.trash-4567"
    link.symlink_to(outside, target_is_directory=True)
    _version("cid", 2)

    unused_versions(set())

    assert not trash.exists()
    assert not link.is_symlink()
    assert (outside / "SKILL.md").is_file()


def test_an_evicted_version_that_is_a_link_goes_without_what_it_points_to(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("# cid v1\n", encoding="utf-8")
    link = _store.skill_dir("cid", 1)
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    _used("cid", 1, days_ago=40)
    _version("cid", 2)

    assert _cleanup() == [("cid", 1)]

    assert sorted(entry.name for entry in link.parent.iterdir()) == [
        ".1.lock",
        ".2.used",
        "2",
    ]
    assert (outside / "SKILL.md").is_file()


def test_a_cleanup_deletes_what_a_write_staged_and_left_over_an_hour_ago() -> None:
    _version("cid", 2)
    skill = _store.store_root() / "cid"
    cache = _store.store_root().parent
    old_version, new_version = skill / ".1.tmp-0123", skill / ".3.tmp-4567"
    old_record = cache / ".resolved-aliases.json.tmp-0123"
    new_record = cache / ".project-manifests.json.tmp-4567"
    for version in (old_version, new_version):
        version.mkdir()
    for record in (old_record, new_record):
        record.write_text("{}", encoding="utf-8")
    for staged in (old_version, old_record):
        _age(staged, 2 / 24)

    unused_versions(set())

    assert not old_version.exists()
    assert not old_record.exists()
    assert new_version.is_dir()
    assert new_record.is_file()


def test_a_skill_that_cannot_be_read_is_skipped_and_the_rest_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for skill_id in ("cid", "locked"):
        _version(skill_id, 1)
        _version(skill_id, 2)
    locked = _store.store_root() / "locked"
    iterdir = Path.iterdir

    def unreadable(self: Path) -> Iterator[Path]:
        if self == locked:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return iterdir(self)

    monkeypatch.setattr(Path, "iterdir", unreadable)

    assert _cleanup() == [("cid", 1)]
    assert _store.is_materialized_sync("locked", 1)


def test_an_entry_that_is_not_a_plain_version_number_is_skipped() -> None:
    odd = _store.store_root() / "cid" / "²"
    odd.mkdir(parents=True)
    (odd / "SKILL.md").write_text("# not a version\n", encoding="utf-8")
    _version("cid", 1)
    _version("cid", 2)

    assert _cleanup() == [("cid", 1)]
    assert odd.is_dir()


def test_a_version_whose_lock_cannot_be_taken_is_skipped_and_the_rest_go() -> None:
    for skill_id in ("cid", "other"):
        for version in (1, 2, 3):
            _version(skill_id, version)

    with held_by_another_process(_store.skill_dir("cid", 1).parent / ".1.lock"):
        evicted = _cleanup()

    assert evicted == [("cid", 2), ("other", 1), ("other", 2)]
    assert _store.is_materialized_sync("cid", 1)


def test_a_writer_waiting_on_an_eviction_writes_the_version_back_before_its_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _version("cid", 1)
    _version("cid", 2)
    unused = unused_versions(set())
    evicting = threading.Event()
    writer_waits = threading.Event()
    written_back = threading.Event()
    acquire = file_lock.acquire_file_lock
    still_unused = _eviction._still_unused
    item = make_item(skill_id="cid", version=1, body="# written back")

    def acquire_watching(file: Any, *, blocking: bool = False) -> None:
        try:
            acquire(file, blocking=blocking)
        except BlockingIOError:
            if evicting.is_set():
                writer_waits.set()
            raise

    def check_once_the_writer_waits(skill_id: str, version: int, cutoff: float) -> bool:
        evicting.set()
        assert writer_waits.wait(timeout=10)
        return still_unused(skill_id, version, cutoff)

    delete = _store.delete

    def delete_once_written_back(path: Path) -> None:
        assert written_back.wait(timeout=10)
        delete(path)

    def write_back() -> Path | None:
        dest = _store._materialize(item, "cid")
        written_back.set()
        return dest

    monkeypatch.setattr(file_lock, "acquire_file_lock", acquire_watching)
    monkeypatch.setattr(_eviction, "_still_unused", check_once_the_writer_waits)
    monkeypatch.setattr(_store, "delete", delete_once_written_back)

    with ThreadPoolExecutor(max_workers=2) as pool:
        evicted = pool.submit(evict_versions, unused)
        assert evicting.wait(timeout=10)
        written = pool.submit(write_back)
        results = (evicted.result(timeout=30), written.result(timeout=30))

    dest = _store.skill_dir("cid", 1)
    assert results == ([("cid", 1)], dest)
    assert _store.is_materialized_sync("cid", 1)
    assert "written back" in (dest / "SKILL.md").read_text(encoding="utf-8")
    assert _fresh(_store.used_marker("cid", 1))


def test_a_machine_runs_one_cleanup_a_day() -> None:
    assert claim_cleanup()
    assert not claim_cleanup()

    _age(last_cleanup(), 23 / 24)
    assert not claim_cleanup()

    _age(last_cleanup(), 1)
    assert claim_cleanup()


def test_of_two_sessions_claiming_the_cleanup_at_once_only_one_runs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = threading.Event()
    second_tried = threading.Event()
    acquire = file_lock.acquire_file_lock
    ran_lately = _eviction._ran_lately

    def acquire_watching(file: Any, *, blocking: bool = False) -> None:
        try:
            acquire(file, blocking=blocking)
        except BlockingIOError:
            if checked.is_set():
                second_tried.set()
            raise

    def check_then_let_the_second_in(marker: Path) -> bool:
        ran = ran_lately(marker)
        if not checked.is_set():
            checked.set()
            assert second_tried.wait(timeout=5)
        return ran

    monkeypatch.setattr(file_lock, "acquire_file_lock", acquire_watching)
    monkeypatch.setattr(_eviction, "_ran_lately", check_then_let_the_second_in)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim_cleanup)
        assert checked.wait(timeout=10)
        second = pool.submit(claim_cleanup)
        claims = sorted([first.result(timeout=30), second.result(timeout=30)])

    assert claims == [False, True]


def test_a_cleanup_skips_a_version_a_writer_holds_without_waiting_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_store, "STORE_LOCK_WAIT", timedelta(minutes=5))
    _version("cid", 1)
    _version("cid", 2)

    with held_by_another_process(_store.skill_dir("cid", 1).parent / ".1.lock"):
        assert _cleanup() == []

    assert _store.is_materialized_sync("cid", 1)


def test_a_claim_made_while_another_session_claims_runs_nothing_at_once() -> None:
    lock = last_cleanup().with_name(f"{last_cleanup().name}.lock")

    with held_by_another_process(lock):
        assert not claim_cleanup()

    assert not last_cleanup().exists()
