from __future__ import annotations

from pathlib import Path

import pytest

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


class TestTildeExpansion:
    def test_tilde_path_expands_and_attaches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        shot = tmp_path / "shot.png"
        shot.write_bytes(b"\x89PNG")

        payload = build_path_prompt_payload("look at @~/shot.png", base_dir=tmp_path)

        assert len(payload.resources) == 1
        resource = payload.resources[0]
        assert resource.path == shot.resolve()
        assert resource.alias == "~/shot.png"
        assert resource.kind == "image"

    def test_bare_tilde_attaches_home_as_folder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))

        payload = build_path_prompt_payload("go to @~", base_dir=tmp_path)

        assert len(payload.resources) == 1
        assert payload.resources[0].path == tmp_path.resolve()
        assert payload.resources[0].kind == "folder"

    def test_unknown_user_tilde_does_not_crash(self, tmp_path: Path) -> None:
        payload = build_path_prompt_payload(
            "see @~nonexistentuser1234/x.png", base_dir=tmp_path
        )

        assert payload.resources == []

    def test_tilde_mid_token_without_anchor_stays_text(self, tmp_path: Path) -> None:
        payload = build_path_prompt_payload("foo~bar baz", base_dir=tmp_path)

        assert payload.resources == []
        assert payload.prompt_text == "foo~bar baz"


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
