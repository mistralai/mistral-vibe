from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from tests.skills.registry.conftest import held_by_another_process, make_item
from vibe.core.skills.models import SkillMetadata
from vibe.core.skills.parser import parse_skill_markdown
from vibe.core.skills.registry import _store
from vibe.core.utils import file_lock


@pytest.mark.asyncio
async def test_materialize_writes_parseable_skill() -> None:
    item = make_item(skill_id="abc", name="my_skill", description="does X", body="# Hi")
    dest = await _store.materialize(item, "my-skill")

    assert dest is not None
    assert dest == _store.skill_dir("abc", 1)
    assert await _store.is_materialized("abc", 1)
    frontmatter, body = parse_skill_markdown((dest / "SKILL.md").read_text())
    meta = SkillMetadata.model_validate(frontmatter)
    assert meta.name == "my-skill"
    assert meta.description == "does X"
    assert meta.metadata["source"] == "ai-registry"
    assert meta.metadata["skill_id"] == "abc"
    assert meta.metadata["version"] == "1"
    assert "# Hi" in body


@pytest.mark.asyncio
async def test_materialize_assets_and_exec_bit() -> None:
    encoded = base64.b64encode(b"#!/bin/sh\n").decode()
    item = make_item(
        skill_id="t",
        name="tooling",
        assets={
            "ref/notes.md": {"textContent": "n", "isExecutable": False},
            "bin/run": {"rawContent": encoded, "isExecutable": True},
        },
    )
    dest = await _store.materialize(item, "tooling")
    assert dest is not None
    assert (dest / "ref" / "notes.md").read_text() == "n"
    mode = (dest / "bin" / "run").stat().st_mode
    assert mode & stat.S_IXUSR
    assert not mode & stat.S_IXGRP
    assert not mode & stat.S_IXOTH


@pytest.mark.asyncio
async def test_materialize_empty_body_returns_none() -> None:
    assert await _store.materialize(make_item(body="   "), "x") is None


@pytest.mark.asyncio
async def test_materialize_strips_embedded_frontmatter() -> None:
    body = "---\nname: ignored\ndescription: ignored\n---\n\n# Real"
    dest = await _store.materialize(make_item(skill_id="f", body=body), "skill-one")
    assert dest is not None
    _, markdown = parse_skill_markdown((dest / "SKILL.md").read_text())
    assert "# Real" in markdown
    assert "ignored" not in markdown


@pytest.mark.asyncio
async def test_materialize_preserves_model_invocation_policy() -> None:
    body = (
        "---\nname: ignored\ndescription: ignored\n"
        "disable-model-invocation: true\n---\n\n# Real"
    )
    dest = await _store.materialize(
        make_item(skill_id="explicit-only", body=body), "explicit-only"
    )
    assert dest is not None

    frontmatter, _ = parse_skill_markdown((dest / "SKILL.md").read_text())

    assert frontmatter["disable-model-invocation"] is True


@pytest.mark.asyncio
async def test_materialize_rejects_traversal_assets() -> None:
    item = make_item(
        skill_id="s",
        assets={"../escape.txt": {"textContent": "no", "isExecutable": False}},
    )
    dest = await _store.materialize(item, "safe")
    assert dest is not None
    assert not (dest.parent.parent.parent / "escape.txt").exists()


@pytest.mark.asyncio
async def test_latest_materialized_returns_highest() -> None:
    assert await _store.latest_materialized("v") is None
    await _store.materialize(make_item(skill_id="v", version=1), "v")
    await _store.materialize(make_item(skill_id="v", version=3), "v")
    assert await _store.latest_materialized("v") == 3


@pytest.mark.asyncio
async def test_export_local_strips_registry_frontmatter(tmp_path: Path) -> None:
    await _store.materialize(make_item(skill_id="e", name="exported"), "exported")
    target = tmp_path / "exported"
    await _store.export_local("e", 1, target, "exported")

    frontmatter, _ = parse_skill_markdown((target / "SKILL.md").read_text())
    assert frontmatter["name"] == "exported"
    assert "metadata" not in frontmatter


@pytest.mark.asyncio
async def test_export_local_preserves_model_invocation_policy(tmp_path: Path) -> None:
    body = (
        "---\nname: exported\ndescription: exported\n"
        "disable-model-invocation: true\n---\n\n# Real"
    )
    await _store.materialize(
        make_item(skill_id="explicit-export", name="exported", body=body), "exported"
    )
    target = tmp_path / "exported"

    await _store.export_local("explicit-export", 1, target, "exported")

    frontmatter, _ = parse_skill_markdown((target / "SKILL.md").read_text())
    assert frontmatter["disable-model-invocation"] is True


