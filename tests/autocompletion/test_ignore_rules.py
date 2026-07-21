from __future__ import annotations

from pathlib import Path

from vibe.core.autocompletion.file_indexer.ignore_rules import IgnoreRules


def test_gitignore_pattern_with_hash_is_not_treated_as_comment(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*#*#\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / "foo#bar#").write_text("autosave", encoding="utf-8")

    rules = IgnoreRules()
    rules.ensure_for_root(tmp_path)

    assert not rules.should_ignore("README.md", "README.md", is_dir=False)
    assert rules.should_ignore("foo#bar#", "foo#bar#", is_dir=False)


def test_gitignore_inline_comment_after_whitespace_is_still_stripped(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("*.log  # build artifacts\n", encoding="utf-8")
    (tmp_path / "app.log").write_text("log", encoding="utf-8")
    (tmp_path / "app.txt").write_text("text", encoding="utf-8")

    rules = IgnoreRules()
    rules.ensure_for_root(tmp_path)

    assert rules.should_ignore("app.log", "app.log", is_dir=False)
    assert not rules.should_ignore("app.txt", "app.txt", is_dir=False)
