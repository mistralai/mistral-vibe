from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.session.session_index import (
    INDEX_FILENAME,
    SessionIndex,
    clear_session_index_registry,
    session_index_for,
)
from vibe.core.session.session_loader import SessionLoader


@pytest.fixture(autouse=True)
def _clear_registry():
    clear_session_index_registry()
    yield
    clear_session_index_registry()


@pytest.fixture
def save_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sessions"
    d.mkdir()
    return d


def _make_session(
    save_dir: Path,
    name: str,
    session_id: str,
    cwd: str = "/home/user/project",
    *,
    title: str | None = None,
    end_time: str | None = "2024-01-01T12:00:00Z",
    with_messages: bool = True,
) -> Path:
    folder = save_dir / f"session_{name}"
    folder.mkdir()
    if with_messages:
        (folder / "messages.jsonl").write_text('{"role": "user", "content": "hi"}\n')
    metadata = {
        "session_id": session_id,
        "environment": {"working_directory": cwd},
        "title": title,
        "end_time": end_time,
    }
    (folder / "meta.json").write_text(json.dumps(metadata))
    return folder


def test_cold_build_returns_sessions_and_persists_index(save_dir: Path) -> None:
    _make_session(save_dir, "a", "aaaa-1111")
    _make_session(save_dir, "b", "bbbb-2222")

    index = SessionIndex(save_dir, "session")
    result = index.list()

    assert {s["session_id"] for s in result} == {"aaaa-1111", "bbbb-2222"}
    assert (save_dir / INDEX_FILENAME).is_file()


def test_end_time_converted_to_utc(save_dir: Path) -> None:
    _make_session(save_dir, "a", "aaaa", end_time="2024-01-01T12:00:00+02:00")
    result = SessionIndex(save_dir, "session").list()
    assert result[0]["end_time"] == "2024-01-01T10:00:00+00:00"


def test_filters_by_cwd(save_dir: Path) -> None:
    _make_session(save_dir, "a", "a", cwd="/p1")
    _make_session(save_dir, "b", "b", cwd="/p2")
    _make_session(save_dir, "c", "c", cwd="/p1")

    result = SessionIndex(save_dir, "session").list(cwd="/p1")

    assert {s["session_id"] for s in result} == {"a", "c"}


def test_skips_missing_messages_and_missing_session_id(save_dir: Path) -> None:
    _make_session(save_dir, "valid", "valid-id")
    _make_session(save_dir, "nomsg", "nomsg-id", with_messages=False)

    noid = save_dir / "session_noid"
    noid.mkdir()
    (noid / "messages.jsonl").write_text('{"role": "user", "content": "x"}\n')
    (noid / "meta.json").write_text('{"environment": {"working_directory": "/x"}}')

    result = SessionIndex(save_dir, "session").list()

    assert {s["session_id"] for s in result} == {"valid-id"}


def test_nonexistent_save_dir_returns_empty_without_creating(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    index = SessionIndex(missing, "session")
    assert index.list() == []
    assert not missing.exists()


def test_incremental_add_and_remove(save_dir: Path) -> None:
    _make_session(save_dir, "a", "a")
    index = SessionIndex(save_dir, "session")
    assert {s["session_id"] for s in index.list()} == {"a"}

    _make_session(save_dir, "b", "b")
    assert {s["session_id"] for s in index.list()} == {"a", "b"}

    import shutil

    shutil.rmtree(save_dir / "session_a")
    assert {s["session_id"] for s in index.list()} == {"b"}


def test_persisted_index_serves_without_rereading_unchanged_meta(
    save_dir: Path,
) -> None:
    folder = _make_session(save_dir, "a", "aaaa", title="Original")
    meta = folder / "meta.json"
    mtime_ns = meta.stat().st_mtime_ns

    SessionIndex(save_dir, "session").list()

    # Corrupt meta content but restore its mtime so reconcile treats it as unchanged.
    meta.write_text("not valid json")
    os.utime(meta, ns=(mtime_ns, mtime_ns))

    fresh = SessionIndex(save_dir, "session")
    result = fresh.list()

    assert len(result) == 1
    assert result[0]["title"] == "Original"


def test_changed_meta_is_reread(save_dir: Path) -> None:
    folder = _make_session(save_dir, "a", "aaaa", title="Original")
    index = SessionIndex(save_dir, "session")
    assert index.list()[0]["title"] == "Original"

    metadata = json.loads((folder / "meta.json").read_text())
    metadata["title"] = "Renamed"
    (folder / "meta.json").write_text(json.dumps(metadata))
    st = (folder / "meta.json").stat()
    os.utime(folder / "meta.json", (st.st_atime, st.st_mtime + 10))

    assert index.list()[0]["title"] == "Renamed"


def test_empty_transcript_with_nonzero_total_is_excluded(save_dir: Path) -> None:
    folder = save_dir / "session_a"
    folder.mkdir()
    (folder / "messages.jsonl").write_text("")
    (folder / "meta.json").write_text(
        json.dumps({"session_id": "aaaa", "total_messages": 2})
    )

    assert SessionIndex(save_dir, "session").list() == []


def test_empty_transcript_with_zero_total_is_listed(save_dir: Path) -> None:
    folder = save_dir / "session_a"
    folder.mkdir()
    (folder / "messages.jsonl").write_text("")
    (folder / "meta.json").write_text(
        json.dumps({"session_id": "aaaa", "total_messages": 0})
    )

    result = SessionIndex(save_dir, "session").list()

    assert [s["session_id"] for s in result] == ["aaaa"]


def test_corrupt_index_file_triggers_full_rebuild(save_dir: Path) -> None:
    _make_session(save_dir, "a", "aaaa")
    (save_dir / INDEX_FILENAME).write_text("{ this is not valid json")

    result = SessionIndex(save_dir, "session").list()

    assert {s["session_id"] for s in result} == {"aaaa"}


def test_session_loader_delegates_to_index(save_dir: Path) -> None:
    _make_session(save_dir, "a", "aaaa", cwd="/p1")
    config = SessionLoggingConfig(
        save_dir=str(save_dir), session_prefix="session", enabled=True
    )

    result = SessionLoader.list_sessions(config, cwd="/p1")

    assert [s["session_id"] for s in result] == ["aaaa"]
    assert session_index_for(config) is session_index_for(config)
