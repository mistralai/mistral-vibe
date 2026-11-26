from __future__ import annotations

from pathlib import Path

import pytest

from vibe.app_server._unified_scratchpad import (
    ScratchpadArgs,
    ScratchpadError,
    run_scratchpad,
    scratchpad_dir,
)

SESSION_ID = "session-under-test"


def test_a_write_is_read_back_and_listed(tmp_path: Path) -> None:
    written = run_scratchpad(
        ScratchpadArgs(action="write", path="notes/plan.md", content="ship it"),
        directory=tmp_path,
    )
    read = run_scratchpad(
        ScratchpadArgs(action="read", path="notes/plan.md"), directory=tmp_path
    )
    listed = run_scratchpad(ScratchpadArgs(action="list"), directory=tmp_path)

    assert written.message == "Wrote notes/plan.md"
    assert read.content == "ship it"
    assert listed.files == ["notes/plan.md"]
    assert listed.message == "Listed 1 files"


def test_a_second_write_replaces_the_file(tmp_path: Path) -> None:
    run_scratchpad(
        ScratchpadArgs(action="write", path="plan.md", content="first"),
        directory=tmp_path,
    )
    run_scratchpad(
        ScratchpadArgs(action="write", path="plan.md", content="second"),
        directory=tmp_path,
    )

    read = run_scratchpad(
        ScratchpadArgs(action="read", path="plan.md"), directory=tmp_path
    )

    assert read.content == "second"


def test_listing_a_scratchpad_that_was_never_written_is_empty(tmp_path: Path) -> None:
    listed = run_scratchpad(
        ScratchpadArgs(action="list"), directory=tmp_path / "never-created"
    )

    assert listed.files == []


def test_a_relative_escape_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "scratchpad"

    with pytest.raises(ScratchpadError, match="escapes"):
        run_scratchpad(
            ScratchpadArgs(action="write", path="../escaped.md", content="oops"),
            directory=directory,
        )

    assert not (tmp_path / "escaped.md").exists()


def test_an_absolute_path_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "escaped.md"

    with pytest.raises(ScratchpadError, match="escapes"):
        run_scratchpad(
            ScratchpadArgs(action="write", path=str(target), content="oops"),
            directory=tmp_path / "scratchpad",
        )

    assert not target.exists()


def test_a_symlink_out_of_the_scratchpad_is_refused(tmp_path: Path) -> None:
    # The one escape a string check would miss: the path is relative and has no
    # `..` in it, and only resolution shows where it lands.
    directory = tmp_path / "scratchpad"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (directory / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ScratchpadError, match="escapes"):
        run_scratchpad(
            ScratchpadArgs(action="write", path="link/escaped.md", content="oops"),
            directory=directory,
        )

    assert not (outside / "escaped.md").exists()


def test_a_symlink_planted_in_the_scratchpad_is_neither_listed_nor_read(
    tmp_path: Path,
) -> None:
    """The write path refuses to create one, but the filesystem tools can."""
    directory = tmp_path / "scratchpad"
    directory.mkdir()
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY", encoding="utf-8")
    (directory / "real.md").write_text("genuine note", encoding="utf-8")
    (directory / "leak.md").symlink_to(secret)

    listed = run_scratchpad(ScratchpadArgs(action="list"), directory=directory)

    assert listed.files == ["real.md"]
    with pytest.raises(ScratchpadError, match="escapes"):
        run_scratchpad(
            ScratchpadArgs(action="read", path="leak.md"), directory=directory
        )


def test_a_hard_link_is_neither_read_nor_overwritten(tmp_path: Path) -> None:
    # `resolve()` has nothing to follow, so the confinement check passes and only the
    # link count shows that a write would truncate the file it aliases.
    directory = tmp_path / "scratchpad"
    directory.mkdir()
    outside = tmp_path / "id_rsa"
    outside.write_text("PRIVATE KEY", encoding="utf-8")
    (directory / "leak.md").hardlink_to(outside)

    with pytest.raises(ScratchpadError, match="hard link"):
        run_scratchpad(
            ScratchpadArgs(action="read", path="leak.md"), directory=directory
        )
    with pytest.raises(ScratchpadError, match="hard link"):
        run_scratchpad(
            ScratchpadArgs(action="write", path="leak.md", content="clobbered"),
            directory=directory,
        )

    assert outside.read_text() == "PRIVATE KEY"
    assert (
        run_scratchpad(ScratchpadArgs(action="list"), directory=directory).files == []
    )


def test_the_scratchpad_directory_itself_is_not_a_file(tmp_path: Path) -> None:
    with pytest.raises(ScratchpadError, match="must name a file"):
        run_scratchpad(
            ScratchpadArgs(action="write", path=".", content="oops"), directory=tmp_path
        )


def test_a_write_without_content_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScratchpadError, match="'content' is required"):
        run_scratchpad(
            ScratchpadArgs(action="write", path="plan.md"), directory=tmp_path
        )


def test_a_read_without_a_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScratchpadError, match="'path' is required"):
        run_scratchpad(ScratchpadArgs(action="read"), directory=tmp_path)


def test_reading_a_file_that_is_not_there_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScratchpadError, match="No such file"):
        run_scratchpad(
            ScratchpadArgs(action="read", path="plan.md"), directory=tmp_path
        )


def test_the_scratchpad_lives_inside_the_harness_session_directory(
    tmp_path: Path,
) -> None:
    from mistralai_vibe_local_harness.vibe._host import UnifiedHarnessSessionBackendHost

    # Reaching for the private ``_session_root`` on purpose: the scratchpad shares
    # the session store's lifetime only while the two agree on the layout, and the
    # Harness owns that layout from another distribution. If it moves, this fails
    # instead of silently orphaning every session's notes.
    host = UnifiedHarnessSessionBackendHost(tmp_path)

    assert scratchpad_dir(tmp_path, SESSION_ID).parent == host._session_root(SESSION_ID)
