from __future__ import annotations

import pytest

from vibe.core.memory import append_user_memory


def test_creates_file_and_section(tmp_path):
    target = tmp_path / "AGENTS.md"

    append_user_memory("prefer tabs over spaces", path=target)

    content = target.read_text()
    assert "## Memories" in content
    assert "- prefer tabs over spaces" in content


def test_appends_under_existing_section(tmp_path):
    target = tmp_path / "AGENTS.md"
    append_user_memory("first note", path=target)

    append_user_memory("second note", path=target)

    content = target.read_text()
    assert content.count("## Memories") == 1
    assert content.index("- first note") < content.index("- second note")


def test_preserves_existing_content(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text("# My instructions\n\nAlways run tests.\n")

    append_user_memory("use uv", path=target)

    content = target.read_text()
    assert "# My instructions" in content
    assert "Always run tests." in content
    assert "- use uv" in content


def test_inserts_before_following_section(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text("## Memories\n\n- old\n\n## Other\n\nstuff\n")

    append_user_memory("new", path=target)

    content = target.read_text()
    assert content.index("- old") < content.index("- new")
    assert content.index("- new") < content.index("## Other")


def test_empty_note_raises(tmp_path):
    with pytest.raises(ValueError):
        append_user_memory("   ", path=tmp_path / "AGENTS.md")
