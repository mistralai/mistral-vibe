from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.autocompletion.completers import PathCompleter


@pytest.fixture()
def file_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "vibe" / "acp").mkdir(parents=True)
    (tmp_path / "vibe" / "acp" / "entrypoint.py").write_text("")
    (tmp_path / "vibe" / "acp" / "agent.py").write_text("")
    (tmp_path / "vibe" / "cli" / "autocompletion").mkdir(parents=True)
    (tmp_path / "vibe" / "cli" / "autocompletion" / "fuzzy.py").write_text("")
    (tmp_path / "vibe" / "cli" / "autocompletion" / "completers.py").write_text("")
    (tmp_path / "tests" / "autocompletion").mkdir(parents=True)
    (tmp_path / "tests" / "autocompletion" / "test_fuzzy.py").write_text("")
    (tmp_path / "README.md").write_text("")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_finds_files_recursively_by_filename(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@entryp", cursor_pos=7)

    assert results[0] == "@vibe/acp/entrypoint.py"


def test_finds_files_recursively_by_partial_path(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@acp/entry", cursor_pos=10)

    assert results[0] == "@vibe/acp/entrypoint.py"


def test_finds_files_recursively_with_subsequence(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@acp/ent", cursor_pos=9)

    assert results[0] == "@vibe/acp/entrypoint.py"


def test_finds_multiple_matches_recursively(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@fuzzy", cursor_pos=6)

    vibe_index = results.index("@vibe/cli/autocompletion/fuzzy.py")
    test_index = results.index("@tests/autocompletion/test_fuzzy.py")
    assert vibe_index < test_index


def test_prioritizes_exact_path_matches(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@vibe/acp/entrypoint", cursor_pos=20)

    assert results[0] == "@vibe/acp/entrypoint.py"


def test_finds_files_when_pattern_matches_directory_name(file_tree: Path) -> None:
    results = PathCompleter().get_completions("@acp", cursor_pos=4)

    assert results == [
        "@vibe/acp/",
        "@vibe/acp/agent.py",
        "@vibe/acp/entrypoint.py",
        "@vibe/cli/autocompletion/completers.py",
        "@tests/autocompletion/",
        "@tests/autocompletion/test_fuzzy.py",
        "@vibe/cli/autocompletion/",
        "@vibe/cli/autocompletion/fuzzy.py",
    ]
