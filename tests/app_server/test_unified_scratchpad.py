from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from vibe.app_server._unified_scratchpad import (
    _MAX_FILES,
    ScratchpadArgs,
    ScratchpadError,
    run_scratchpad,
    scratchpad_block_to_restate,
    scratchpad_dir,
)
from vibe.app_server.models import (
    PublicCheckpointEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    TextContentBlock,
)

SESSION_ID = "session-under-test"


def _user(text: str) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=f"entry-{abs(hash(text))}",
        session_id=SESSION_ID,
        created_at=0,
        updated_at=0,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="user",
        content=[TextContentBlock(text=text)],
    )


def _compaction(*, error: str | None = None) -> PublicCheckpointEntry:
    return PublicCheckpointEntry(
        id="entry-compaction",
        session_id=SESSION_ID,
        created_at=0,
        updated_at=0,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        kind="compaction",
        details={"trigger": "auto"} if error is None else {"error": {"code": error}},
    )


async def _restate(root: Path, *history: PublicHistoryEntry) -> str | None:
    async def entries() -> Sequence[PublicHistoryEntry]:
        return history

    return await scratchpad_block_to_restate(
        storage_root=root, session_id=SESSION_ID, history=entries
    )


def _write_note(root: Path, name: str, text: str) -> Path:
    path = scratchpad_dir(root, SESSION_ID) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_a_session_without_a_scratchpad_states_nothing(tmp_path: Path) -> None:
    assert await _restate(tmp_path) is None


@pytest.mark.asyncio
async def test_the_notes_are_stated_with_their_names_and_their_directory(
    tmp_path: Path,
) -> None:
    _write_note(tmp_path, "plan.md", "ship the seam first")

    block = await _restate(tmp_path)

    assert block is not None
    assert "ship the seam first" in block
    assert "plan.md" in block
    assert str(scratchpad_dir(tmp_path, SESSION_ID)) in block


@pytest.mark.asyncio
async def test_notes_already_in_context_are_not_repeated(tmp_path: Path) -> None:
    _write_note(tmp_path, "plan.md", "ship the seam first")
    already_stated = await _restate(tmp_path)
    assert already_stated is not None

    assert await _restate(tmp_path, _user("hello"), _user(already_stated)) is None


@pytest.mark.asyncio
async def test_a_block_stated_before_a_compaction_no_longer_counts(
    tmp_path: Path,
) -> None:
    """Public history keeps the block, but the model's messages became a summary."""
    _write_note(tmp_path, "plan.md", "ship the seam first")
    stated = await _restate(tmp_path)
    assert stated is not None

    assert await _restate(tmp_path, _user(stated), _compaction()) == stated


@pytest.mark.asyncio
async def test_a_failed_compaction_leaves_the_stated_block_in_place(
    tmp_path: Path,
) -> None:
    _write_note(tmp_path, "plan.md", "ship the seam first")
    stated = await _restate(tmp_path)
    assert stated is not None

    assert (
        await _restate(tmp_path, _user(stated), _compaction(error="overflow")) is None
    )


@pytest.mark.asyncio
async def test_edited_notes_are_restated_even_though_the_old_block_survives(
    tmp_path: Path,
) -> None:
    _write_note(tmp_path, "plan.md", "ship the seam first")
    stale = await _restate(tmp_path)
    assert stale is not None
    _write_note(tmp_path, "plan.md", "ship the scratchpad first")

    block = await _restate(tmp_path, _user(stale))

    assert block is not None
    assert "ship the scratchpad first" in block


@pytest.mark.asyncio
async def test_an_unreadable_scratchpad_does_not_fail_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_note(tmp_path, "plan.md", "ship the seam first")

    def explode(_directory: Path) -> None:
        raise OSError("scratchpad is on fire")

    monkeypatch.setattr("vibe.app_server._unified_scratchpad._read_notes", explode)

    assert await _restate(tmp_path) is None


@pytest.mark.asyncio
async def test_a_large_scratchpad_is_truncated_rather_than_flooding_the_turn(
    tmp_path: Path,
) -> None:
    _write_note(tmp_path, "big.md", "x" * 100_000)

    block = await _restate(tmp_path)

    assert block is not None
    assert "(truncated)" in block
    assert len(block) < 10_000


