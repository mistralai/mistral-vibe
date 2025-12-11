from __future__ import annotations

from pathlib import Path

from vibe.core.autocompletion.path_prompt_adapter import (
    DEFAULT_MAX_EMBED_BYTES,
    render_path_prompt,
)


def test_treats_paths_to_files_as_embedded_resources(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("hello", encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    main_py = src_dir / "main.py"
    main_py.write_text("print('hi')", encoding="utf-8")

    rendered = render_path_prompt(
        "Please review @README.md and @src/main.py",
        base_dir=tmp_path,
        max_embed_bytes=DEFAULT_MAX_EMBED_BYTES,
    )

    expected = (
        f"Please review README.md and src/main.py\n\n"
        f"{readme.as_uri()}\n```\nhello\n```\n\n"
        f"{main_py.as_uri()}\n```\nprint('hi')\n```"
    )
    assert rendered == expected


def test_treats_path_to_directory_as_resource_links(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    rendered = render_path_prompt(
        "See @docs/ for details",
        base_dir=tmp_path,
        max_embed_bytes=DEFAULT_MAX_EMBED_BYTES,
    )

    expected = f"See docs/ for details\n\nuri: {docs_dir.as_uri()}\nname: docs/"
    assert rendered == expected


def test_keeps_emails_and_embeds_paths(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("hello", encoding="utf-8")

    rendered = render_path_prompt(
        "Contact user@example.com about @README.md",
        base_dir=tmp_path,
        max_embed_bytes=DEFAULT_MAX_EMBED_BYTES,
    )

    expected = (
        f"Contact user@example.com about README.md\n\n"
        f"{readme.as_uri()}\n```\nhello\n```"
    )
    assert rendered == expected


def test_ignores_nonexistent_paths(tmp_path: Path) -> None:
    rendered = render_path_prompt(
        "Missing @nope.txt here",
        base_dir=tmp_path,
        max_embed_bytes=DEFAULT_MAX_EMBED_BYTES,
    )

    assert rendered == "Missing @nope.txt here"


def test_falls_back_to_link_for_binary_files(tmp_path: Path) -> None:
    binary_path = tmp_path / "image.bin"
    binary_path.write_bytes(b"\x00\x01\x02")

    rendered = render_path_prompt(
        "Inspect @image.bin", base_dir=tmp_path, max_embed_bytes=DEFAULT_MAX_EMBED_BYTES
    )

    assert (
        rendered == f"Inspect image.bin\n\nuri: {binary_path.as_uri()}\nname: image.bin"
    )


def test_excludes_supposed_binary_files_quickly_before_reading_content(
    tmp_path: Path,
) -> None:
    zip_like = tmp_path / "archive.zip"
    zip_like.write_text("text content inside but treated as binary", encoding="utf-8")

    rendered = render_path_prompt(
        "Inspect @archive.zip",
        base_dir=tmp_path,
        max_embed_bytes=DEFAULT_MAX_EMBED_BYTES,
    )

    assert (
        rendered
        == f"Inspect archive.zip\n\nuri: {zip_like.as_uri()}\nname: archive.zip"
    )


def test_applies_max_embed_size_guard(tmp_path: Path) -> None:
    large_file = tmp_path / "big.txt"
    large_file.write_text("a" * 50, encoding="utf-8")

    rendered = render_path_prompt(
        "Review @big.txt", base_dir=tmp_path, max_embed_bytes=10
    )

    assert rendered == f"Review big.txt\n\nuri: {large_file.as_uri()}\nname: big.txt"


def test_parses_paths_with_special_characters_when_quoted(tmp_path: Path) -> None:
    weird = tmp_path / "weird name(1).txt"
    weird.write_text("odd", encoding="utf-8")

    rendered = render_path_prompt(
        'Open @"weird name(1).txt"',
        base_dir=tmp_path,
        max_embed_bytes=DEFAULT_MAX_EMBED_BYTES,
    )

    assert rendered == f"Open weird name(1).txt\n\n{weird.as_uri()}\n```\nodd\n```"


def test_deduplicates_identical_paths(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("hello", encoding="utf-8")

    rendered = render_path_prompt(
        "See @README.md and again @README.md",
        base_dir=tmp_path,
        max_embed_bytes=DEFAULT_MAX_EMBED_BYTES,
    )

    assert (
        rendered
        == f"See README.md and again README.md\n\n{readme.as_uri()}\n```\nhello\n```"
    )
