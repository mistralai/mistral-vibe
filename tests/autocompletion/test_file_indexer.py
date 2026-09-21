from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
from threading import Event
from typing import TYPE_CHECKING

import pytest

from vibe.cli.autocompletion.file_indexer import FileIndexer

if TYPE_CHECKING:
    from watchfiles import Change

# This suite runs against the real filesystem and watcher. A faked store/watcher
# split would be faster to unit-test, but given time constraints and the low churn
# expected for this feature, integration coverage was chosen as a trade-off.
#
# Nothing here waits on a clock. Every assertion used to sit behind a polling
# `_wait_for(..., timeout=3.0)`, which handed the verdict to whatever else the
# agent was running: the rename case went red on CI having waited three seconds
# for a watcher that normally answers in under one. The negatives were worse --
# they sampled the index for a second and called the absence of news a proof.
#
# Instead, a positive blocks on the watcher's own callback, so a slow machine
# makes these tests slower and never redder, and a genuinely lost event stops on
# pytest's timeout rather than being reported as a three-second-old opinion. A
# negative asserts that no watcher is running, which settles the question
# outright and takes no time at all.


@pytest.fixture
def file_indexer() -> Generator[FileIndexer]:
    indexer = FileIndexer(should_enable_watcher=lambda: True)
    yield indexer
    indexer.shutdown()


def _current_entries(file_indexer: FileIndexer) -> set[str]:
    return {entry.rel for entry in file_indexer.get_index(Path("."))}


def _indexed(file_indexer: FileIndexer) -> set[str]:
    """Entries straight from the store.

    Conditions run on the watcher thread, which must not re-enter `get_index`:
    that call manages the very watcher it would be running inside.
    """
    with file_indexer._lock:
        return {entry.rel for entry in file_indexer._store.snapshot()}


@contextmanager
def _watcher_applies(
    file_indexer: FileIndexer, condition: Callable[[], bool]
) -> Generator[None]:
    """Run the block, then block until the watcher has made ``condition`` true.

    The wait tracks the work: it ends when the indexer finishes applying a batch
    of changes, whenever that is. The controller looks `_on_changes` up per call,
    so the observer survives the watcher being stopped and restarted.
    """
    controller = file_indexer._watcher
    deliver = controller._on_changes
    satisfied = Event()

    def observing(root: Path, raw_changes: Iterable[tuple[Change, str]]) -> None:
        deliver(root, raw_changes)
        if condition():
            satisfied.set()

    controller._on_changes = observing
    try:
        yield
        if not condition():
            # Deliberately unbounded. A deadline here would be a guess about the
            # agent competing with a guess about the watcher, which is the thing
            # that made this file flaky; the suite-wide `timeout` in pyproject is
            # the backstop, and reaching it means an event was lost rather than
            # late.
            satisfied.wait()
        assert condition()
    finally:
        controller._on_changes = deliver


def _assert_index_is_frozen(
    file_indexer: FileIndexer, expected_entries: set[str], expected_updates: int
) -> None:
    """Assert no runtime update can reach the index, rather than that none has.

    With no watcher running there is no path by which an update could arrive, so
    this holds immediately and for good.
    """
    assert not file_indexer._watcher.is_watching
    assert _current_entries(file_indexer) == expected_entries
    assert file_indexer.stats.incremental_updates == expected_updates


def test_updates_index_on_file_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    file_indexer.get_index(Path("."))
    target = tmp_path / "new_file.py"

    with _watcher_applies(file_indexer, lambda: target.name in _indexed(file_indexer)):
        target.write_text("", encoding="utf-8")

    assert target.name in _current_entries(file_indexer)


def test_updates_index_on_file_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "new_file.py"
    target.write_text("", encoding="utf-8")
    file_indexer.get_index(Path("."))

    with _watcher_applies(
        file_indexer, lambda: target.name not in _indexed(file_indexer)
    ):
        target.unlink()

    assert target.name not in _current_entries(file_indexer)


def test_updates_index_on_file_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    old_file = tmp_path / "old_name.py"
    old_file.write_text("", encoding="utf-8")
    file_indexer.get_index(Path("."))
    new_file = tmp_path / "new_name.py"

    def renamed() -> bool:
        entries = _indexed(file_indexer)
        return old_file.name not in entries and new_file.name in entries

    with _watcher_applies(file_indexer, renamed):
        old_file.rename(new_file)

    entries = _current_entries(file_indexer)
    assert old_file.name not in entries
    assert new_file.name in entries


