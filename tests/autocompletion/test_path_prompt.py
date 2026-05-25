from __future__ import annotations

from pathlib import Path

from vibe.core.autocompletion.path_prompt import (
    build_path_prompt_payload,
    build_title_segments,
)
from vibe.core.session.title_format import MentionSegment, TextSegment


def test_deduplicates_same_file_mentioned_twice(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("hello", encoding="utf-8")

    payload = build_path_prompt_payload(
        "See @README.md and again @README.md", base_dir=tmp_path
    )

    assert len(payload.resources) == 1
    assert payload.resources[0].path == readme
    assert len(payload.all_resources) == 2


class TestBuildTitleSegments:
    def test_empty_message(self) -> None:
        assert build_title_segments("") == []

    def test_plain_text_no_mentions(self) -> None:
        segments = build_title_segments("hello world")
        assert segments == [TextSegment(text="hello world")]

    def test_matched_file_mention_uses_basename(self, tmp_path: Path) -> None:
        nested = tmp_path / "src" / "auth"
        nested.mkdir(parents=True)
        target = nested / "foo.py"
        target.write_text("x", encoding="utf-8")

        segments = build_title_segments(
            "Refactor @src/auth/foo.py please", base_dir=tmp_path
        )
        assert segments == [
            TextSegment(text="Refactor "),
            MentionSegment(name="foo.py"),
            TextSegment(text=" please"),
        ]

    def test_unmatched_mention_stays_as_text(self, tmp_path: Path) -> None:
        segments = build_title_segments("Look at @nope.py here", base_dir=tmp_path)
        assert segments == [TextSegment(text="Look at @nope.py here")]

    def test_folder_mention_uses_basename(self, tmp_path: Path) -> None:
        folder = tmp_path / "components"
        folder.mkdir()

        segments = build_title_segments("Update @components", base_dir=tmp_path)
        assert segments == [
            TextSegment(text="Update "),
            MentionSegment(name="components"),
        ]

    def test_multiple_mentions_keep_text_in_between(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "b.py").write_text("", encoding="utf-8")

        segments = build_title_segments("@a.py vs @b.py", base_dir=tmp_path)
        assert segments == [
            MentionSegment(name="a.py"),
            TextSegment(text=" vs "),
            MentionSegment(name="b.py"),
        ]

    def test_mention_with_no_surrounding_text(self, tmp_path: Path) -> None:
        (tmp_path / "only.py").write_text("", encoding="utf-8")

        segments = build_title_segments("@only.py", base_dir=tmp_path)
        assert segments == [MentionSegment(name="only.py")]
