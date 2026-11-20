from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import signal

import pytest

from tests.stubs.fake_filesystem import FakeFilesystem
from vibe.core.checkpoints import DiskFilesystem, FileState, FileStore


class TestFileStoreRead:
    def test_read_present_file_returns_its_bytes(self) -> None:
        store = FileStore(FakeFilesystem({"a.txt": b"hello"}))
        assert store.read("a.txt") == FileState(b"hello")

    def test_read_missing_file_returns_absent(self) -> None:
        store = FileStore(FakeFilesystem())
        assert store.read("missing.txt") == FileState.absent()

    def test_read_unreadable_file_raises(self) -> None:
        fs = FakeFilesystem({"a.txt": b"hello"})
        fs.fail_reads.add("a.txt")
        store = FileStore(fs)
        with pytest.raises(OSError):
            store.read("a.txt")


class TestFileStoreApply:
    def test_writes_present_states(self) -> None:
        fs = FakeFilesystem()
        store = FileStore(fs)
        errors, restored = store.apply({"a.txt": FileState(b"new")})
        assert errors == []
        assert restored == ["a.txt"]
        assert fs.files["a.txt"] == b"new"

    def test_deletes_absent_state_when_file_exists(self) -> None:
        fs = FakeFilesystem({"a.txt": b"old"})
        store = FileStore(fs)
        errors, restored = store.apply({"a.txt": FileState.absent()})
        assert errors == []
        assert restored == ["a.txt"]
        assert "a.txt" not in fs.files

    def test_deleting_already_absent_file_is_a_noop(self) -> None:
        fs = FakeFilesystem()
        store = FileStore(fs)
        errors, restored = store.apply({"gone.txt": FileState.absent()})
        assert errors == []
        assert restored == []

    def test_skips_write_when_content_already_matches(self) -> None:
        fs = FakeFilesystem({"a.txt": b"same"})
        store = FileStore(fs)
        errors, restored = store.apply({"a.txt": FileState(b"same")})
        assert errors == []
        assert restored == []

    def test_reports_error_when_write_fails(self) -> None:
        fs = FakeFilesystem()
        fs.fail_writes.add("a.txt")
        store = FileStore(fs)
        errors, restored = store.apply({"a.txt": FileState(b"new")})
        assert restored == []
        assert errors == ["Failed to restore file: a.txt"]

    def test_reports_error_when_delete_fails(self) -> None:
        fs = FakeFilesystem({"a.txt": b"old"})
        fs.fail_removes.add("a.txt")
        store = FileStore(fs)
        errors, restored = store.apply({"a.txt": FileState.absent()})
        assert restored == []
        assert errors == ["Failed to delete file: a.txt"]

    def test_aggregates_across_a_mixed_plan(self) -> None:
        fs = FakeFilesystem({"keep.txt": b"v", "del.txt": b"x"})
        fs.fail_writes.add("boom.txt")
        store = FileStore(fs)
        errors, restored = store.apply({
            "write.txt": FileState(b"created"),
            "del.txt": FileState.absent(),
            "keep.txt": FileState(b"v"),
            "boom.txt": FileState(b"nope"),
        })
        assert set(restored) == {"write.txt", "del.txt"}
        assert errors == ["Failed to restore file: boom.txt"]
        assert fs.files["write.txt"] == b"created"
        assert "del.txt" not in fs.files


@contextmanager
def _file_size_limit(limit: int) -> Iterator[None]:
    resource = pytest.importorskip("resource")
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    previous_handler = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
        signal.signal(signal.SIGXFSZ, previous_handler)


class TestDiskFilesystem:
    def test_write_then_read_round_trips_and_creates_parents(
        self, tmp_path: Path
    ) -> None:
        fs = DiskFilesystem()
        target = tmp_path / "nested" / "dir" / "a.txt"
        fs.write_bytes(str(target), b"payload")
        assert fs.read_bytes(str(target)) == b"payload"
        assert fs.exists(str(target))

    def test_read_missing_returns_none(self, tmp_path: Path) -> None:
        fs = DiskFilesystem()
        assert fs.read_bytes(str(tmp_path / "missing.txt")) is None

    def test_read_unreadable_path_raises_instead_of_absent(
        self, tmp_path: Path
    ) -> None:
        # A path that exists but cannot be read as a file (here, a directory)
        # must raise rather than be silently reported as absent.
        fs = DiskFilesystem()
        with pytest.raises(OSError):
            fs.read_bytes(str(tmp_path))

    def test_remove_deletes_the_file(self, tmp_path: Path) -> None:
        fs = DiskFilesystem()
        target = tmp_path / "a.txt"
        target.write_bytes(b"x")
        fs.remove(str(target))
        assert not target.exists()

    def test_failed_write_keeps_the_previous_content(self, tmp_path: Path) -> None:
        fs = DiskFilesystem()
        target = tmp_path / "a.txt"
        target.write_bytes(b"x = 1\n")
        with _file_size_limit(16), pytest.raises(OSError):
            fs.write_bytes(str(target), b"y" * 64)
        assert target.read_bytes() == b"x = 1\n"
        assert os.listdir(tmp_path) == ["a.txt"]

    def test_failed_write_of_a_new_file_leaves_nothing(self, tmp_path: Path) -> None:
        fs = DiskFilesystem()
        target = tmp_path / "a.txt"
        with _file_size_limit(16), pytest.raises(OSError):
            fs.write_bytes(str(target), b"y" * 64)
        assert os.listdir(tmp_path) == []

    def test_write_replaces_a_file_with_a_maximum_length_name(
        self, tmp_path: Path
    ) -> None:
        fs = DiskFilesystem()
        target = tmp_path / ("a" * 255)
        target.write_bytes(b"old")
        fs.write_bytes(str(target), b"new")
        assert target.read_bytes() == b"new"

    def test_write_goes_through_a_symlink(self, tmp_path: Path) -> None:
        fs = DiskFilesystem()
        real = tmp_path / "real.txt"
        real.write_bytes(b"old")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        fs.write_bytes(str(link), b"new")
        assert link.is_symlink()
        assert real.read_bytes() == b"new"

    def test_write_keeps_the_existing_mode(self, tmp_path: Path) -> None:
        fs = DiskFilesystem()
        target = tmp_path / "run.sh"
        target.write_bytes(b"old")
        target.chmod(0o751)
        fs.write_bytes(str(target), b"new")
        assert target.stat().st_mode & 0o777 == 0o751