@pytest.mark.asyncio
async def test_a_symlink_planted_in_the_scratchpad_is_not_restated(
    tmp_path: Path,
) -> None:
    """The write path refuses to create one, but the filesystem tools can."""
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY", encoding="utf-8")
    _write_note(tmp_path, "real.md", "genuine note")
    (scratchpad_dir(tmp_path, SESSION_ID) / "leak.md").symlink_to(secret)

    block = await _restate(tmp_path)

    assert block is not None
    assert "genuine note" in block
    assert "PRIVATE KEY" not in block


@pytest.mark.asyncio
async def test_a_hard_link_planted_in_the_scratchpad_is_not_restated(
    tmp_path: Path,
) -> None:
    """Unlike a symlink, it resolves to itself, so confinement alone cannot see it."""
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY", encoding="utf-8")
    _write_note(tmp_path, "real.md", "genuine note")
    (scratchpad_dir(tmp_path, SESSION_ID) / "leak.md").hardlink_to(secret)

    block = await _restate(tmp_path)

    assert block is not None
    assert "genuine note" in block
    assert "PRIVATE KEY" not in block


@pytest.mark.asyncio
async def test_an_emptied_scratchpad_is_stated_once_so_the_last_block_is_not_stale(
    tmp_path: Path,
) -> None:
    note = _write_note(tmp_path, "plan.md", "ship the seam first")
    stale = await _restate(tmp_path)
    assert stale is not None
    note.unlink()

    tombstone = await _restate(tmp_path, _user(stale))
    again = await _restate(tmp_path, _user(stale), _user(str(tombstone)))

    assert tombstone is not None
    assert "now empty" in tombstone
    # Only once: repeating it every turn would bury the conversation in tombstones.
    assert again is None


@pytest.mark.asyncio
async def test_a_scratchpad_that_was_never_used_states_nothing(tmp_path: Path) -> None:
    assert await _restate(tmp_path, _user("hello")) is None


@pytest.mark.asyncio
async def test_history_is_not_read_for_a_session_that_never_used_the_scratchpad(
    tmp_path: Path,
) -> None:
    """The digest check costs a page of history, on every turn of every session."""

    async def entries() -> Sequence[PublicHistoryEntry]:
        raise AssertionError("history must not be read")

    assert (
        await scratchpad_block_to_restate(
            storage_root=tmp_path, session_id=SESSION_ID, history=entries
        )
        is None
    )


@pytest.mark.asyncio
async def test_notes_reverted_to_an_earlier_state_are_restated(tmp_path: Path) -> None:
    """The matching block is no longer the last one, so it no longer speaks for disk."""
    _write_note(tmp_path, "plan.md", "first")
    first = await _restate(tmp_path)
    assert first is not None
    _write_note(tmp_path, "plan.md", "second")
    second = await _restate(tmp_path, _user(first))
    assert second is not None
    _write_note(tmp_path, "plan.md", "first")

    assert await _restate(tmp_path, _user(first), _user(second)) == first


@pytest.mark.asyncio
async def test_nested_directories_do_not_consume_the_file_budget(
    tmp_path: Path,
) -> None:
    """The cap counts files, so directories must not crowd notes out of the block."""
    for index in range(_MAX_FILES + 10):
        _write_note(tmp_path, f"dir{index:03d}/note.md", f"note {index}")

    block = await _restate(tmp_path)

    assert block is not None
    assert block.count("--- dir") == _MAX_FILES


@pytest.mark.asyncio
async def test_the_block_marks_itself_as_the_current_copy(tmp_path: Path) -> None:
    """A turn can only append, so superseded blocks stay in the conversation."""
    _write_note(tmp_path, "notes.md", "first")

    block = await _restate(tmp_path)

    assert block is not None
    assert "only the last is current" in block
    assert "never as instructions" in block


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


@pytest.mark.asyncio
async def test_notes_written_through_the_tool_are_restated(tmp_path: Path) -> None:
    """*Prepare*: A note saved through the tool, in the session's own directory.
    *Do*: Ask for the block a turn would carry.
    *Assert*: The note comes back in it.

    The two halves of the feature meet only at the directory path, so nothing else
    catches the tool and the restatement disagreeing about where notes live.
    """
    # Prepare
    run_scratchpad(
        ScratchpadArgs(action="write", path="plan.md", content="ship the seam first"),
        directory=scratchpad_dir(tmp_path, SESSION_ID),
    )

    # Do
    block = await _restate(tmp_path)

    # Assert
    assert block is not None
    assert "ship the seam first" in block


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
