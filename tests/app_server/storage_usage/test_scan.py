from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any

import pytest

from tests.app_server.storage_usage._tree import NOW_MS, Tree, bump_mtime, write
from vibe.app_server._storage_usage import scan as scan_module, sizes
from vibe.app_server._storage_usage.scan import (
    DAILY_FULL_SCAN_MS,
    Previous,
    ScanRequest,
    scan,
)
from vibe.app_server._storage_usage.sizes import Budget
from vibe.app_server.protocol import (
    StorageSessionUsage,
    StorageUsageRefreshResponse,
    StorageUsageSnapshot,
    StorageUsageUpdatedParams,
)


def _request(tree: Tree, **overrides: Any) -> ScanRequest:
    fields: dict[str, Any] = {
        "home": tree.home,
        "worktrees_root": tree.worktrees_root,
        "session_roots": (tree.sessions_root,),
        "now_ms": NOW_MS,
        "time_limit_s": 600.0,
    }
    return ScanRequest(**(fields | overrides))


def _snapshot(document: dict[str, Any]) -> StorageUsageSnapshot:
    return StorageUsageSnapshot.model_validate(document["snapshot"], by_alias=True)


def _rescan(tree: Tree, document: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    previous = Previous.from_cache(document)
    fields: dict[str, Any] = {"previous": previous, "now_ms": NOW_MS + 60_000}
    return scan(_request(tree, **(fields | overrides)))


def test_a_subagent_child_folds_into_its_root_session(tree: Tree) -> None:
    """*Prepare*: A root session and a subagent child naming it in its runtime state.
    *Do*: Scan.
    *Assert*: One row, whose history includes the child's.
    """
    # Prepare
    tree.session("root", size=10_000)
    tree.child("child-0a1b2c", "root", size=20_000)
    alone = scan(_request(tree, session_roots=()))

    # Do
    document = scan(_request(tree))

    # Assert
    rows = {row.session_id: row for row in _snapshot(document).sessions}
    assert set(rows) == {"root"}
    assert rows["root"].history_bytes >= 30_000
    assert document["childRoots"] == {"child-0a1b2c": "root"}
    assert not _snapshot(alone).sessions


def test_a_fork_keeps_its_own_row(tree: Tree) -> None:
    """*Prepare*: A session and a fork of it, both with metadata.
    *Do*: Scan.
    *Assert*: Two rows; the fork is not folded into its parent.
    """
    # Prepare
    tree.session("parent", size=10_000)
    tree.session("fork", size=10_000, parent="parent")

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    assert {row.session_id for row in snapshot.sessions} == {"parent", "fork"}


def test_a_session_without_metadata_is_an_untitled_row_never_suggested(
    tree: Tree,
) -> None:
    """*Prepare*: A session folder with no meta.json.
    *Do*: Scan.
    *Assert*: Its own untitled row, active, with the newest file time as its
    last activity, and listed as never suggested.
    """
    # Prepare
    tree.session("no-meta", meta=False)

    # Do
    document = scan(_request(tree))

    # Assert
    [row] = _snapshot(document).sessions
    assert row.session_id == "no-meta"
    assert row.title is None
    assert not row.archived
    assert row.last_activity_at > 0
    assert document["neverSuggested"] == ["no-meta"]


def test_an_orphan_child_goes_to_other_vibe_data(tree: Tree) -> None:
    """*Prepare*: A child whose root session is gone.
    *Do*: Scan.
    *Assert*: No row; its size is in Other Vibe data as an orphan history.
    """
    # Prepare
    tree.child("child-ffff", "deleted-root", size=30_000)

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    assert not snapshot.sessions
    orphans = [entry for entry in snapshot.other if entry.kind == "orphan_history"]
    assert orphans and orphans[0].bytes >= 30_000


def test_legacy_folders_and_other_session_data_go_to_other_vibe_data(
    tree: Tree,
) -> None:
    """*Prepare*: A legacy session folder, a lease folder and the shared scratchpad.
    *Do*: Scan.
    *Assert*: No row for them; logs and the scratchpad are counted in Other.
    """
    # Prepare
    write(tree.sessions_root / "session_20250101_legacy" / "messages.jsonl", 40_000)
    write(tree.sessions_root / "active" / "abc.lock", 10)
    write(tree.home / "scratchpad" / "notes.md", 50_000)

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    assert not snapshot.sessions
    kinds = {entry.kind: entry.bytes for entry in snapshot.other}
    assert kinds["logs_and_caches"] >= 40_000
    assert kinds["shared_scratchpad"] >= 50_000
    assert snapshot.totals.other_vibe_data_bytes == sum(kinds.values())


def test_worktree_layout_bucket_unclaimed_and_not_a_bucket(tree: Tree) -> None:
    """*Prepare*: A claimed worktree, an unclaimed folder in a bucket, a folder
    that is not a bucket, and the template folder.
    *Do*: Scan.
    *Assert*: The claimed one is the session's, the unclaimed one a leftover,
    the rest Other Vibe data; a worktree shared by two sessions counts once.
    """
    # Prepare
    tree.session("one", cwd=tree.worktrees_root / "repo-0123456789ab" / "shared")
    tree.session("two")
    tree.worktree("shared", sessions=["one", "two"], size=100_000)
    tree.worktree("stray", claim=False, size=50_000)
    write(tree.worktrees_root / "not-a-bucket" / "file", 7_000)
    write(tree.worktrees_root / ".templates" / "repo-0123456789ab" / "f", 9_000)

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    worktrees = {row.id.split("/")[1]: row for row in snapshot.worktrees}
    assert worktrees["shared"].session_ids == ["one", "two"]
    assert worktrees["shared"].managed and not worktrees["shared"].leftover
    assert not worktrees["stray"].managed and worktrees["stray"].leftover
    rows = {row.session_id: row for row in snapshot.sessions}
    assert rows["one"].worktree_id == worktrees["shared"].id
    assert rows["two"].worktree_id == worktrees["shared"].id
    kinds = {entry.kind for entry in snapshot.other}
    assert {"worktrees_unknown", "worktree_template"} <= kinds
    shared = worktrees["shared"].bytes
    assert shared is not None and shared >= 100_000
    assert snapshot.totals.leftover_worktrees_bytes == worktrees["stray"].bytes
    histories = sum(row.history_bytes for row in snapshot.sessions)
    assert snapshot.totals.active_sessions_bytes == histories + shared


def test_a_worktree_of_archived_sessions_only_is_a_leftover(tree: Tree) -> None:
    """*Prepare*: A worktree claimed by one archived session.
    *Do*: Scan.
    *Assert*: Leftover; the session's history is in Archived sessions.
    """
    # Prepare
    tree.session("done", archived=True, size=3_000)
    tree.worktree("old", sessions=["done"])

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    [worktree] = snapshot.worktrees
    assert worktree.leftover
    assert snapshot.totals.archived_sessions_bytes >= 3_000


def test_an_unchanged_worktree_keeps_its_cached_size(tree: Tree) -> None:
    """*Prepare*: A scanned worktree, then a file added inside it, with no signal.
    *Do*: Scan again, not full.
    *Assert*: The cached size and its measure time stay.
    """
    # Prepare
    tree.session("s")
    folder = tree.worktree("w", sessions=["s"], size=10_000)
    first = scan(_request(tree))
    write(folder / "build" / "out.bin", 200_000)

    # Do
    second = _rescan(tree, first)

    # Assert
    assert second["worktrees"] == first["worktrees"]
    assert second["remeasured"] == 0


def test_each_change_signal_triggers_a_re_measure(tree: Tree) -> None:
    """*Prepare*: A scanned worktree.
    *Do*: Move each signal in turn, the history, git's index found through a
    ``gitdir:`` naming ``w1``, and the pnpm state, then scan.
    *Assert*: Each scan re-measures it and sees the new file.
    """
    # Prepare
    history = tree.session("s")
    folder = tree.worktree("w", sessions=["s"], size=10_000, admin_name="w1")
    write(folder / "node_modules" / ".modules.yaml", 10)
    document = scan(_request(tree))
    moves = [
        lambda: write(history / "journal" / "000002", 10),
        lambda: bump_mtime(tree.index_of("w1")),
        lambda: bump_mtime(folder / "node_modules" / ".modules.yaml"),
    ]

    for step, move in enumerate(moves):
        write(folder / f"new-{step}.bin", 100_000)
        before = document["worktrees"]["repo-0123456789ab/w"]["bytes"]

        # Do
        move()
        document = _rescan(tree, document)

        # Assert
        assert document["remeasured"] == 1, step
        assert document["worktrees"]["repo-0123456789ab/w"]["bytes"] > before


def test_the_daily_full_scan_and_refresh_re_measure_everything(tree: Tree) -> None:
    """*Prepare*: A scanned worktree that changed without a signal.
    *Do*: Scan a day later, then scan with ``full``.
    *Assert*: Both re-measure it and mark the snapshot full.
    """
    # Prepare
    tree.session("s")
    folder = tree.worktree("w", sessions=["s"])
    first = scan(_request(tree, full=True))
    write(folder / "big.bin", 100_000)

    # Do
    daily = _rescan(tree, first, now_ms=NOW_MS + DAILY_FULL_SCAN_MS)
    refresh = _rescan(tree, first, full=True)

    # Assert
    for document in (daily, refresh):
        assert document["remeasured"] == 1
        assert _snapshot(document).full
    assert daily["lastFullScanAt"] == NOW_MS + DAILY_FULL_SCAN_MS


def test_a_scan_after_an_archive_is_never_the_daily_full_scan(tree: Tree) -> None:
    """*Prepare*: A cache whose last full scan is over a day old.
    *Do*: Scan for a ``waitForSessionIds`` refresh.
    *Assert*: Not full, and the last full scan time is kept.
    """
    # Prepare
    tree.session("s")
    folder = tree.worktree("w", sessions=["s"])
    first = scan(_request(tree, full=True))
    write(folder / "big.bin", 100_000)

    # Do
    after_archive = _rescan(
        tree, first, now_ms=NOW_MS + 2 * DAILY_FULL_SCAN_MS, allow_daily_full=False
    )

    # Assert
    assert after_archive["remeasured"] == 0
    assert not _snapshot(after_archive).full
    assert after_archive["lastFullScanAt"] == NOW_MS


def test_a_write_during_the_last_turn_triggers_a_re_measure(tree: Tree) -> None:
    """*Prepare*: A session mid-turn, scanned.
    *Do*: The turn writes its last tool result to the history and a build
    output to the worktree, then ends; scan again.
    *Assert*: The worktree is re-measured with the build output.
    """
    # Prepare
    history = tree.session("s")
    folder = tree.worktree("w", sessions=["s"], size=1_000)
    mid_turn = scan(_request(tree))

    # Do
    write(folder / "dist" / "bundle.js", 300_000)
    write(history / "tool-results" / "last.json", 100)
    after = _rescan(tree, mid_turn)

    # Assert
    assert after["remeasured"] == 1
    assert after["worktrees"]["repo-0123456789ab/w"]["bytes"] >= 300_000


def test_a_cut_scan_reports_partial_and_not_measured(tree: Tree) -> None:
    """*Prepare*: A worktree never measured and a budget already spent.
    *Do*: Scan.
    *Assert*: Partial, the worktree's size is null and counted as not measured,
    and it is left out of the totals.
    """
    # Prepare
    tree.session("s")
    tree.worktree("w", sessions=["s"])
    spent = Budget(deadline=0.0)

    # Do
    document = scan(_request(tree), spent)

    # Assert
    snapshot = _snapshot(document)
    assert document["status"] == "partial"
    assert snapshot.not_measured_count == 1
    assert snapshot.worktrees[0].bytes is None
    assert not snapshot.full
    assert document["worktrees"]["repo-0123456789ab/w"]["bytes"] is None


def test_a_cut_scan_keeps_the_previous_size(tree: Tree) -> None:
    """*Prepare*: A measured worktree whose signal then moved.
    *Do*: Scan with no budget left.
    *Assert*: The old size stays.
    """
    # Prepare
    tree.session("s")
    tree.worktree("w", sessions=["s"])
    first = scan(_request(tree))
    bump_mtime(tree.index_of("w"))

    # Do
    second = scan(
        _request(tree, previous=Previous.from_cache(first)), Budget(deadline=0.0)
    )

    # Assert
    assert (
        second["worktrees"]["repo-0123456789ab/w"]
        == first["worktrees"]["repo-0123456789ab/w"]
    )


class _Folders:
    """How many more worktrees the time limit lets a scan measure."""

    def __init__(self, count: int) -> None:
        self.left = count


def _cut_after(
    monkeypatch: pytest.MonkeyPatch, folders: _Folders, worktrees_root: Path
) -> list[str]:
    """Measures past the first ``folders.left`` worktrees come back cut; the
    names of the worktrees measured, in order.
    """
    real_measure = scan_module.measure
    measured: list[str] = []

    def measure(root: Path, budget: Budget, seen: Any, **kwargs: Any) -> Any:
        if root.parent.parent != worktrees_root:
            return real_measure(root, Budget(), seen, **kwargs)
        if folders.left <= 0:
            return sizes.Measured(complete=False)
        folders.left -= 1
        measured.append(root.name)
        return real_measure(root, Budget(), seen, **kwargs)

    monkeypatch.setattr(scan_module, "measure", measure)
    return measured


def test_a_cut_full_scan_leaves_the_next_one_the_worktrees_it_did_not_reach(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: Three worktrees, and a time limit that lets a scan measure
    two of them.
    *Do*: Run three full scans in a row, each from the last one's cache.
    *Assert*: The second measures the one the first did not reach before
    re-measuring any; the third starts with the oldest measurement; rows keep
    their order.
    """
    # Prepare
    tree.session("s")
    for name in ("a", "b", "c"):
        tree.worktree(name, sessions=["s"])
    folders = _Folders(2)
    measured = _cut_after(monkeypatch, folders, tree.worktrees_root)

    # Do
    first = scan(_request(tree, full=True))
    reached_first = list(measured)
    folders.left, measured[:] = 2, []
    second = _rescan(tree, first, full=True)
    reached_second = list(measured)
    folders.left, measured[:] = 2, []
    _rescan(tree, second, full=True, now_ms=NOW_MS + 120_000)
    reached_third = list(measured)

    # Assert
    assert len(reached_first) == 2
    (missed,) = {"a", "b", "c"} - set(reached_first)
    assert reached_second[0] == missed
    assert reached_third[0] == reached_first[1]
    assert _snapshot(first).not_measured_count == 1
    assert _snapshot(second).not_measured_count == 0
    assert [row.id for row in _snapshot(second).worktrees] == [
        row.id for row in _snapshot(first).worktrees
    ]


def test_the_time_limit_counts_from_the_submit(tree: Tree) -> None:
    """*Prepare*: A scan submitted longer ago than its time limit, as when it
    waited in the worker's queue, and a worktree never measured.
    *Do*: Scan.
    *Assert*: The limit has already passed: the worktree is not measured.
    """
    # Prepare
    tree.session("s")
    tree.worktree("w", sessions=["s"])
    submitted_at = time.time() - 10.0

    # Do
    document = scan(_request(tree, time_limit_s=5.0, submitted_at=submitted_at))

    # Assert
    assert document["cutByTimeLimit"]
    assert _snapshot(document).not_measured_count == 1


def test_copy_on_write_is_read_from_the_worktree_config(tree: Tree) -> None:
    """*Prepare*: One cloned worktree and one plain.
    *Do*: Scan.
    *Assert*: Only the clone is flagged.
    """
    # Prepare
    tree.worktree("cloned", copy_on_write=True, claim=False)
    tree.worktree("plain", claim=False)

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    flags = {row.id.split("/")[1]: row.is_copy_on_write for row in snapshot.worktrees}
    assert flags == {"cloned": True, "plain": False}


def test_size_kind_is_allocated_when_a_worktree_was_not_read_privately(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A worktree on a volume that gives no private sizes.
    *Do*: Scan.
    *Assert*: The snapshot says its sizes are allocated ones.
    """
    # Prepare
    tree.worktree("w", claim=False)
    monkeypatch.setattr(
        "vibe.app_server._storage_usage.sizes._bulk_reader_available", lambda: False
    )

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    assert snapshot.size_kind == "allocated"


def test_an_incremental_scan_keeps_the_size_kind_of_the_sizes_it_reuses(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A worktree measured by allocated size (a volume without
    private sizes), then a system that reads private sizes again.
    *Do*: Scan again without re-measuring it.
    *Assert*: Still allocated: the size shown was not read privately.
    """
    # Prepare
    tree.worktree("w", claim=False)
    reader = "vibe.app_server._storage_usage.sizes._bulk_reader_available"
    monkeypatch.setattr(reader, lambda: False)
    first = scan(_request(tree))
    monkeypatch.setattr(reader, lambda: True)

    # Do
    second = _rescan(tree, first)

    # Assert
    assert second["remeasured"] == 0
    assert _snapshot(second).size_kind == "allocated"


def test_a_malformed_cache_section_reads_as_nothing_remembered(tree: Tree) -> None:
    """*Prepare*: A version-1 cache whose worktrees and child roots are not objects.
    *Do*: Read what the last scan left, then scan from it.
    *Assert*: Nothing remembered, and the scan runs.
    """
    # Prepare
    tree.worktree("w", claim=False)
    document: dict[str, Any] = {"version": 1, "worktrees": [1, 2], "childRoots": "x"}

    # Do
    previous = Previous.from_cache(document)
    result = scan(_request(tree, previous=previous))

    # Assert
    assert previous.worktrees == {} and previous.child_roots == {}
    assert result["remeasured"] == 1


def test_each_session_row_points_at_its_own_history_folder(tree: Tree) -> None:
    """*Prepare*: A root session with a subagent child, and a fork.
    *Do*: Scan.
    *Assert*: Each row's history path is the absolute folder of that session,
    never the child's, so revealing it opens the right one.
    """
    # Prepare
    root = tree.session("root")
    tree.child("child-0a1b2c", "root")
    fork = tree.session("fork", parent="root")

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    paths = {row.session_id: row.history_path for row in snapshot.sessions}
    assert paths == {"root": str(root), "fork": str(fork)}
    assert all(Path(path).is_absolute() for path in paths.values() if path)


def test_a_session_row_from_an_older_server_has_no_history_path() -> None:
    """*Prepare*: A session row as a server before ``historyPath`` sent it.
    *Do*: Parse it.
    *Assert*: It parses, with no history path.
    """
    # Do
    row = StorageSessionUsage.model_validate(
        {"sessionId": "s", "historyBytes": 10}, by_alias=True
    )

    # Assert
    assert row.history_path is None
    assert row.model_dump(by_alias=True)["historyPath"] is None


def test_other_vibe_data_skips_a_folder_on_another_device(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A folder under ``~/.vibe`` that sits on another device, as a
    mount point does, beside a session's history.
    *Do*: Scan.
    *Assert*: Its files are not counted in Other Vibe data.
    """
    # Prepare
    tree.session("s")
    mounted = tree.home / "mounted"
    write(mounted / "big.bin", 400_000)
    real_device_of = sizes._device_of

    def device_of(entry: os.DirEntry[str]) -> int:
        device = real_device_of(entry)
        return device + 1 if Path(entry.path) == mounted else device

    monkeypatch.setattr(sizes, "_device_of", device_of)

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    other = {entry.kind: entry.bytes for entry in snapshot.other}
    assert other["logs_and_caches"] < 400_000


def test_an_entry_with_an_unknown_device_is_still_counted(
    tree: Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: Directory entries that report device 0, as Windows does.
    *Do*: Scan.
    *Assert*: They count in Other Vibe data like any entry on the home device.
    """
    # Prepare
    tree.session("s")
    write(tree.home / "logs" / "vibe.log", 300_000)
    monkeypatch.setattr(sizes, "_device_of", lambda _entry: 0)

    # Do
    snapshot = _snapshot(scan(_request(tree)))

    # Assert
    other = {entry.kind: entry.bytes for entry in snapshot.other}
    assert other["logs_and_caches"] >= 300_000


def test_older_server_payloads_parse_without_a_scan_id() -> None:
    """*Prepare*: A refresh answer and an update as a server before
    ``scanId`` sent them.
    *Do*: Parse them.
    *Assert*: Both parse, with no scan id.
    """
    # Do
    refresh = StorageUsageRefreshResponse.model_validate(
        {"started": True}, by_alias=True
    )
    update = StorageUsageUpdatedParams.model_validate(
        {"status": "up_to_date", "eventId": 3}, by_alias=True
    )

    # Assert
    assert refresh.scan_id is None
    assert update.scan_id is None
