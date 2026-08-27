from __future__ import annotations

from pathlib import Path

import pytest

from scripts import bump_version


def _write_whats_new(tmp_path: Path, content: str) -> Path:
    vibe_dir = tmp_path / "vibe"
    vibe_dir.mkdir(parents=True, exist_ok=True)
    whats_new = vibe_dir / "whats_new.md"
    whats_new.write_text(content, encoding="utf-8")
    return whats_new


def test_scaffold_whats_new_minor_resets_to_header_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    whats_new = _write_whats_new(
        tmp_path,
        "# What's new in v2.23.0\n\n- **Interactive resume**: Added /resume\n- **Foo**: bar\n",
    )

    bump_version.scaffold_whats_new("2.24.0")

    assert whats_new.read_text(encoding="utf-8") == "# What's new in v2.24.0\n"


def test_scaffold_whats_new_major_resets_to_header_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    whats_new = _write_whats_new(
        tmp_path, "# What's new in v2.23.5\n\n- **A**: thing\n"
    )

    bump_version.scaffold_whats_new("3.0.0")

    assert whats_new.read_text(encoding="utf-8") == "# What's new in v3.0.0\n"


def test_scaffold_whats_new_patch_preserves_bullets_and_bumps_header(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    whats_new = _write_whats_new(
        tmp_path,
        "# What's new in v2.23.0\n\n"
        "- **Interactive resume**: Added /resume command\n"
        "- **Foo**: bar\n",
    )

    bump_version.scaffold_whats_new("2.23.1")

    assert whats_new.read_text(encoding="utf-8") == (
        "# What's new in v2.23.1\n\n"
        "- **Interactive resume**: Added /resume command\n"
        "- **Foo**: bar\n"
    )


def test_scaffold_whats_new_patch_on_empty_file_writes_header(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    whats_new = _write_whats_new(tmp_path, "")

    bump_version.scaffold_whats_new("2.23.1")

    assert whats_new.read_text(encoding="utf-8") == "# What's new in v2.23.1\n"


def test_scaffold_whats_new_patch_on_header_only_preserves_header_only(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    whats_new = _write_whats_new(tmp_path, "# What's new in v2.23.0\n")

    bump_version.scaffold_whats_new("2.23.1")

    assert whats_new.read_text(encoding="utf-8") == "# What's new in v2.23.1\n"


def test_scaffold_whats_new_patch_with_missing_header_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_whats_new(tmp_path, "Some intro without a header\n\n- no header here\n")

    with pytest.raises(ValueError, match="whats_new.md header not found"):
        bump_version.scaffold_whats_new("2.23.1")


def test_finalize_whats_new_empties_header_only_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    whats_new = _write_whats_new(tmp_path, "# What's new in v2.24.0\n")

    bump_version.finalize_whats_new()

    assert whats_new.read_text(encoding="utf-8") == ""


def test_finalize_whats_new_empties_header_only_with_trailing_whitespace(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    whats_new = _write_whats_new(tmp_path, "# What's new in v2.24.0\n\n  \n\t\n")

    bump_version.finalize_whats_new()

    assert whats_new.read_text(encoding="utf-8") == ""


def test_finalize_whats_new_preserves_file_with_bullets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    content = (
        "# What's new in v2.24.0\n\n- **New**: A thing\n- **More**: Another thing\n"
    )
    whats_new = _write_whats_new(tmp_path, content)

    bump_version.finalize_whats_new()

    assert whats_new.read_text(encoding="utf-8") == content


def test_finalize_whats_new_preserves_empty_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    whats_new = _write_whats_new(tmp_path, "")

    bump_version.finalize_whats_new()

    assert whats_new.read_text(encoding="utf-8") == ""


def test_finalize_whats_new_noop_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    bump_version.finalize_whats_new()
