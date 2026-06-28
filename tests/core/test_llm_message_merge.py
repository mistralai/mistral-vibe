from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.types import (
    FileImageSource,
    ImageAttachment,
    LLMMessage,
    Role,
    UserDisplayContentMetadata,
)


@pytest.fixture()
def image_a(tmp_path: Path) -> ImageAttachment:
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG")
    return ImageAttachment(
        source=FileImageSource(path=p), alias="a.png", mime_type="image/png"
    )


@pytest.fixture()
def image_b(tmp_path: Path) -> ImageAttachment:
    p = tmp_path / "b.png"
    p.write_bytes(b"\x89PNG")
    return ImageAttachment(
        source=FileImageSource(path=p), alias="b.png", mime_type="image/png"
    )


def _msg(content: str, images: list[ImageAttachment] | None = None) -> LLMMessage:
    return LLMMessage(role=Role.assistant, content=content, images=images)


def _display_content(host: str) -> UserDisplayContentMetadata:
    return UserDisplayContentMetadata(
        version="1.0.0", host=host, content=[{"type": "text", "text": host}]
    )


def test_merge_prefers_self_images_when_present(
    image_a: ImageAttachment, image_b: ImageAttachment
) -> None:
    merged = _msg("hi", images=[image_a]) + _msg(" there", images=[image_b])
    assert merged.images == [image_a]


def test_merge_falls_back_to_other_when_self_is_none(image_b: ImageAttachment) -> None:
    merged = _msg("hi") + _msg(" there", images=[image_b])
    assert merged.images == [image_b]


def test_merge_preserves_explicit_empty_self_images_over_other(
    image_b: ImageAttachment,
) -> None:
    # An explicit `[]` (intentional clearing) must NOT silently inherit
    # `other.images`. Truthy/falsy-based merging would have flipped this.
    merged = _msg("hi", images=[]) + _msg(" there", images=[image_b])
    assert merged.images == []


def test_merge_yields_none_when_both_sides_are_none() -> None:
    merged = _msg("hi") + _msg(" there")
    assert merged.images is None


def test_merge_prefers_self_user_display_content_when_present() -> None:
    self_display_content = _display_content("mistral-vscode")
    other_display_content = _display_content("mistral-jetbrains")

    merged = LLMMessage(
        role=Role.user, content="hi", user_display_content=self_display_content
    ) + LLMMessage(
        role=Role.user, content=" there", user_display_content=other_display_content
    )

    assert merged.user_display_content == self_display_content


def test_merge_falls_back_to_other_user_display_content() -> None:
    other_display_content = _display_content("mistral-vscode")

    merged = LLMMessage(role=Role.user, content="hi") + LLMMessage(
        role=Role.user, content=" there", user_display_content=other_display_content
    )

    assert merged.user_display_content == other_display_content
