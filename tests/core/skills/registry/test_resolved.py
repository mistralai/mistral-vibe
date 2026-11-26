from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
import threading
from typing import Any

import pytest
import respx

from tests.skills.registry.conftest import catalog_lists, clean_up, make_item
from vibe.core.skills.registry import _records, _resolved, _store
from vibe.core.utils import file_lock
from vibe.utils.cache_store import FileSystemCacheStore
from vibe.utils.vibe_home import get_vibe_home


def test_what_other_processes_write_to_the_shared_cache_never_touches_the_record() -> (
    None
):
    _resolved.record({("cid", "stable"): 2, ("other", "beta"): 5})
    FileSystemCacheStore().write_section("update_check", {"last": 1})
    (get_vibe_home() / "cache.toml").write_text("[update_check\nlast = ", "utf-8")

    assert _resolved.recorded_versions() == {("cid", 2), ("other", 5)}
    assert _resolved.get("cid", "stable") == 2


def test_two_processes_recording_at_once_keep_each_other_s_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read = _resolved._read
    first_read = threading.Event()
    second_tried = threading.Event()
    acquire = file_lock.acquire_file_lock

    def acquire_watching(file: Any, *, blocking: bool = False) -> None:
        try:
            acquire(file, blocking=blocking)
        except BlockingIOError:
            if first_read.is_set():
                second_tried.set()
            raise

    def read_then_let_the_second_in() -> dict[str, int]:
        records = read()
        if not first_read.is_set():
            first_read.set()
            second_tried.wait(timeout=1)
        return records

    monkeypatch.setattr(file_lock, "acquire_file_lock", acquire_watching)
    monkeypatch.setattr(_resolved, "_read", read_then_let_the_second_in)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_resolved.record, {("a", "stable"): 1})
        assert first_read.wait(timeout=10)
        second = pool.submit(_resolved.record, {("b", "stable"): 2})
        first.result(timeout=30)
        second.result(timeout=30)

    assert read() == {"a@stable": 1, "b@stable": 2}


def test_a_record_that_cannot_be_replaced_stays_whole_and_leaves_nothing_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _resolved.record({("cid", "stable"): 2})

    def full(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_records.os, "replace", full)
    _resolved.record({("cid", "stable"): 3, ("other", "beta"): 5})

    assert _resolved.recorded_versions() == {("cid", 2)}
    assert sorted(path.name for path in _resolved.record_path().parent.iterdir()) == [
        "resolved-aliases.json",
        "resolved-aliases.json.lock",
    ]


def test_until_the_record_exists_what_the_shared_cache_holds_stands_in_for_it() -> None:
    FileSystemCacheStore().write_section("registry_resolved_aliases", {"cid@stable": 2})

    assert _resolved.get("cid", "stable") == 2

    _resolved.record({("other", "beta"): 5})
    FileSystemCacheStore().write_section("registry_resolved_aliases", {"cid@stable": 9})

    assert _resolved.recorded_versions() == {("cid", 2), ("other", 5)}


@pytest.mark.parametrize(
    ("file", "text"),
    [("resolved-aliases.json", "{not json"), ("cache.toml", "[registry_resolved")],
)
def test_a_record_that_cannot_be_read_is_never_written_over(
    file: str, text: str
) -> None:
    target = (
        _resolved.record_path()
        if file == "resolved-aliases.json"
        else get_vibe_home() / "cache.toml"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")

    _resolved.record({("cid", "stable"): 2})

    with pytest.raises(ValueError):
        _resolved.recorded_versions()
    assert _resolved.get("cid", "stable") is None
    assert target.read_text(encoding="utf-8") == text


@pytest.mark.asyncio
async def test_a_cleanup_that_cannot_read_the_record_evicts_nothing(
    respx_mock: respx.MockRouter,
) -> None:
    long_ago = (datetime.now(UTC) - timedelta(days=40)).timestamp()
    for version in (1, 2):
        assert _store._materialize(make_item(skill_id="cid", version=version), "sk")
        os.utime(_store.used_marker("cid", version), (long_ago, long_ago))
    _resolved.record_path().write_text("{not json", encoding="utf-8")
    catalog_lists(respx_mock, cid=2)

    with pytest.raises(ValueError):
        await clean_up()

    assert _store.is_materialized_sync("cid", 1)
