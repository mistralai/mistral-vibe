from __future__ import annotations

from pathlib import Path

from vibe.core.config.harness_files import HarnessFilesManager


def _dirs(tmp_path: Path, *names: str) -> list[Path]:
    made = []
    for name in names:
        d = tmp_path / name
        d.mkdir()
        made.append(d.resolve())
    return made


def test_a_move_replaces_the_departure_root_rather_than_adding_to_it(
    tmp_path: Path,
) -> None:
    repo, worktree = _dirs(tmp_path, "repo", "worktree")
    started = HarnessFilesManager(sources=("project",)).for_session(
        repo, workspace_roots=[repo]
    )

    moved = started.moved_to(worktree)

    assert moved.cwd == worktree
    assert moved.project_roots == [worktree]


def test_a_sequence_of_moves_never_widens_the_root_set(tmp_path: Path) -> None:
    # What D3 forbids. for_session merges, so routing a move through it would
    # leave every directory the session had ever sat in still authorised.
    first, second, third = _dirs(tmp_path, "first", "second", "third")
    manager = HarnessFilesManager(sources=("project",)).for_session(
        first, workspace_roots=[first]
    )

    manager = manager.moved_to(second).moved_to(third)

    assert manager.project_roots == [third]


def test_roots_held_for_other_reasons_survive_a_move(tmp_path: Path) -> None:
    # The attachment cache and --add-dir entries are not the directory the
    # session sits in, so a move has no business revoking them.
    repo, worktree, attachments = _dirs(tmp_path, "repo", "worktree", "attachments")
    started = HarnessFilesManager(sources=("project",)).for_session(
        repo, workspace_roots=[repo, attachments]
    )

    moved = started.moved_to(worktree)

    assert moved.project_roots == [attachments, worktree]


def test_an_opted_in_root_survives_the_session_passing_through_it(
    tmp_path: Path,
) -> None:
    # Leaving a directory revokes it as the session's position, not as a root
    # the user opened. An --add-dir the session moves into and back out of has
    # to still be there afterwards, or the move has quietly taken it away.
    repo, attachments, worktree = _dirs(tmp_path, "repo", "attachments", "worktree")
    started = HarnessFilesManager(sources=("project",)).for_session(
        repo, workspace_roots=[repo, attachments]
    )

    moved = started.moved_to(attachments).moved_to(worktree)

    assert moved.project_roots == [attachments, worktree]


def test_moving_where_the_session_already_sits_is_not_a_widening(
    tmp_path: Path,
) -> None:
    (repo,) = _dirs(tmp_path, "repo")
    started = HarnessFilesManager(sources=("project",)).for_session(
        repo, workspace_roots=[repo]
    )

    assert started.moved_to(repo).project_roots == [repo]