def test_updates_index_on_folder_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    old_folder = tmp_path / "old_folder"
    old_folder.mkdir()
    number_of_files = 5
    file_names = [f"file{i}.py" for i in range(1, number_of_files + 1)]
    for name in file_names:
        (old_folder / name).write_text("", encoding="utf-8")
    file_indexer.get_index(Path("."))

    new_folder = tmp_path / "new_folder"
    expected = {f"new_folder/{name}" for name in file_names}

    def moved() -> bool:
        entries = _indexed(file_indexer)
        return expected <= entries and not any(
            entry.startswith("old_folder/") for entry in entries
        )

    with _watcher_applies(file_indexer, moved):
        old_folder.rename(new_folder)

    entries = _current_entries(file_indexer)
    assert expected <= entries
    assert not any(entry.startswith("old_folder/") for entry in entries)


def test_updates_index_incrementally_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    file_indexer.get_index(Path("."))

    rebuilds_before = file_indexer.stats.rebuilds
    incremental_before = file_indexer.stats.incremental_updates
    target = tmp_path / "stats_file.py"

    with _watcher_applies(file_indexer, lambda: target.name in _indexed(file_indexer)):
        target.write_text("", encoding="utf-8")

    assert target.name in _current_entries(file_indexer)
    assert file_indexer.stats.rebuilds == rebuilds_before
    assert file_indexer.stats.incremental_updates >= incremental_before + 1


def test_rebuilds_index_when_mass_change_threshold_is_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mass_change_threshold = 5
    # in an ideal world, we would use "threshold + 1", but in reality, we need to test with a
    # number of files important enough to MAKE SURE that a batch of >= threshold events will be
    # detected by the watcher
    number_of_files = mass_change_threshold * 3
    monkeypatch.chdir(tmp_path)
    indexer = FileIndexer(
        mass_change_threshold=mass_change_threshold, should_enable_watcher=lambda: True
    )
    try:
        indexer.get_index(Path("."))
        rebuilds_before = indexer.stats.rebuilds
        expected = {f"bulk{i}.py" for i in range(number_of_files)}

        def rebuilt() -> bool:
            return (
                indexer.stats.rebuilds >= rebuilds_before + 1
                and _indexed(indexer) == expected
            )

        with _watcher_applies(indexer, rebuilt):
            # Consumed inside the pool's own block, so every file exists before
            # the wait begins rather than racing it.
            with ThreadPoolExecutor(max_workers=number_of_files) as pool:
                list(
                    pool.map(
                        lambda i: (tmp_path / f"bulk{i}.py").write_text(
                            "", encoding="utf-8"
                        ),
                        range(number_of_files),
                    )
                )

        assert _current_entries(indexer) == expected
        # we do not assert that "incremental_updates" did not change,
        # as the watcher potentially reported some batches of events that were
        # smaller than the threshold
        assert indexer.stats.rebuilds >= rebuilds_before + 1
    finally:
        indexer.shutdown()


def test_switching_between_roots_restarts_index(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    file_indexer: FileIndexer,
) -> None:
    """`get_index` rebuilds before it returns, so a new root needs no waiting."""
    first_root = tmp_path
    second_root = tmp_path_factory.mktemp("second-root")
    (first_root / "first.py").write_text("", encoding="utf-8")
    (second_root / "second.py").write_text("", encoding="utf-8")

    monkeypatch.chdir(first_root)
    assert "first.py" in _current_entries(file_indexer)

    monkeypatch.chdir(second_root)
    entries = _current_entries(file_indexer)
    assert "first.py" not in entries
    assert "second.py" in entries


