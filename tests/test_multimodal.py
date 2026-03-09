"""Tests for _resolve_image_references in AgentLoop."""

from __future__ import annotations

import base64
from pathlib import Path
import struct
import zlib

import pytest

from vibe.core.agent_loop import AgentLoop

# ── helpers ──────────────────────────────────────────────────────────────────


def make_png(path: Path) -> None:
    """Write a minimal valid 1×1 PNG to *path*."""

    def chunk(name: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + name + data
        return c + struct.pack(">I", zlib.crc32(c[4:]) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


# ── no images ─────────────────────────────────────────────────────────────────


def test_plain_text_returns_unchanged():
    text, parts = AgentLoop._resolve_image_references("hello world")
    assert text == "hello world"
    assert parts == []


def test_at_ref_non_image_extension_is_ignored(tmp_path: Path):
    f = tmp_path / "README.md"
    f.write_text("# hello")
    text, parts = AgentLoop._resolve_image_references(f"@{f} explain this")
    assert parts == []
    assert f"@{f}" in text


def test_at_ref_nonexistent_image_raises(tmp_path: Path):
    missing = tmp_path / "ghost.png"
    with pytest.raises(FileNotFoundError, match="ghost.png"):
        AgentLoop._resolve_image_references(f"@{missing} what is this?")


# ── single image ──────────────────────────────────────────────────────────────


def test_image_ref_at_start_of_message(tmp_path: Path):
    img = tmp_path / "photo.png"
    make_png(img)
    text, parts = AgentLoop._resolve_image_references(f"@{img} what is this?")
    assert len(parts) == 1
    assert text == "what is this?"
    assert parts[0].image_url.startswith("data:image/png;base64,")


def test_image_ref_at_end_of_message(tmp_path: Path):
    img = tmp_path / "photo.png"
    make_png(img)
    text, parts = AgentLoop._resolve_image_references(f"what is this? @{img}")
    assert len(parts) == 1
    assert text == "what is this?"


def test_image_only_no_surrounding_text(tmp_path: Path):
    img = tmp_path / "photo.png"
    make_png(img)
    text, parts = AgentLoop._resolve_image_references(f"@{img}")
    assert len(parts) == 1
    assert text == ""


def test_jpeg_extension_detected(tmp_path: Path):
    img = tmp_path / "photo.jpg"
    make_png(img)
    _, parts = AgentLoop._resolve_image_references(f"@{img} describe")
    assert len(parts) == 1
    assert "image/jpeg" in parts[0].image_url


def test_webp_extension_detected(tmp_path: Path):
    img = tmp_path / "photo.webp"
    img.write_bytes(b"RIFF....WEBP")
    _, parts = AgentLoop._resolve_image_references(f"@{img} describe")
    assert len(parts) == 1


def test_gif_extension_detected(tmp_path: Path):
    img = tmp_path / "anim.gif"
    img.write_bytes(b"GIF89a")
    _, parts = AgentLoop._resolve_image_references(f"@{img} describe")
    assert len(parts) == 1


# ── multiple images ───────────────────────────────────────────────────────────


def test_two_images_in_one_message(tmp_path: Path):
    img1 = tmp_path / "a.png"
    img2 = tmp_path / "b.png"
    make_png(img1)
    make_png(img2)
    text, parts = AgentLoop._resolve_image_references(f"compare @{img1} and @{img2}")
    assert len(parts) == 2
    # each @ref is replaced with "" leaving a double space; strip only trims ends
    assert "compare" in text and "and" in text


def test_image_and_non_image_ref_in_same_message(tmp_path: Path):
    img = tmp_path / "photo.png"
    txt = tmp_path / "notes.txt"
    make_png(img)
    txt.write_text("some notes")
    text, parts = AgentLoop._resolve_image_references(
        f"@{img} explain with context @{txt}"
    )
    assert len(parts) == 1
    assert str(txt) in text


# ── base64 encoding ───────────────────────────────────────────────────────────


def test_base64_round_trip(tmp_path: Path):
    img = tmp_path / "photo.png"
    make_png(img)
    _, parts = AgentLoop._resolve_image_references(f"@{img}")
    _prefix, b64 = parts[0].image_url.split(",", 1)
    assert base64.standard_b64decode(b64) == img.read_bytes()


def test_media_type_stored_on_part(tmp_path: Path):
    img = tmp_path / "photo.png"
    make_png(img)
    _, parts = AgentLoop._resolve_image_references(f"@{img}")
    assert parts[0].media_type == "image/png"
