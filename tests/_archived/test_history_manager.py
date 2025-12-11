from __future__ import annotations

import json
from pathlib import Path

from vibe.cli.history_manager import HistoryManager


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

    result = manager.get_previous(current_input="", prefix="1")

    assert result == "123"


def test_history_manager_retains_a_fixed_number_of_entries(tmp_path: Path) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file, max_entries=3)

    manager.add("first")
    manager.add("second")
    manager.add("third")
    manager.add("fourth")

    reloaded = HistoryManager(history_file)

    assert reloaded.get_previous(current_input="", prefix="") == "fourth"
    assert reloaded.get_previous(current_input="", prefix="") == "third"
    assert reloaded.get_previous(current_input="", prefix="") == "second"
    # "first" is not proposed as we defined number of entries to 3
    assert reloaded.get_previous(current_input="", prefix="") is None


def test_history_manager_filters_invalid_and_duplicated_entries(tmp_path: Path) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file, max_entries=5)
    manager.add("")  # empty
    manager.add("   ")  # is trimmed
    manager.add("first")
    manager.add("second")
    manager.add("second")  # duplicate
    manager.add("third")

    reloaded = HistoryManager(history_file)

    assert reloaded.get_previous(current_input="", prefix="") == "third"
    assert reloaded.get_previous(current_input="", prefix="") == "second"
    assert reloaded.get_previous(current_input="", prefix="") == "first"
    assert reloaded.get_previous(current_input="", prefix="") is None
    assert reloaded.get_previous(current_input="", prefix="") is None


def test_history_manager_filters_commands(tmp_path: Path) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file, max_entries=5)
    manager.add("first")
    manager.add("/skip")

    reloaded = HistoryManager(history_file)

    assert reloaded.get_previous(current_input="", prefix="/") == None
    assert reloaded.get_previous(current_input="", prefix="") == "first"
    assert reloaded.get_previous(current_input="", prefix="") is None


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


def test_history_manager_prefix_filtering(tmp_path: Path) -> None:
    history_file = tmp_path / "history.jsonl"
    manager = HistoryManager(history_file)

    manager.add("foo")
    manager.add("bar")
    manager.add("fizz")

    assert manager.get_previous(current_input="", prefix="f") == "fizz"
    assert manager.get_previous(current_input="", prefix="f") == "foo"
    assert manager.get_previous(current_input="", prefix="f") is None
