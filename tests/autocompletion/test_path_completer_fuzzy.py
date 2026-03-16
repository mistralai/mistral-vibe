from __future__ import annotations

from pathlib import Path

import pytest

import vibe.core.autocompletion.completers as completers_module
from vibe.core.autocompletion.completers import PathCompleter
from vibe.core.autocompletion.fuzzy import fuzzy_match as real_fuzzy_match


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


def test_directory_prefix_can_match_from_a_nested_path_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "vibe" / "acp").mkdir(parents=True)
    (tmp_path / "vibe" / "acp" / "entrypoint.py").write_text("", encoding="utf-8")
    (tmp_path / "vibe" / "myacp").mkdir(parents=True)
    (tmp_path / "vibe" / "myacp" / "entrypoint.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    results = PathCompleter().get_completions("@acp/", cursor_pos=5)

    assert "@vibe/acp/entrypoint.py" in results
    assert "@vibe/myacp/entrypoint.py" not in results


def test_prefers_exact_filename_match_over_other_path_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "chat-input.tsx").write_text("", encoding="utf-8")
    (tmp_path / "src" / "features").mkdir(parents=True)
    (tmp_path / "src" / "features" / "chat-input-state.ts").write_text(
        "", encoding="utf-8"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "chat-input.md").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    results = PathCompleter().get_completions("@chat-input", cursor_pos=11)

    assert results[0] == "@src/chat-input.tsx"


def test_keeps_late_strong_match_when_target_matches_is_small(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(10):
        (tmp_path / f"prefix_{i}_chatnoise.txt").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "chat-input.tsx").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    results = PathCompleter(target_matches=5).get_completions("@chati", cursor_pos=6)

    assert results[0] == "@src/chat-input.tsx"


def test_prefers_source_file_over_lock_file_for_stem_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "foo.lock").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    results = PathCompleter().get_completions("@foo", cursor_pos=4)

    assert results[0] == "@src/foo.py"


def test_prefers_exact_filename_matches_for_filename_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        tmp_path
        / "ts"
        / "apps"
        / "le-chat-web"
        / "src"
        / "app"
        / "chat"
        / "_components"
    ).mkdir(parents=True)
    (
        tmp_path
        / "ts"
        / "apps"
        / "le-chat-web"
        / "src"
        / "app"
        / "chat"
        / "_components"
        / "chat-input.tsx"
    ).write_text("", encoding="utf-8")
    (tmp_path / "ts" / "apps" / "le-chat-web" / "src" / "components").mkdir(
        parents=True
    )
    (
        tmp_path
        / "ts"
        / "apps"
        / "le-chat-web"
        / "src"
        / "components"
        / "chat-input.tsx"
    ).write_text("", encoding="utf-8")
    (
        tmp_path / "ts" / "apps" / "le-chat-mobile" / "components" / "SearchInput.tsx"
    ).parent.mkdir(parents=True)
    (
        tmp_path / "ts" / "apps" / "le-chat-mobile" / "components" / "SearchInput.tsx"
    ).write_text("", encoding="utf-8")
    (
        tmp_path
        / "ts"
        / "apps"
        / "le-chat-web"
        / "src"
        / "app"
        / "chat"
        / "_components"
        / "message-input.tsx"
    ).write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    results = PathCompleter().get_completions("@chat-input.tsx", cursor_pos=16)

    assert set(results[:2]) == {
        "@ts/apps/le-chat-web/src/app/chat/_components/chat-input.tsx",
        "@ts/apps/le-chat-web/src/components/chat-input.tsx",
    }
    assert results.index("@ts/apps/le-chat-mobile/components/SearchInput.tsx") > 1
    assert (
        results.index("@ts/apps/le-chat-web/src/app/chat/_components/message-input.tsx")
        > 1
    )


def test_exact_path_query_ranks_children_ahead_of_unrelated_fuzzy_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "scripts" / "zephyr" / "generators").mkdir(parents=True)
    (tmp_path / "zephyr" / "generators" / "tests").mkdir(parents=True)
    (tmp_path / "zephyr" / "generators" / "prompts").mkdir(parents=True)
    (tmp_path / "zephyr" / "generators" / "common.py").write_text("", encoding="utf-8")
    (tmp_path / "zephyr" / "generators" / "README.md").write_text("", encoding="utf-8")
    (
        tmp_path
        / "zephyr"
        / "datasets"
        / "synthetic_sp_up_conflict"
        / "grounded_policies"
        / "hotel"
    ).mkdir(parents=True)
    (
        tmp_path
        / "zephyr"
        / "datasets"
        / "synthetic_sp_up_conflict"
        / "grounded_policies"
        / "hotel"
        / "generators.py"
    ).write_text("", encoding="utf-8")
    (
        tmp_path
        / "zephyr"
        / "datasets"
        / "synthetic_sp_up_conflict"
        / "grounded_policies"
        / "retail"
    ).mkdir(parents=True)
    (
        tmp_path
        / "zephyr"
        / "datasets"
        / "synthetic_sp_up_conflict"
        / "grounded_policies"
        / "retail"
        / "generators.py"
    ).write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    results = PathCompleter().get_completions("@zephyr/generators", cursor_pos=18)

    assert results[0] == "@zephyr/generators/"
    assert results.index("@zephyr/generators/common.py") < results.index(
        "@zephyr/datasets/synthetic_sp_up_conflict/grounded_policies/hotel/generators.py"
    )
    assert results.index("@zephyr/generators/prompts/") < results.index(
        "@zephyr/datasets/synthetic_sp_up_conflict/grounded_policies/retail/generators.py"
    )
    assert results.index("@zephyr/generators/tests/") < results.index(
        "@zephyr/datasets/synthetic_sp_up_conflict/grounded_policies/hotel/generators.py"
    )


def test_skips_fuzzy_scoring_for_entries_missing_required_query_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "chat-input.tsx").write_text("", encoding="utf-8")
    for name in ("alpha.txt", "theta.txt", "notes.md"):
        (tmp_path / name).write_text("", encoding="utf-8")
    for i in range(20):
        (tmp_path / f"zzzz_{i}.txt").write_text("", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    fuzzy_calls: list[str] = []

    def counting_fuzzy_match(
        pattern: str, text: str, text_lower: str | None = None
    ) -> object:
        fuzzy_calls.append(text)
        return real_fuzzy_match(pattern, text, text_lower)

    monkeypatch.setattr(completers_module, "fuzzy_match", counting_fuzzy_match)

    results = PathCompleter().get_completions("@chati", cursor_pos=6)

    assert results[0] == "@src/chat-input.tsx"
    assert fuzzy_calls == ["src/chat-input.tsx"]


def test_non_ascii_queries_still_match_when_ascii_prefilter_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "café.txt").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    results = PathCompleter().get_completions("@café", cursor_pos=5)

    assert results == ["@café.txt"]
