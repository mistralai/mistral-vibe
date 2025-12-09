from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.autocompletion.completers import PathCompleter


@pytest.fixture()
def file_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "models.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "core" / "logger.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "models.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "ports.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "sanitize.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "use_cases.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "core" / "validate.py").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "settings.py").write_text("", encoding="utf-8")
    (tmp_path / "config" / "database.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_fuzzy_matches_subsequence_characters(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@sr", cursor_pos=3)

    assert "@src/" in results


def test_fuzzy_matches_consecutive_characters_higher(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src/main", cursor_pos=9)

    assert "@src/main.py" in results


def test_fuzzy_matches_prefix_highest(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src", cursor_pos=4)

    assert results[0].startswith("@src")


def test_fuzzy_matches_across_directory_boundaries(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src/main", cursor_pos=9)

    assert "@src/main.py" in results


def test_fuzzy_matches_case_insensitive(file_tree: Path) -> None:
    completer = PathCompleter()
    assert "@README.md" in completer.get_completions("@readme", cursor_pos=7)
    assert "@README.md" in completer.get_completions("@README", cursor_pos=7)


def test_fuzzy_matches_word_boundaries_preferred(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src/mp", cursor_pos=7)

    assert "@src/models.py" in results


def test_fuzzy_matches_empty_pattern_shows_all(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@", cursor_pos=1)

    assert "@README.md" in results
    assert "@src/" in results


def test_fuzzy_matches_hidden_files_only_with_dot(file_tree: Path) -> None:
    completer = PathCompleter()
    assert "@.env" not in completer.get_completions("@e", cursor_pos=2)
    assert "@.env" in completer.get_completions("@.", cursor_pos=2)


def test_fuzzy_matches_directories_and_files(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src/", cursor_pos=5)

    assert any(r.endswith("/") for r in results)
    assert any(not r.endswith("/") for r in results)


def test_fuzzy_matches_sorted_by_score(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src/main", cursor_pos=9)

    assert results[0] == "@src/main.py"


def test_fuzzy_matches_nested_directories(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src/core/l", cursor_pos=11)

    assert "@src/core/logger.py" in results


def test_fuzzy_matches_partial_filename(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src/mo", cursor_pos=7)

    assert "@src/models.py" in results


def test_fuzzy_matches_multiple_files_with_same_pattern(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src/m", cursor_pos=6)

    assert "@src/main.py" in results
    assert "@src/models.py" in results


def test_fuzzy_matches_no_results_when_no_match(file_tree: Path) -> None:
    completer = PathCompleter()
    assert completer.get_completions("@xyz123", cursor_pos=7) == []


def test_fuzzy_matches_directory_traversal(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@src/", cursor_pos=5)

    assert "@src/main.py" in results
    assert "@src/core/" in results
    assert "@src/utils/" in results