def test_skill_dir_rejects_unsafe_id() -> None:
    for bad in ("", ".", "..", "../escape", "foo/../bar", "a/b"):
        with pytest.raises(ValueError):
            _store.skill_dir(bad, 1)


@pytest.mark.asyncio
async def test_asset_cannot_overwrite_entrypoint() -> None:
    dest = await _store.materialize(
        make_item(
            skill_id="x",
            name="x",
            body="# real",
            assets={"sub/../SKILL.md": {"textContent": "PWNED", "isExecutable": False}},
        ),
        "x",
    )
    assert dest is not None
    assert "PWNED" not in (dest / "SKILL.md").read_text()


@pytest.mark.asyncio
async def test_asset_dot_path_skipped() -> None:
    dest = await _store.materialize(
        make_item(
            skill_id="d", assets={".": {"textContent": "n", "isExecutable": False}}
        ),
        "d",
    )
    assert dest is not None
    assert (dest / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_a_version_on_disk_is_never_rewritten() -> None:
    dest = await _store.materialize(
        make_item(
            skill_id="r",
            assets={"notes.md": {"textContent": "n", "isExecutable": False}},
        ),
        "r",
    )
    assert dest is not None
    written = (dest / "SKILL.md").stat().st_mtime_ns

    again = await _store.materialize(make_item(skill_id="r", body="# other"), "r")

    assert again == dest
    assert (dest / "SKILL.md").stat().st_mtime_ns == written
    assert (dest / "notes.md").exists()


@pytest.mark.asyncio
async def test_an_empty_body_leaves_the_version_on_disk() -> None:
    dest = await _store.materialize(make_item(skill_id="c", version=1), "c")

    again = await _store.materialize(make_item(skill_id="c", version=1, body="  "), "c")

    assert again == dest
    assert await _store.is_materialized("c", 1)


def test_an_empty_body_never_writes_a_version_evicted_meanwhile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = iter([True, False])
    monkeypatch.setattr(_store, "_is_materialized", lambda *_args: next(stored))

    assert _store._materialize(make_item(skill_id="e", body="  "), "e") is None
    assert not _store.skill_dir("e", 1).exists()


@pytest.mark.asyncio
async def test_a_version_a_broken_write_left_behind_is_written_again() -> None:
    leftover = _store.skill_dir("j", 1)
    leftover.mkdir(parents=True)
    (leftover / "stray.txt").write_text("half a write", encoding="utf-8")

    dest = await _store.materialize(make_item(skill_id="j", version=1), "j")

    assert dest == leftover
    assert await _store.is_materialized("j", 1)
    assert not (leftover / "stray.txt").exists()
    assert sorted(entry.name for entry in leftover.parent.iterdir()) == [
        ".1.lock",
        ".1.used",
        "1",
    ]


@pytest.mark.asyncio
async def test_a_leftover_that_cannot_be_deleted_is_moved_out_of_the_way_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leftover = _store.skill_dir("j", 1)
    leftover.mkdir(parents=True)
    (leftover / "stray.txt").write_text("half a write", encoding="utf-8")
    monkeypatch.setattr(
        _store, "shutil", SimpleNamespace(rmtree=lambda *_args, **_kwargs: None)
    )

    dest = await _store.materialize(make_item(skill_id="j", version=1), "j")

    assert dest == leftover
    assert await _store.is_materialized("j", 1)
    assert not (leftover / "stray.txt").exists()


@pytest.mark.asyncio
async def test_a_failed_write_leaves_nothing_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store.materialize(make_item(skill_id="p", version=1, body="# good"), "p")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_store, "_write_assets", boom)
    with pytest.raises(OSError):
        await _store.materialize(make_item(skill_id="p", version=2, body="# new"), "p")

    assert not await _store.is_materialized("p", 2)
    assert sorted(
        entry.name for entry in _store.skill_dir("p", 1).parent.iterdir()
    ) == [".1.lock", ".1.used", ".2.lock", "1"]
    _, md = parse_skill_markdown((_store.skill_dir("p", 1) / "SKILL.md").read_text())
    assert "# good" in md


def test_two_writers_of_one_version_both_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquire = file_lock.acquire_file_lock
    second_waits = threading.Event()
    attempts: list[object] = []

    def acquire_counting(file: Any, *, blocking: bool = False) -> None:
        attempts.append(file)
        if len(attempts) == 2:
            second_waits.set()
        acquire(file, blocking=blocking)

    write_assets = _store._write_assets
    writing = threading.Event()
    writes: list[Path] = []

    def write_while_the_second_waits(dest: Path, item: Any) -> None:
        write_assets(dest, item)
        writes.append(dest)
        writing.set()
        assert second_waits.wait(timeout=10)

    monkeypatch.setattr(file_lock, "acquire_file_lock", acquire_counting)
    monkeypatch.setattr(_store, "_write_assets", write_while_the_second_waits)
    item = make_item(skill_id="race", version=3, body="# shared")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_store._materialize, item, "race")
        assert writing.wait(timeout=10)
        second = pool.submit(_store._materialize, item, "race")
        written = [first.result(timeout=10), second.result(timeout=10)]

    dest = _store.skill_dir("race", 3)
    assert written == [dest, dest]
    assert len(writes) == 1
    assert sorted(entry.name for entry in dest.parent.iterdir()) == [
        ".3.lock",
        ".3.used",
        "3",
    ]
    _, md = parse_skill_markdown((dest / "SKILL.md").read_text())
    assert "# shared" in md


