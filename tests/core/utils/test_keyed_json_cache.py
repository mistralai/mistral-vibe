from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vibe.core.utils.keyed_json_cache import load_keyed_entry, store_keyed_entry

_TTL = 60


def _parse(payload: Any) -> dict[str, int] | None:
    if not isinstance(payload, dict) or "value" not in payload:
        return None
    return payload


def test_store_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"

    store_keyed_entry(path, "user-a", {"value": 1})

    assert load_keyed_entry(path, "user-a", ttl_seconds=_TTL, parse=_parse) == {
        "value": 1
    }


def test_entries_are_isolated_per_key(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    store_keyed_entry(path, "user-a", {"value": 1})
    store_keyed_entry(path, "user-b", {"value": 2})

    assert load_keyed_entry(path, "user-b", ttl_seconds=_TTL, parse=_parse) == {
        "value": 2
    }
    assert load_keyed_entry(path, "user-a", ttl_seconds=_TTL, parse=_parse) == {
        "value": 1
    }


def test_stale_entry_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    store_keyed_entry(path, "user-a", {"value": 1})
    entries = json.loads(path.read_text())
    entries["user-a"]["stored_at_timestamp"] -= _TTL + 1
    path.write_text(json.dumps(entries))

    assert load_keyed_entry(path, "user-a", ttl_seconds=_TTL, parse=_parse) is None


def test_missing_key_missing_file_and_corrupt_file_all_return_none(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cache.json"

    assert load_keyed_entry(path, "nobody", ttl_seconds=_TTL, parse=_parse) is None

    store_keyed_entry(path, "user-a", {"value": 1})
    assert load_keyed_entry(path, "nobody", ttl_seconds=_TTL, parse=_parse) is None

    path.write_text("{ not valid json")
    assert load_keyed_entry(path, "user-a", ttl_seconds=_TTL, parse=_parse) is None


def test_parse_rejecting_the_payload_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    store_keyed_entry(path, "user-a", {"not_value": 1})

    assert load_keyed_entry(path, "user-a", ttl_seconds=_TTL, parse=_parse) is None


def test_store_overwrites_the_previous_entry(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    store_keyed_entry(path, "user-a", {"value": 1})
    store_keyed_entry(path, "user-a", {"value": 2})

    assert load_keyed_entry(path, "user-a", ttl_seconds=_TTL, parse=_parse) == {
        "value": 2
    }


def test_store_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "cache.json"

    store_keyed_entry(path, "user-a", {"value": 1})

    assert load_keyed_entry(path, "user-a", ttl_seconds=_TTL, parse=_parse) == {
        "value": 1
    }
