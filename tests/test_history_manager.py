from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.cli.history_manager import HistoryManager


def submit(manager: HistoryManager, text: str) -> None:
    manager.add(text)
    manager.reset_navigation()
    manager.persist(text)


def test_history_manager_normalizes_loaded_entries_like_numbers_to_strings(
    tmp_path: Path,
) -> None:
    # ideally, we would not use real I/O; but this test is a quick bugfix, thus it
    # does not intend to refactor the HistoryManager
    history_file = tmp_path / "history.jsonl"
    history_entries = ["hello", 123]
    history_file.write_text(
        "\n".join(json.dumps(entry) for entry in history_entries) + "\n",
        encoding="utf-8",
    )
    manager = HistoryManager(history_file)

    result = manager.get_previous(current_input="")

    assert result == "123"


def test_history_manager_retains_a_fixed_number_of_entries(tmp_path: Path) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file, max_entries=3)

    submit(manager, "first")
    submit(manager, "second")
    submit(manager, "third")
    submit(manager, "fourth")

    reloaded = HistoryManager(history_file)

    assert reloaded.get_previous(current_input="") == "fourth"
    assert reloaded.get_previous(current_input="") == "third"
    assert reloaded.get_previous(current_input="") == "second"
    # "first" is not proposed as we defined number of entries to 3
    assert reloaded.get_previous(current_input="") is None


def test_history_manager_filters_invalid_and_duplicated_entries(tmp_path: Path) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file, max_entries=5)
    submit(manager, "")  # empty
    submit(manager, "   ")  # is trimmed
    submit(manager, "first")
    submit(manager, "second")
    submit(manager, "second")  # duplicate
    submit(manager, "third")

    reloaded = HistoryManager(history_file)

    assert reloaded.get_previous(current_input="") == "third"
    assert reloaded.get_previous(current_input="") == "second"
    assert reloaded.get_previous(current_input="") == "first"
    assert reloaded.get_previous(current_input="") is None
    assert reloaded.get_previous(current_input="") is None


def test_history_manager_stores_slash_prefixed_entries(tmp_path: Path) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file, max_entries=5)
    submit(manager, "first")
    submit(manager, "/tool_call arg1 arg2")

    reloaded = HistoryManager(history_file)

    assert reloaded.get_previous(current_input="") == "/tool_call arg1 arg2"
    assert reloaded.get_previous(current_input="") == "first"
    assert reloaded.get_previous(current_input="") is None


def test_history_manager_keeps_entries_when_reload_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file)
    submit(manager, "first")

    def raise_os_error(*args: object, **kwargs: object) -> None:
        raise OSError

    monkeypatch.setattr("vibe.cli.history_manager.read_safe", raise_os_error)

    submit(manager, "second")

    assert manager.get_previous(current_input="") == "second"
    assert manager.get_previous(current_input="") == "first"


def test_history_manager_merges_other_sessions_entries_on_persist(
    tmp_path: Path,
) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file, max_entries=2)
    submit(manager, "a")
    submit(manager, "b")

    other = HistoryManager(history_file, max_entries=10)
    submit(other, "c")
    submit(other, "d")
    submit(other, "e")

    assert manager.get_previous(current_input="") == "b"
    submit(manager, "e")

    assert manager.get_previous(current_input="") == "e"
    assert manager.get_previous(current_input="") == "d"
    assert manager.get_previous(current_input="") is None


def test_history_manager_persists_pending_entries_in_submission_order(
    tmp_path: Path,
) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file)

    manager.add("first")
    manager.reset_navigation()
    manager.add("second")
    manager.reset_navigation()
    manager.persist("second")
    manager.persist("first")

    reloaded = HistoryManager(history_file)

    assert reloaded.get_previous(current_input="") == "second"
    assert reloaded.get_previous(current_input="") == "first"
    assert reloaded.get_previous(current_input="") is None


def test_history_manager_keeps_pending_entries_when_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file)
    original_write_entries = manager._write_entries
    calls = 0

    def fail_once(entries: list[str]) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return False
        return original_write_entries(entries)

    monkeypatch.setattr(manager, "_write_entries", fail_once)

    manager.add("first")
    manager.persist("first")
    manager.persist("first")

    reloaded = HistoryManager(history_file)

    assert reloaded.get_previous(current_input="") == "first"
    assert reloaded.get_previous(current_input="") is None


def test_history_manager_clamps_navigation_after_entries_shrink(tmp_path: Path) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file)

    manager.add("first")
    manager.add("second")
    manager.add("third")

    assert manager.get_previous(current_input="") == "third"
    manager._entries = ["only"]

    assert manager.get_previous(current_input="") == "only"
    assert manager.get_previous(current_input="") is None


def test_history_manager_allows_navigation_round_trip(tmp_path: Path) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file)

    manager.add("alpha")
    manager.add("beta")

    assert manager.get_previous(current_input="typed") == "beta"
    assert manager.get_previous(current_input="typed") == "alpha"
    assert manager.get_next() == "beta"
    assert manager.get_next() == "typed"
    assert manager.get_next() is None


def test_history_manager_preserves_original_draft_during_navigation(
    tmp_path: Path,
) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file)

    manager.add("foo")
    manager.add("bar")
    manager.add("fizz")

    assert manager.get_previous(current_input="draft") == "fizz"
    assert manager.get_previous(current_input="overwritten draft") == "bar"
    assert manager.get_next() == "fizz"
    assert manager.get_next() == "draft"
