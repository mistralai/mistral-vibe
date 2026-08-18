from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vibe.core.git.worktree.record import (
    CLAIMS_DIR_NAME,
    RECORD_FILENAME,
    WorktreeClaim,
    WorktreeRecord,
    WorktreeRecordError,
    _claims_root,
    managed_bucket_name,
)
from vibe.core.paths import WORKTREES_DIR

_BUCKET = "repo-abc123456789"


def _claim(name: str = "feature") -> WorktreeClaim:
    return WorktreeClaim(bucket=_BUCKET, name=name)


def _record(name: str = "feature", repo_root: Path = Path("/repo")) -> WorktreeRecord:
    return WorktreeRecord.new(
        name=name, branch=f"vibe/{name}", repo_root=repo_root, branch_created=True
    )


def test_record_round_trips(tmp_path: Path) -> None:
    record = _record(repo_root=tmp_path)

    _claim().write(record)
    loaded = _claim().read()

    assert loaded is not None
    assert loaded.name == "feature"
    assert loaded.branch == "vibe/feature"
    assert loaded.repo_root == tmp_path
    assert loaded.branch_created is True
    assert loaded.base_commit is None
    assert loaded.claimed_at == record.claimed_at


def test_record_keeps_base_commit(tmp_path: Path) -> None:
    record = _record(repo_root=tmp_path).model_copy(update={"base_commit": "abc123"})

    _claim().write(record)

    loaded = _claim().read()
    assert loaded is not None
    assert loaded.base_commit == "abc123"


def test_record_ignores_unknown_future_fields(tmp_path: Path) -> None:
    _claim().write(_record(repo_root=tmp_path))
    target = _claim().directory / RECORD_FILENAME
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["arrived_in_a_later_version"] = True
    target.write_text(json.dumps(payload), encoding="utf-8")

    assert _claim().read() is not None


def test_missing_record_reads_as_absent() -> None:
    assert _claim("never-written").read() is None


@pytest.mark.parametrize("payload", ["{not json", '{"name": "feature"}'])
def test_unreadable_record_reads_as_absent(tmp_path: Path, payload: str) -> None:
    _claim().write(_record(repo_root=tmp_path))
    target = _claim().directory / RECORD_FILENAME
    target.write_text(payload, encoding="utf-8")

    assert _claim().read() is None
    # The file is a breadcrumb for whoever debugs this; never delete it.
    assert target.exists()


def test_failed_write_leaves_the_previous_record_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claim().write(_record(repo_root=tmp_path))
    directory = _claim().directory
    original = (directory / RECORD_FILENAME).read_text(encoding="utf-8")

    def fail(source: object, target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        _claim().write(
            _record(repo_root=tmp_path).model_copy(update={"base_commit": "abc123"})
        )

    assert (directory / RECORD_FILENAME).read_text(encoding="utf-8") == original
    assert list(directory.glob("*.tmp")) == []


def test_delete_record_removes_empty_parents(tmp_path: Path) -> None:
    _claim().write(_record(repo_root=tmp_path))

    _claim().delete()

    assert not _claim().directory.exists()
    assert not (_claims_root() / _BUCKET).exists()


def test_the_last_holder_out_clears_a_deleted_claim(tmp_path: Path) -> None:
    _claim().write(_record(repo_root=tmp_path))
    _claim().add_holder("session-a")

    # The delete cannot rmdir past a live holder, and the sweep never revisits a
    # claim whose record is gone - so without this the skeleton outlives both.
    _claim().delete()
    _claim().remove_holder("session-a")

    assert not _claim().directory.exists()
    assert not (_claims_root() / _BUCKET).exists()


def test_a_departing_holder_keeps_a_live_claim(tmp_path: Path) -> None:
    _claim().write(_record(repo_root=tmp_path))
    _claim().add_holder("session-a")

    _claim().remove_holder("session-a")

    # Nobody is in it, but the record still says it is ours.
    assert _claim().read() is not None
    assert _claim().directory.exists()


def test_delete_record_keeps_a_bucket_with_other_claims(tmp_path: Path) -> None:
    _claim("first").write(_record("first", repo_root=tmp_path))
    _claim("second").write(_record("second", repo_root=tmp_path))

    _claim("first").delete()

    assert _claim("second").read() is not None


def test_delete_record_is_idempotent() -> None:
    _claim("never-written").delete()


@pytest.mark.parametrize(
    "session_id",
    ["../escape", "../../escape", "nested/escape", "/absolute", "", "..", "."],
)
def test_holder_ids_that_could_escape_are_rejected(session_id: str) -> None:
    with pytest.raises(WorktreeRecordError):
        _claim().add_holder(session_id)
    with pytest.raises(WorktreeRecordError):
        _claim().remove_holder(session_id)


def test_removing_a_holder_cannot_unlink_outside_the_holders_directory(
    tmp_path: Path,
) -> None:
    _claim().write(_record(repo_root=tmp_path))
    victim = _claim().directory / RECORD_FILENAME

    with pytest.raises(WorktreeRecordError):
        _claim().remove_holder(f"../{RECORD_FILENAME}")

    assert victim.exists()


def test_holders_round_trip(tmp_path: Path) -> None:
    _claim().write(_record(repo_root=tmp_path))

    _claim().add_holder("session-a")
    _claim().add_holder("session-b")

    assert _claim().holders() == {"session-a", "session-b"}

    _claim().remove_holder("session-a")

    assert _claim().holders() == {"session-b"}


def test_claims_root_lives_under_the_managed_worktree_root() -> None:
    assert _claims_root().is_relative_to(WORKTREES_DIR.path.resolve())


def test_bucket_names_cannot_collide_with_the_claims_directory() -> None:
    bucket = managed_bucket_name(Path("/repo") / CLAIMS_DIR_NAME, Path("/repo/.git"))

    assert bucket != CLAIMS_DIR_NAME
    assert bucket.startswith(f"{CLAIMS_DIR_NAME}-")


def test_locate_worktree_finds_bucket_and_name() -> None:
    root = WORKTREES_DIR.path.resolve() / _BUCKET / "feature"

    assert WorktreeClaim.locate(root) == _claim()


def test_locate_worktree_finds_the_root_from_a_subdirectory() -> None:
    inside = WORKTREES_DIR.path.resolve() / _BUCKET / "feature" / "pkg" / "src"

    assert WorktreeClaim.locate(inside) == _claim()


@pytest.mark.parametrize(
    "relative", [Path(_BUCKET), Path(CLAIMS_DIR_NAME) / _BUCKET / "feature"]
)
def test_locate_worktree_rejects_non_worktree_paths(relative: Path) -> None:
    assert WorktreeClaim.locate(WORKTREES_DIR.path.resolve() / relative) is None


def test_locate_worktree_rejects_paths_outside_the_managed_root(tmp_path: Path) -> None:
    assert WorktreeClaim.locate(tmp_path / "elsewhere" / "feature") is None