def test_watcher_failure_does_not_break_existing_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    """The index must survive the watcher raising while applying a change.

    Waiting for the failing call is what gives the test its teeth: the index
    looks intact before the watcher has done anything, so asserting that first
    would pass without ever reaching the failure.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "seed.py").write_text("", encoding="utf-8")
    file_indexer.get_index(Path("."))

    attempted = Event()

    def boom(*_: object, **__: object) -> None:
        attempted.set()
        raise RuntimeError("boom")

    monkeypatch.setattr(file_indexer._store, "apply_changes", boom)

    (tmp_path / "new_file.py").write_text("", encoding="utf-8")
    attempted.wait()

    assert _current_entries(file_indexer) == {"seed.py"}


def test_shutdown_cleans_up_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test.txt").write_text("", encoding="utf-8")
    file_indexer = FileIndexer()
    file_indexer.get_index(Path("."))

    file_indexer.shutdown()
    assert file_indexer.get_index(Path(".")) == []


def test_watcher_is_disabled_without_an_enabled_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    file_indexer = FileIndexer()
    try:
        baseline_entries = _current_entries(file_indexer)
        incremental_before = file_indexer.stats.incremental_updates

        (tmp_path / "file.py").write_text("", encoding="utf-8")

        _assert_index_is_frozen(file_indexer, baseline_entries, incremental_before)
    finally:
        file_indexer.shutdown()


def test_git_catalog_uses_nested_gitignore_and_keeps_untracked_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracked.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.py"], check=True)
    (tmp_path / "staged.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "staged.py"], check=True)
    (tmp_path / "untracked.py").write_text("", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / ".gitignore").write_text("cache/\n", encoding="utf-8")
    (tmp_path / "generated" / "cache").mkdir()
    (tmp_path / "generated" / "cache" / "ignored.py").write_text("", encoding="utf-8")
    (tmp_path / "generated" / "kept.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    entries = {entry.rel for entry in file_indexer.get_index(Path("."))}

    assert {
        "tracked.py",
        "staged.py",
        "untracked.py",
        "generated",
        "generated/kept.py",
    } <= entries
    assert "generated/cache" not in entries
    assert "generated/cache/ignored.py" not in entries


def test_git_catalog_refreshes_lazily_after_watcher_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracked.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.py"], check=True)
    monkeypatch.chdir(tmp_path)
    file_indexer.get_index(Path("."))
    rebuilds_before = file_indexer.stats.rebuilds

    with _watcher_applies(file_indexer, lambda: file_indexer._store.is_dirty):
        (tmp_path / "new.py").write_text("", encoding="utf-8")

    assert "new.py" in _current_entries(file_indexer)
    assert file_indexer.stats.rebuilds == rebuilds_before + 1


def test_file_index_does_not_execute_project_local_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    marker = tmp_path / "executed"
    git_name = "git.exe" if os.name == "nt" else "git"
    fake_git = tmp_path / git_name
    fake_git.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    fake_git.chmod(0o755)
    monkeypatch.delenv("GIT_PYTHON_GIT_EXECUTABLE", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    file_indexer.get_index(Path("."))

    assert not marker.exists()
    assert not file_indexer._store.is_git_backed


def test_non_git_walk_stops_when_cancelled(tmp_path: Path) -> None:
    from vibe.cli.autocompletion.file_indexer.ignore_rules import IgnoreRules
    from vibe.cli.autocompletion.file_indexer.store import (
        FileIndexStats,
        FileIndexStore,
    )

    (tmp_path / "first.py").write_text("", encoding="utf-8")
    (tmp_path / "second.py").write_text("", encoding="utf-8")
    store = FileIndexStore(IgnoreRules(), FileIndexStats())
    checks = 0

    def should_cancel() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    assert store._walk_directory(tmp_path, cancel_check=should_cancel) is None


def test_disabling_watcher_stops_runtime_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    watcher_enabled = True
    file_indexer = FileIndexer(should_enable_watcher=lambda: watcher_enabled)
    try:
        tracked = tmp_path / "tracked.py"
        tracked.write_text("", encoding="utf-8")
        file_indexer.get_index(Path("."))
        assert "tracked.py" in _current_entries(file_indexer)

        watcher_enabled = False
        file_indexer.get_index(Path("."))

        expected_entries = _current_entries(file_indexer)
        incremental_before = file_indexer.stats.incremental_updates
        tracked.unlink()

        _assert_index_is_frozen(file_indexer, expected_entries, incremental_before)
    finally:
        file_indexer.shutdown()


def _assert_created_file_is_not_indexed(
    file_indexer: FileIndexer, tmp_path: Path, filename: str
) -> None:
    expected_entries = _current_entries(file_indexer)
    expected_updates = file_indexer.stats.incremental_updates

    (tmp_path / filename).write_text("", encoding="utf-8")

    _assert_index_is_frozen(file_indexer, expected_entries, expected_updates)


def _assert_created_file_is_indexed(
    file_indexer: FileIndexer, tmp_path: Path, filename: str
) -> None:
    incremental_before = file_indexer.stats.incremental_updates

    with _watcher_applies(file_indexer, lambda: filename in _indexed(file_indexer)):
        (tmp_path / filename).write_text("", encoding="utf-8")

    assert filename in _current_entries(file_indexer)
    assert file_indexer.stats.incremental_updates >= incremental_before + 1


def test_watcher_toggle_flow_off_on_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    watcher_enabled = False
    file_indexer = FileIndexer(should_enable_watcher=lambda: watcher_enabled)
    try:
        file_indexer.get_index(Path("."))
        _assert_created_file_is_not_indexed(file_indexer, tmp_path, "off_before.py")

        watcher_enabled = True
        file_indexer.get_index(Path("."))
        _assert_created_file_is_indexed(file_indexer, tmp_path, "on_file.py")

        watcher_enabled = False
        file_indexer.get_index(Path("."))
        _assert_created_file_is_not_indexed(file_indexer, tmp_path, "off_after.py")
    finally:
        file_indexer.shutdown()
