from __future__ import annotations

from pathlib import Path

from vibe.core.autocompletion.path_prompt import build_path_prompt_payload
from vibe.core.autocompletion.path_prompt_adapter import (
    extract_image_resources,
    render_path_prompt,
)


def test_image_extension_is_classified_as_image_kind(tmp_path: Path) -> None:
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")

    payload = build_path_prompt_payload("look at @logo.png", base_dir=tmp_path)

    assert len(payload.resources) == 1
    assert payload.resources[0].kind == "image"
    assert payload.resources[0].alias == "logo.png"


def test_text_file_remains_kind_file(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("hello")

    payload = build_path_prompt_payload("read @notes.md", base_dir=tmp_path)

    assert payload.resources[0].kind == "file"


def test_image_mentions_omit_resource_link_when_skip_images(tmp_path: Path) -> None:
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")

    rendered = render_path_prompt(
        "look at @logo.png", base_dir=tmp_path, skip_images=True
    )

    assert "logo.png" in rendered  # kept in the prompt text as the @ mention
    assert "file://" not in rendered  # no resource_link block emitted


def test_image_mentions_emit_resource_link_by_default(tmp_path: Path) -> None:
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")

    rendered = render_path_prompt("look at @logo.png", base_dir=tmp_path)

    assert "logo.png" in rendered
    assert (tmp_path / "logo.png").as_uri() in rendered


def test_extract_image_resources_filters_only_images(tmp_path: Path) -> None:
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    (tmp_path / "notes.md").write_text("hi")
    (tmp_path / "diagram.webp").write_bytes(b"RIFF")

    payload = build_path_prompt_payload(
        "see @logo.png @notes.md @diagram.webp", base_dir=tmp_path
    )

    images = extract_image_resources(payload)

    aliases = sorted(r.alias for r in images)
    assert aliases == ["diagram.webp", "logo.png"]


def test_case_insensitive_image_extension(tmp_path: Path) -> None:
    (tmp_path / "screenshot.PNG").write_bytes(b"\x89PNG")

    payload = build_path_prompt_payload("see @screenshot.PNG", base_dir=tmp_path)

    assert payload.resources[0].kind == "image"