def test_a_writer_gives_up_on_a_version_another_process_keeps_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_store, "STORE_LOCK_WAIT", timedelta(milliseconds=200))
    lock = _store.skill_dir("cid", 1).parent / ".1.lock"
    lock.parent.mkdir(parents=True)

    with lock.open("a+b") as holder:
        file_lock.acquire_file_lock(holder)
        with pytest.raises(BlockingIOError, match="could not lock version 1 of cid"):
            _store._materialize(make_item(skill_id="cid", version=1), "cid")

    assert not _store.is_materialized_sync("cid", 1)


def test_storing_a_version_again_marks_it_used_under_its_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = make_item(skill_id="cid", version=1)
    _store._materialize(item, "cid")
    marker = _store.used_marker("cid", 1)
    long_ago = time.time() - 40 * 24 * 3600
    os.utime(marker, (long_ago, long_ago))
    lock = _store.skill_dir("cid", 1).parent / ".1.lock"
    held: list[bool] = []
    mark_used = _store.mark_used

    def mark_noting_the_lock(skill_id: str, version: int) -> None:
        with lock.open("a+b") as other:
            try:
                file_lock.acquire_file_lock(other)
            except BlockingIOError:
                held.append(True)
            else:
                file_lock.release_file_lock(other)
                held.append(False)
        mark_used(skill_id, version)

    monkeypatch.setattr(_store, "mark_used", mark_noting_the_lock)

    _store._materialize(item, "cid")

    assert held == [True]
    assert marker.stat().st_mtime > time.time() - 60


def test_a_held_lock_refuses_another_handle_until_it_is_released(
    tmp_path: Path,
) -> None:
    path = tmp_path / "held.lock"

    with _store.locked(path), path.open("a+b") as other:
        with pytest.raises(BlockingIOError):
            file_lock.acquire_file_lock(other)

    with path.open("a+b") as other:
        file_lock.acquire_file_lock(other)
        file_lock.release_file_lock(other)
    assert path.exists()


@pytest.mark.parametrize(
    ("description", "named"),
    [(None, "{path}"), ("version 3 of cid", "version 3 of cid")],
)
def test_a_wait_that_gives_up_names_what_it_locks(
    tmp_path: Path, description: str | None, named: str
) -> None:
    path = tmp_path / "busy.lock"

    with (
        held_by_another_process(path),
        pytest.raises(BlockingIOError) as raised,
        _store.locked(path, wait=timedelta(0), description=description),
    ):
        pass

    assert str(raised.value) == f"could not lock {named.format(path=path)}"


def test_a_bounded_wait_gives_up_once_its_time_has_passed(tmp_path: Path) -> None:
    path = tmp_path / "held.lock"

    with path.open("a+b") as holder:
        file_lock.acquire_file_lock(holder)
        started = time.monotonic()
        with (
            pytest.raises(BlockingIOError),
            _store.locked(path, wait=timedelta(milliseconds=200)),
        ):
            pass
        waited = time.monotonic() - started
        file_lock.release_file_lock(holder)

    assert waited >= 0.2


def test_a_bounded_wait_takes_the_lock_once_its_holder_lets_go(tmp_path: Path) -> None:
    path = tmp_path / "held.lock"
    holder = path.open("a+b")
    file_lock.acquire_file_lock(holder)

    def let_go() -> None:
        file_lock.release_file_lock(holder)
        holder.close()

    timer = threading.Timer(0.2, let_go)
    timer.start()
    try:
        with _store.locked(path, wait=timedelta(seconds=5)):
            taken = True
    finally:
        timer.join()

    assert taken


def test_a_zero_wait_tries_the_lock_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tries: list[object] = []

    def refused(file: object, **_kwargs: object) -> None:
        tries.append(file)
        raise BlockingIOError

    monkeypatch.setattr(file_lock, "acquire_file_lock", refused)

    with (
        pytest.raises(BlockingIOError),
        _store.locked(tmp_path / "busy.lock", wait=timedelta(0)),
    ):
        pass

    assert len(tries) == 1
