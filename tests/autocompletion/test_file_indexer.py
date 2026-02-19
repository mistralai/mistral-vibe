from __future__ import annotations

from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

import pytest

from vibe.core.autocompletion.file_indexer import FileIndexer

# This suite runs against the real filesystem and watcher. A faked store/watcher
# split would be faster to unit-test, but given time constraints and the low churn
# expected for this feature, integration coverage was chosen as a trade-off.


@pytest.fixture
def file_indexer() -> Generator[FileIndexer]:
    indexer = FileIndexer(should_enable_watcher=lambda: True)
    yield indexer
    indexer.shutdown()


def _wait_for(condition: Callable[[], bool], timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def _assert_index_state_stable(
    file_indexer: FileIndexer,
    expected_entries: set[str],
    expected_incremental_updates: int,
    duration: float = 1.0,
) -> None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        current_entries = {entry.rel for entry in file_indexer.get_index(Path("."))}
        assert current_entries == expected_entries
        assert file_indexer.stats.incremental_updates == expected_incremental_updates
        time.sleep(0.1)


def test_updates_index_on_file_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    file_indexer.get_index(Path("."))

    target = tmp_path / "new_file.py"
    target.write_text("", encoding="utf-8")

    assert _wait_for(
        lambda: any(
            entry.rel == target.name for entry in file_indexer.get_index(Path("."))
        )
    )


def test_updates_index_on_file_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "new_file.py"
    target.write_text("", encoding="utf-8")
    file_indexer.get_index(Path("."))

    target.unlink()

    assert _wait_for(
        lambda: all(
            entry.rel != target.name for entry in file_indexer.get_index(Path("."))
        )
    )


def test_updates_index_on_file_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    old_file = tmp_path / "old_name.py"
    old_file.write_text("", encoding="utf-8")
    file_indexer.get_index(Path("."))

    new_file = tmp_path / "new_name.py"
    old_file.rename(new_file)

    assert _wait_for(
        lambda: all(
            entry.rel != old_file.name for entry in file_indexer.get_index(Path("."))
        )
        and any(
            entry.rel == new_file.name for entry in file_indexer.get_index(Path("."))
        )
    )


def test_updates_index_on_folder_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    old_folder = tmp_path / "old_folder"
    old_folder.mkdir()
    number_of_files = 5
    file_names = [f"file{i}.py" for i in range(1, number_of_files + 1)]
    old_file_paths = [old_folder / name for name in file_names]
    for file_path in old_file_paths:
        file_path.write_text("", encoding="utf-8")
    file_indexer.get_index(Path("."))

    new_folder = tmp_path / "new_folder"
    old_folder.rename(new_folder)

    assert _wait_for(
        lambda: (
            entries := file_indexer.get_index(Path(".")),
            all(not entry.rel.startswith("old_folder/") for entry in entries)
            and all(
                any(entry.rel == f"new_folder/{name}" for entry in entries)
                for name in file_names
            ),
        )[1]
    )


def test_updates_index_incrementally_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    file_indexer.get_index(Path("."))

    rebuilds_before = file_indexer.stats.rebuilds
    incremental_before = file_indexer.stats.incremental_updates

    target = tmp_path / "stats_file.py"
    target.write_text("", encoding="utf-8")

    assert _wait_for(
        lambda: any(
            entry.rel == target.name for entry in file_indexer.get_index(Path("."))
        )
    )

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

        ThreadPoolExecutor(max_workers=number_of_files).map(
            lambda i: (tmp_path / f"bulk{i}.py").write_text("", encoding="utf-8"),
            range(number_of_files),
        )

        assert _wait_for(lambda: len(indexer.get_index(Path("."))) == number_of_files)
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
    first_root = tmp_path
    second_root = tmp_path_factory.mktemp("second-root")
    (first_root / "first.py").write_text("", encoding="utf-8")
    (second_root / "second.py").write_text("", encoding="utf-8")

    monkeypatch.chdir(first_root)
    assert _wait_for(
        lambda: any(
            entry.rel == "first.py" for entry in file_indexer.get_index(Path("."))
        )
    )

    monkeypatch.chdir(second_root)
    assert _wait_for(
        lambda: all(
            entry.rel != "first.py" for entry in file_indexer.get_index(Path("."))
        )
    )
    assert _wait_for(
        lambda: any(
            entry.rel == "second.py" for entry in file_indexer.get_index(Path("."))
        )
    )


def test_watcher_failure_does_not_break_existing_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, file_indexer: FileIndexer
) -> None:
    monkeypatch.chdir(tmp_path)
    seed = tmp_path / "seed.py"
    seed.write_text("", encoding="utf-8")
    file_indexer.get_index(Path("."))

    def boom(*_: object, **__: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(file_indexer._store, "apply_changes", boom)

    (tmp_path / "new_file.py").write_text("", encoding="utf-8")

    assert _wait_for(
        lambda: (
            entries := file_indexer.get_index(Path(".")),
            # new file was not added: watcher failed
            all(entry.rel != "new_file.py" for entry in entries)
            # but the existing index is still intact
            and all(entry.rel == "seed.py" for entry in entries),
        )[1]
    )


def test_shutdown_cleans_up_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test.txt").write_text("", encoding="utf-8")
    file_indexer = FileIndexer()
    file_indexer.get_index(Path("."))

    file_indexer.shutdown()
    assert file_indexer.get_index(Path(".")) == []


def test_watcher_is_disabled_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    file_indexer = FileIndexer()
    try:
        baseline_entries = {entry.rel for entry in file_indexer.get_index(Path("."))}
        incremental_before = file_indexer.stats.incremental_updates
        (tmp_path / "file.py").write_text("", encoding="utf-8")

        _assert_index_state_stable(
            file_indexer=file_indexer,
            expected_entries=baseline_entries,
            expected_incremental_updates=incremental_before,
        )
    finally:
        file_indexer.shutdown()


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
        assert any(
            entry.rel == "tracked.py" for entry in file_indexer.get_index(Path("."))
        )

        watcher_enabled = False
        file_indexer.get_index(Path("."))

        expected_entries = {entry.rel for entry in file_indexer.get_index(Path("."))}
        incremental_before = file_indexer.stats.incremental_updates
        tracked.unlink()

        _assert_index_state_stable(
            file_indexer=file_indexer,
            expected_entries=expected_entries,
            expected_incremental_updates=incremental_before,
        )
    finally:
        file_indexer.shutdown()


def _current_entries(file_indexer: FileIndexer) -> set[str]:
    return {entry.rel for entry in file_indexer.get_index(Path("."))}


def _assert_created_file_is_not_indexed(
    file_indexer: FileIndexer, tmp_path: Path, filename: str
) -> None:
    expected_entries = _current_entries(file_indexer)
    expected_incremental_updates = file_indexer.stats.incremental_updates
    (tmp_path / filename).write_text("", encoding="utf-8")

    _assert_index_state_stable(
        file_indexer=file_indexer,
        expected_entries=expected_entries,
        expected_incremental_updates=expected_incremental_updates,
    )


def _assert_created_file_is_indexed(
    file_indexer: FileIndexer, tmp_path: Path, filename: str
) -> None:
    incremental_before = file_indexer.stats.incremental_updates
    (tmp_path / filename).write_text("", encoding="utf-8")

    assert _wait_for(lambda: filename in _current_entries(file_indexer))
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
