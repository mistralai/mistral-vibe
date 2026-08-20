from __future__ import annotations

from datetime import timedelta
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, cast

from git import Repo
from git.exc import GitCommandError
import pytest

from vibe.core.git.errors import GitError
import vibe.core.git.repo as git_repo_module
from vibe.core.git.worktree import (
    LinkedWorktree,
    ManagedWorktree,
    PreparedWorktree,
    WorktreeError,
    WorktreeRelease,
    WorktreeReleaseOutcome,
    WorktreeRepository,
)
from vibe.core.git.worktree.record import (
    WorktreeClaim,
    WorktreeRecord,
    managed_bucket_name,
)
import vibe.core.git.worktree.repository as worktree_module

# The API is scoped to a repository or to one managed worktree, so these keep a
# test that only cares about the outcome down to a single call.


def _prepare(name: str, base: Path, *, branch: str | None = None) -> PreparedWorktree:
    with WorktreeRepository.open(base) as repository:
        return repository.prepare(name, branch=branch)


def _prepare_auto(
    base: Path, *, prompt: str | None = None, suggested_name: str | None = None
) -> PreparedWorktree:
    with WorktreeRepository.open(base) as repository:
        return repository.prepare_auto(prompt=prompt, suggested_name=suggested_name)


def _linked(base: Path) -> tuple[LinkedWorktree, ...]:
    with WorktreeRepository.open(base) as repository:
        return repository.linked()


def _hold(cwd: Path, session_id: str) -> None:
    if managed := ManagedWorktree.at(cwd):
        managed.hold(session_id)


def _holders(cwd: Path) -> frozenset[str]:
    managed = ManagedWorktree.at(cwd)
    return frozenset() if managed is None else managed.holders()


def _release(cwd: Path, session_id: str | None = None) -> WorktreeRelease:
    managed = ManagedWorktree.at(cwd)
    if managed is None:
        return WorktreeRelease(WorktreeReleaseOutcome.KEPT_UNMANAGED)
    return managed.release(session_id)


def _init_repo(root: Path, *, separate_git_dir: Path | None = None) -> Repo:
    repo = Repo.init(
        root,
        initial_branch="main",
        separate_git_dir=separate_git_dir,
        allow_unsafe_options=True,
    )
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


def _commit_off_head(repo: Repo, message: str) -> Any:
    """Commit on a side branch and rewind, leaving it unreachable from HEAD."""
    original = repo.active_branch
    side = repo.create_head("side")
    repo.head.reference = side
    (Path(repo.working_dir) / "side.txt").write_text(f"{message}\n")
    repo.index.add(["side.txt"])
    commit = repo.index.commit(message)
    repo.head.reference = original
    repo.head.reset(index=True, working_tree=True)
    repo.delete_head(side, force=True)
    return commit


def _managed_worktree_root(repo: Repo) -> Path:
    paths = git_repo_module.GitRepo(repo).paths
    return worktree_module._worktree_root(paths.repo_root, paths.common_git_dir)


def _claim(repo: Repo, name: str) -> WorktreeClaim:
    paths = git_repo_module.GitRepo(repo).paths
    bucket = managed_bucket_name(paths.repo_root, paths.common_git_dir)
    return WorktreeClaim(bucket=bucket, name=name)


def test_creates_named_worktree_for_separate_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    worktree = _prepare("feature-worktree", tmp_path, branch="feat/feature")

    assert worktree.name == "feature-worktree"
    assert worktree.branch == "feat/feature"
    assert Repo(worktree.root).active_branch.name == "feat/feature"
    assert "feat/feature" in (head.name for head in repo.heads)


def test_prepare_worktree_forwards_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    path = _prepare("feature-worktree", tmp_path, branch="feat/feature").path

    assert Repo(path).active_branch.name == "feat/feature"
    assert "feat/feature" in (head.name for head in repo.heads)


def test_reuses_named_worktree_for_separate_branch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    first = _prepare("feature-worktree", tmp_path, branch="feat/feature")

    second = _prepare("feature-worktree", tmp_path, branch="feat/feature")

    assert second.root == first.root
    assert second.branch == first.branch
    assert second.created is False


def test_prepare_does_not_require_worktree_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)

    def fail_if_called(git: git_repo_module.GitRepo) -> tuple[object, ...]:
        raise AssertionError(git)

    monkeypatch.setattr(git_repo_module.GitRepo, "records", fail_if_called)

    worktree = _prepare("feature", tmp_path)

    assert worktree.root.is_dir()


def test_prepare_cleans_created_worktree_when_metadata_build_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)

    def fail_build_prepared(*_args: Any, **_kwargs: Any) -> object:
        raise RuntimeError("metadata failed")

    monkeypatch.setattr(WorktreeRepository, "_build_prepared", fail_build_prepared)

    with pytest.raises(RuntimeError, match="metadata failed"):
        _prepare("feature", tmp_path, branch="feat/feature")

    assert _linked(tmp_path) == ()
    assert "feat/feature" not in (head.name for head in repo.heads)


def test_build_prepared_wraps_invalid_head_metadata(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    target = tmp_path / "worktree"
    target.mkdir()

    with (
        WorktreeRepository.open(tmp_path) as repository,
        pytest.raises(GitError, match="Failed to inspect HEAD"),
    ):
        repository._build_prepared(
            "worktree", "feat/worktree", target, created=True, branch_created=True
        )


def test_lists_worktree_when_null_output_is_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    linked_root = tmp_path.parent / f"{tmp_path.name}-feature"
    repo.git.worktree("add", "-b", "feat/feature", str(linked_root))
    list_porcelain = git_repo_module.GitRepo._list_porcelain

    def unsupported(git: git_repo_module.GitRepo, *, null_terminated: bool) -> str:
        if null_terminated:
            raise GitCommandError("git worktree", 129)
        return list_porcelain(git, null_terminated=False)

    monkeypatch.setattr(git_repo_module.GitRepo, "_list_porcelain", unsupported)

    linked = _linked(tmp_path)

    assert [(item.branch, item.root) for item in linked] == [
        ("feat/feature", linked_root.resolve())
    ]


def test_worktree_listing_failure_is_not_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)

    def failed(git: git_repo_module.GitRepo, *, null_terminated: bool) -> str:
        raise GitCommandError("git worktree", 128)

    monkeypatch.setattr(git_repo_module.GitRepo, "_list_porcelain", failed)

    with pytest.raises(GitError, match="Failed to list git worktrees"):
        _linked(tmp_path)


def test_root_level_system_alias_is_not_an_unstable_path_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_alias = Path(Path.cwd().anchor) / "system-alias"
    original_is_symlink = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        return path == system_alias or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)

    assert (
        worktree_module._has_linked_path_component(system_alias / "repo" / "worktree")
        is False
    )


def test_cleanup_and_remove_use_separate_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare("feature-worktree", tmp_path, branch="feat/feature")
    worktree_repo = Repo(worktree.root)
    (worktree.root / "file.txt").write_text("changed\n")
    worktree_repo.index.add(["file.txt"])
    worktree_repo.index.commit("change")

    assert worktree.inspect_for_cleanup().new_commit_count == 1

    worktree.remove()

    assert not worktree.root.exists()
    assert "feat/feature" not in (head.name for head in repo.heads)


def test_auto_worktree_records_ownership(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    worktree = _prepare_auto(tmp_path)

    record = _claim(repo, worktree.name).read()
    assert record is not None
    assert record.branch == worktree.branch
    assert record.base_commit == worktree.base_commit
    assert record.branch_created is True
    assert record.repo_root == worktree.repo_root


def test_auto_worktree_records_the_claim_before_creating_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    add_worktree = git_repo_module.GitRepo.add_worktree
    seen: list[Any] = []

    def record_then_create(*args: Any, **kwargs: Any) -> None:
        # The reservation must already be on disk here: a crash during the add
        # is exactly what the sweep needs a breadcrumb for.
        seen.append(_claim(repo, args[1].name).read())
        add_worktree(*args, **kwargs)

    monkeypatch.setattr(git_repo_module.GitRepo, "add_worktree", record_then_create)

    _prepare_auto(tmp_path)

    assert len(seen) == 1
    assert seen[0] is not None
    assert seen[0].base_commit is None


def test_auto_worktree_discards_its_record_when_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)

    def fail_create(*args: Any, **kwargs: Any) -> None:
        raise WorktreeError("boom")

    monkeypatch.setattr(git_repo_module.GitRepo, "add_worktree", fail_create)

    with pytest.raises(WorktreeError):
        _prepare_auto(tmp_path)

    assert all(
        _claim(repo, path.name).read() is None
        for path in _managed_worktree_root(repo).iterdir()
    )


def test_named_worktree_records_ownership(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    worktree = _prepare("feature-worktree", tmp_path)

    record = _claim(repo, "feature-worktree").read()
    assert record is not None
    assert record.base_commit == worktree.base_commit
    assert record.branch_created is True


def test_reused_worktree_does_not_record_ownership(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    repo.create_head("feat/existing")
    _prepare("feature-worktree", tmp_path, branch="feat/existing")
    shutil.rmtree(worktree_module.WORKTREES_DIR.path / ".claims")

    reused = _prepare("feature-worktree", tmp_path, branch="feat/existing")

    assert reused.created is False
    assert _claim(repo, "feature-worktree").read() is None


def test_named_worktree_discards_its_record_when_metadata_build_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)

    def fail_build(*args: Any, **kwargs: Any) -> None:
        raise WorktreeError("boom")

    monkeypatch.setattr(WorktreeRepository, "_build_prepared", fail_build)

    with pytest.raises(WorktreeError):
        _prepare("feature-worktree", tmp_path)

    assert _claim(repo, "feature-worktree").read() is None


def test_release_removes_a_clean_worktree_and_its_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert release.branch_deleted is True
    assert not worktree.root.exists()
    assert worktree.branch not in (head.name for head in repo.heads)
    assert _claim(repo, worktree.name).read() is None


@pytest.mark.parametrize("dirt", ["uncommitted", "untracked", "commit"])
def test_release_keeps_a_worktree_holding_work(tmp_path: Path, dirt: str) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")
    match dirt:
        case "uncommitted":
            (worktree.root / "file.txt").write_text("changed\n")
        case "untracked":
            (worktree.root / "new.txt").write_text("new\n")
        case "commit":
            worktree_repo = Repo(worktree.root)
            (worktree.root / "file.txt").write_text("changed\n")
            worktree_repo.index.add(["file.txt"])
            worktree_repo.index.commit("change")

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.KEPT_DIRTY
    assert release.reasons
    assert worktree.root.is_dir()
    assert worktree.branch in (head.name for head in repo.heads)
    assert _claim(repo, worktree.name).read() is not None


def test_release_keeps_a_worktree_another_session_still_holds(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")
    _hold(worktree.root, "session-b")

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.KEPT_IN_USE
    assert worktree.root.is_dir()

    # The last holder leaving is what allows removal.
    assert (
        _release(worktree.root, "session-b").outcome is WorktreeReleaseOutcome.REMOVED
    )
    assert not worktree.root.exists()


def test_release_ignores_a_worktree_vibe_did_not_create(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    linked_root = tmp_path.parent / f"{tmp_path.name}-feature"
    repo.git.worktree("add", "-b", "feat/feature", str(linked_root))

    release = _release(linked_root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.KEPT_UNMANAGED
    assert linked_root.is_dir()


def test_release_ignores_a_managed_worktree_without_a_record(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    shutil.rmtree(worktree_module.WORKTREES_DIR.path / ".claims")

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.KEPT_UNMANAGED
    assert worktree.root.is_dir()
    assert worktree.branch in (head.name for head in repo.heads)


def test_release_forgets_a_worktree_removed_behind_its_back(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    repo.git.worktree("remove", "--force", str(worktree.root))

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.NOT_FOUND
    assert _claim(repo, worktree.name).read() is None


def test_hold_ignores_a_worktree_vibe_did_not_create(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    linked_root = tmp_path.parent / f"{tmp_path.name}-feature"
    repo.git.worktree("add", "-b", "feat/feature", str(linked_root))

    _hold(linked_root, "session-a")

    assert not (worktree_module.WORKTREES_DIR.path / ".claims").exists()


def test_worktree_holders_reports_the_sessions_currently_inside(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    assert _holders(worktree.root) == frozenset()

    _hold(worktree.root, "session-a")
    _hold(worktree.root, "session-b")

    assert _holders(worktree.root) == frozenset({"session-a", "session-b"})
    assert _holders(tmp_path) == frozenset()


def _age_claim(claim: WorktreeClaim, minutes: int) -> None:
    record = claim.read()
    assert record is not None
    claim.write(
        record.model_copy(
            update={"claimed_at": record.claimed_at - timedelta(minutes=minutes)}
        )
    )


def test_sweep_keeps_an_abandoned_clean_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _age_claim(_claim(repo, worktree.name), 30)

    WorktreeRepository.sweep_claims(tmp_path)

    # An app-server that died leaves no way to know the session was finished
    # with, and its session is still on disk and resumable. Only the user
    # deleting the session removes a worktree that exists.
    assert worktree.root.is_dir()
    assert worktree.branch in (head.name for head in repo.heads)
    assert _claim(repo, worktree.name).read() is not None


def test_sweep_spares_a_claim_inside_the_grace_period(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)

    WorktreeRepository.sweep_claims(tmp_path)

    # A session that finished `git worktree add` has no holder until it
    # attaches; sweeping here would delete the worktree it is starting in.
    assert worktree.root.is_dir()
    assert _claim(repo, worktree.name).read() is not None


def test_sweep_spares_a_held_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")
    _age_claim(_claim(repo, worktree.name), 30)

    WorktreeRepository.sweep_claims(tmp_path)

    assert worktree.root.is_dir()


def test_sweep_spares_an_abandoned_worktree_holding_work(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    (worktree.root / "unsaved.txt").write_text("work\n")
    _age_claim(_claim(repo, worktree.name), 30)

    WorktreeRepository.sweep_claims(tmp_path)

    assert worktree.root.is_dir()
    assert worktree.branch in (head.name for head in repo.heads)


def test_sweep_never_touches_a_directory_without_a_claim(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    shutil.rmtree(worktree_module.WORKTREES_DIR.path / ".claims")

    WorktreeRepository.sweep_claims(tmp_path)

    # This is the live mkdir reservation case too: no claim means not ours.
    assert worktree.root.is_dir()
    assert worktree.branch in (head.name for head in repo.heads)


def test_sweep_discards_a_reservation_that_never_became_a_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)

    def fail_create(*args: Any, **kwargs: Any) -> None:
        raise WorktreeError("crashed before the add finished")

    monkeypatch.setattr(git_repo_module.GitRepo, "add_worktree", fail_create)
    with pytest.raises(WorktreeError):
        _prepare_auto(tmp_path)
    # Rebuild the reservation the crash would have stranded. Not undoing the
    # patch first: monkeypatch.undo() reverts what the fixtures set too, and
    # the redirected VIBE_HOME is one of them, so everything written after it
    # lands in the developer's real home.
    reservation = _managed_worktree_root(repo) / "stranded"
    reservation.mkdir(parents=True)
    _claim(repo, "stranded").write(
        WorktreeRecord.new(
            name="stranded",
            branch="vibe/stranded",
            repo_root=tmp_path,
            branch_created=False,
        )
    )
    _age_claim(_claim(repo, "stranded"), 30)

    WorktreeRepository.sweep_claims(tmp_path)

    assert not reservation.exists()
    assert _claim(repo, "stranded").read() is None


def test_sweep_keeps_a_populated_worktree_whose_claim_has_no_base_commit(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    record = _claim(repo, worktree.name).read()
    assert record is not None
    # A process killed between `git worktree add` and the second record write.
    _claim(repo, worktree.name).write(
        record.model_copy(
            update={
                "base_commit": None,
                "claimed_at": record.claimed_at - timedelta(minutes=30),
            }
        )
    )

    WorktreeRepository.sweep_claims(tmp_path)

    # Discarding here would rmdir-fail on the populated directory and drop the
    # record, orphaning the worktree for good.
    assert worktree.root.is_dir()
    assert _claim(repo, worktree.name).read() is not None


def test_release_keeps_a_worktree_claimed_while_it_was_inspected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")
    inspect = PreparedWorktree.inspect_for_cleanup

    def join_during_inspection(prepared: Any) -> Any:
        # A second session in another process lands between the holder check
        # and the removal.
        _hold(worktree.root, "session-b")
        return inspect(prepared)

    monkeypatch.setattr(PreparedWorktree, "inspect_for_cleanup", join_during_inspection)

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.KEPT_IN_USE
    assert worktree.root.is_dir()
    assert worktree.branch in (head.name for head in repo.heads)


def test_sweep_ignores_a_non_git_path(tmp_path: Path) -> None:
    WorktreeRepository.sweep_claims(tmp_path)


def test_remove_worktree_does_not_change_the_process_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("feature-worktree", tmp_path)
    chdir_targets: list[str] = []
    monkeypatch.setattr(os, "chdir", lambda target: chdir_targets.append(str(target)))

    worktree.remove()

    assert chdir_targets == []
    assert not worktree.root.exists()


def test_leave_worktree_moves_out_of_the_worktree(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("feature-worktree", tmp_path)
    os.chdir(worktree.root)

    worktree.leave_if_current_directory()

    assert Path.cwd().resolve() == worktree.repo_root.resolve()


def test_leave_worktree_keeps_an_unrelated_directory(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("feature-worktree", tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.chdir(elsewhere)

    worktree.leave_if_current_directory()

    assert Path.cwd().resolve() == elsewhere.resolve()


def test_cleanup_ignores_ignored_files(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".cache/\n")
    repo.index.add([".gitignore"])
    repo.index.commit("ignore generated file")
    worktree = _prepare("feature", tmp_path)
    cache = worktree.root / ".cache"
    cache.mkdir()
    (cache / "first").write_text("important\n")
    (cache / "second").write_text("important\n")

    cleanup = worktree.inspect_for_cleanup()

    assert cleanup.is_clean is True


def test_cleanup_detects_untracked_files_when_git_config_hides_them(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    repo.config_writer().set_value("status", "showUntrackedFiles", "no").release()
    worktree = _prepare("feature", tmp_path)
    (worktree.root / "untracked.txt").write_text("important\n")

    cleanup = worktree.inspect_for_cleanup()

    assert cleanup.has_untracked_files is True
    assert cleanup.reasons == ("untracked files",)


def test_cleanup_detects_detached_head_commit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("feature", tmp_path)
    worktree_repo = Repo(worktree.root)
    worktree_repo.git.checkout("--detach")
    (worktree.root / "file.txt").write_text("detached change\n")
    worktree_repo.index.add(["file.txt"])
    worktree_repo.index.commit("detached change")

    cleanup = worktree.inspect_for_cleanup()

    assert cleanup.new_commit_count == 1


def test_lists_linked_worktrees_at_matching_project_subdirectory(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    project = tmp_path / "packages" / "app"
    project.mkdir(parents=True)
    (project / "app.py").write_text("print('hello')\n")
    repo.index.add(["packages/app/app.py"])
    repo.index.commit("add project")
    first = _prepare("first", project, branch="feat/first")
    second = _prepare("second", project, branch="feat/second")

    linked = _linked(project)

    assert [(item.name, item.branch, item.path) for item in linked] == [
        ("first", "feat/first", first.path),
        ("second", "feat/second", second.path),
    ]


def test_lists_current_and_sibling_worktrees_from_linked_worktree(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    first = _prepare("first", tmp_path, branch="feat/first")
    second = _prepare("second", tmp_path, branch="feat/second")

    linked = _linked(first.path)

    assert [(item.name, item.branch, item.path) for item in linked] == [
        ("first", "feat/first", first.path),
        ("second", "feat/second", second.path),
    ]
    assert {item.repo_root for item in linked} == {tmp_path.resolve()}


def test_separate_git_dir_uses_primary_worktree_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    git_dir = tmp_path / "git-data"
    _init_repo(repo_root, separate_git_dir=git_dir)

    worktree = _prepare("feature-worktree", repo_root, branch="feat/feature")
    linked = _linked(repo_root)

    assert worktree.repo_root == repo_root.resolve()
    assert len(linked) == 1
    assert linked[0].name == worktree.name
    assert linked[0].branch == worktree.branch
    assert linked[0].root == worktree.root
    assert linked[0].path == worktree.path
    assert linked[0].repo_root == repo_root.resolve()


def test_separate_git_dir_linked_worktree_cannot_infer_primary_root(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    git_dir = tmp_path / "git-data"
    _init_repo(repo_root, separate_git_dir=git_dir)
    worktree = _prepare("feature-worktree", repo_root, branch="feat/feature")

    with pytest.raises(GitError, match="Cannot determine the primary checkout"):
        _linked(worktree.path)


def test_skips_worktree_when_project_path_escapes_through_symlink(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    project = tmp_path / "packages" / "app"
    project.mkdir(parents=True)
    (project / "app.py").write_text("print('hello')\n")
    repo.index.add(["packages/app/app.py"])
    repo.index.commit("add project")
    worktree = _prepare("escape", project, branch="feat/escape")
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(worktree.path)
    worktree.path.symlink_to(outside, target_is_directory=True)

    assert _linked(project) == ()


def test_skips_worktree_when_registered_root_is_replaced_by_symlink(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("feature", tmp_path, branch="feat/feature")
    shutil.rmtree(worktree.root)
    worktree.root.symlink_to(tmp_path, target_is_directory=True)

    assert _linked(tmp_path) == ()


def test_skips_worktree_when_registered_parent_is_replaced_by_symlink(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("feature", tmp_path, branch="feat/feature")
    registered_parent = worktree.root.parent
    moved_parent = tmp_path / "moved-worktrees"
    registered_parent.rename(moved_parent)
    registered_parent.symlink_to(moved_parent, target_is_directory=True)

    assert _linked(tmp_path) == ()


def test_rejects_managed_repository_directory_symlink_escape(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    common_git_dir = git_repo_module.GitRepo(repo).paths.common_git_dir
    managed_repo_root = worktree_module._worktree_root(
        tmp_path.resolve(), common_git_dir
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    managed_repo_root.parent.mkdir(parents=True, exist_ok=True)
    managed_repo_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorktreeError, match="resolves outside"):
        _prepare("feature", tmp_path, branch="feat/feature")


def test_skips_worktree_when_project_path_is_foreign_repository(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    project = tmp_path / "packages" / "app"
    project.mkdir(parents=True)
    (project / "app.py").write_text("print('hello')\n")
    repo.index.add(["packages/app/app.py"])
    repo.index.commit("add project")
    worktree = _prepare("foreign", project, branch="feat/foreign")
    Repo.init(worktree.path)

    assert _linked(project) == ()


def test_rejects_base_that_resolves_outside_worktree(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo_root.mkdir()
    outside.mkdir()
    _init_repo(repo_root)
    escaped = repo_root / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)

    with pytest.raises(GitError, match="outside git repository"):
        _linked(escaped)


def test_existing_worktree_with_malformed_rev_parse_output_raises_worktree_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("feature", tmp_path)
    original_git = worktree_module._git_python()

    class FakeGit:
        def rev_parse(self, *_args: object) -> str:
            return "only-one-line"

    class FakeRepo:
        git = FakeGit()

    def repo_factory(path: Path, *args: Any, **kwargs: Any) -> Repo:
        if path == worktree.root:
            return cast(Repo, FakeRepo())
        return original_git.repo(path, *args, **kwargs)

    monkeypatch.setattr(
        worktree_module,
        "_git_python",
        lambda: git_repo_module._GitPython(
            repo=cast(type[Repo], repo_factory),
            invalid_git_repository_error=original_git.invalid_git_repository_error,
            git_command_error=original_git.git_command_error,
            no_such_path_error=original_git.no_such_path_error,
        ),
    )

    with pytest.raises(WorktreeError, match="expected git rev-parse to return 2 lines"):
        _prepare("feature", tmp_path)


def test_missing_base_raises_worktree_error(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="not inside a git repository"):
        _linked(tmp_path / "missing")


def test_rejects_invalid_branch_name(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    with pytest.raises(GitError, match="Invalid branch"):
        _prepare("feature", tmp_path, branch="invalid branch")


@pytest.mark.parametrize(
    "name",
    [
        "bad*name",
        "bad:name",
        "trailing.",
        "trailing ",
        "NUL",
        "con.txt",
        "CONIN$",
        "CONOUT$.txt",
        "bad\0name",
    ],
)
def test_rejects_nonportable_worktree_name(tmp_path: Path, name: str) -> None:
    _init_repo(tmp_path)

    with pytest.raises(WorktreeError, match="portable filename"):
        _prepare(name, tmp_path, branch="feat/feature")


@pytest.mark.parametrize("branch", ["", "invalid\0branch"])
def test_rejects_empty_or_nul_branch(tmp_path: Path, branch: str) -> None:
    _init_repo(tmp_path)

    with pytest.raises(GitError, match="Invalid branch"):
        _prepare("feature", tmp_path, branch=branch)


def test_create_error_identifies_worktree_and_branch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _prepare("first", tmp_path, branch="feat/shared")

    with pytest.raises(
        GitError, match="Failed to create worktree 'second' for branch 'feat/shared'"
    ):
        _prepare("second", tmp_path, branch="feat/shared")


def test_named_worktree_reserves_its_claim_before_creating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    create = git_repo_module.GitRepo.add_worktree
    reserved: WorktreeRecord | None = None

    def capture_the_reservation(*args: Any, **kwargs: Any) -> None:
        nonlocal reserved
        reserved = _claim(repo, "feature").read()
        create(*args, **kwargs)

    monkeypatch.setattr(
        git_repo_module.GitRepo, "add_worktree", capture_the_reservation
    )
    _prepare("feature", tmp_path)

    # What a crash mid-add would leave behind. Recorded only afterwards, the
    # worktree would exist with no claim naming it, and neither the sweep nor
    # release would ever recognise it as ours.
    assert reserved is not None
    assert reserved.base_commit is None


def test_named_worktree_removes_its_claim_when_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)

    def fail_create(*_args: Any, **_kwargs: Any) -> None:
        raise WorktreeError("create failed")

    monkeypatch.setattr(git_repo_module.GitRepo, "add_worktree", fail_create)

    with pytest.raises(WorktreeError, match="create failed"):
        _prepare("feature", tmp_path)

    assert _claim(repo, "feature").read() is None
    assert not (_managed_worktree_root(repo) / "feature").exists()


def test_named_worktree_keeps_a_branch_it_did_not_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    existing = _commit_off_head(repo, "work in progress")
    repo.create_head("feat/shared", commit=existing)

    def fail_create(*_args: Any, **_kwargs: Any) -> None:
        raise WorktreeError("create failed")

    monkeypatch.setattr(git_repo_module.GitRepo, "add_worktree", fail_create)

    with pytest.raises(WorktreeError, match="create failed"):
        _prepare("feature", tmp_path, branch="feat/shared")

    # Discarding the reservation must not take the user's branch with it. Only
    # the auto path always owns the branch it names.
    assert repo.heads["feat/shared"].commit == existing


def test_named_worktree_reserves_its_directory_before_its_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    target = _managed_worktree_root(repo) / "feature"
    reserved_first = False
    write = WorktreeClaim.write

    def capture(self: WorktreeClaim, record: WorktreeRecord) -> None:
        nonlocal reserved_first
        reserved_first = target.is_dir()
        write(self, record)

    monkeypatch.setattr(WorktreeClaim, "write", capture)
    _prepare("feature", tmp_path)

    # The directory is the atomic reservation. Without it two callers for one
    # name both write the claim and both run the add, and the loser's cleanup
    # deletes the record describing the winner's live worktree.
    assert reserved_first


def test_preparing_a_name_already_taken_keeps_its_claim(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    first = _prepare("feature", tmp_path)
    record = _claim(repo, "feature").read()
    assert record is not None

    second = _prepare("feature", tmp_path)

    # What the loser of a race sees. It has to read the worktree as already
    # prepared rather than tear down the claim that owns it.
    assert second.root == first.root
    assert _claim(repo, "feature").read() == record


def test_sweep_reclaims_a_named_reservation_whose_add_never_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)

    def die_mid_add(*_args: Any, **_kwargs: Any) -> None:
        # Not an Exception: _create discards the claim on those, and what is
        # under test is the claim a killed process leaves behind.
        raise KeyboardInterrupt

    monkeypatch.setattr(git_repo_module.GitRepo, "add_worktree", die_mid_add)
    with pytest.raises(KeyboardInterrupt):
        _prepare("feature", tmp_path)
    _age_claim(_claim(repo, "feature"), 30)

    WorktreeRepository.sweep_claims(tmp_path)

    # The sweep judges a reservation by whether its path is an empty directory,
    # so a claim naming a path that was never created reads as populated, and
    # is kept forever.
    assert _claim(repo, "feature").read() is None
    assert not (_managed_worktree_root(repo) / "feature").exists()


def test_the_bucket_tells_two_repositories_apart(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _init_repo(first)
    _init_repo(second)
    (first / "src").mkdir()

    assert WorktreeRepository.bucket_for(first) == WorktreeRepository.bucket_for(
        first / "src"
    )
    assert WorktreeRepository.bucket_for(first) != WorktreeRepository.bucket_for(second)
    assert WorktreeRepository.bucket_for(tmp_path / "not-a-repo") is None


def test_auto_worktree_derives_name_and_branch_from_prompt(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    worktree = _prepare_auto(tmp_path, prompt="Fix the login bug")

    assert worktree.name == "fix-the-login-bug"
    assert worktree.branch == "vibe/fix-the-login-bug"
    assert worktree.created is True
    assert worktree.branch_created is True
    assert Repo(worktree.root).active_branch.name == "vibe/fix-the-login-bug"
    assert "vibe/fix-the-login-bug" in (head.name for head in repo.heads)


def test_auto_worktree_prefers_a_suggested_name_over_the_prompt(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    worktree = _prepare_auto(
        tmp_path, prompt="Fix the login bug", suggested_name="repair-oauth-redirect"
    )

    assert worktree.name == "repair-oauth-redirect"
    assert worktree.branch == "vibe/repair-oauth-redirect"


# A model answers in prose however firmly the prompt asks for a slug, so its
# reply goes through the same slugifier as the raw prompt rather than a
# looser check that would let an unusable path segment reach git.
@pytest.mark.parametrize(
    ("suggested", "expected"),
    [
        ("Fix The Login Bug!", "fix-the-login-bug"),
        ("`fix-login-bug`", "fix-login-bug"),
        ("fix login bug\n", "fix-login-bug"),
    ],
)
def test_auto_worktree_sanitises_a_suggested_name(
    tmp_path: Path, suggested: str, expected: str
) -> None:
    _init_repo(tmp_path)

    worktree = _prepare_auto(tmp_path, suggested_name=suggested)

    assert worktree.name == expected


@pytest.mark.parametrize("suggested", ["", "   ", "!!!", "🙂", "nul"])
def test_auto_worktree_falls_back_when_a_suggestion_is_unusable(
    tmp_path: Path, suggested: str
) -> None:
    _init_repo(tmp_path)

    worktree = _prepare_auto(
        tmp_path, prompt="Fix the login bug", suggested_name=suggested
    )

    assert worktree.name == "fix-the-login-bug"


def test_auto_worktree_still_dedupes_a_suggested_name(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    first = _prepare_auto(tmp_path, suggested_name="repair-oauth")
    second = _prepare_auto(tmp_path, suggested_name="repair-oauth")

    assert first.name == "repair-oauth"
    assert second.name == "repair-oauth-2"
    assert first.root != second.root


def test_auto_worktree_uses_random_slug_without_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    monkeypatch.setattr(worktree_module, "create_slug", lambda: "brave-quiet-otter")

    worktree = _prepare_auto(tmp_path)

    assert worktree.name == "brave-quiet-otter"
    assert worktree.branch == "vibe/brave-quiet-otter"


@pytest.mark.parametrize("prompt", ["", "   ", "!!!", "作業ツリー", "🙂"])
def test_auto_worktree_uses_random_slug_for_unsluggable_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    _init_repo(tmp_path)
    monkeypatch.setattr(worktree_module, "create_slug", lambda: "brave-quiet-otter")

    assert _prepare_auto(tmp_path, prompt=prompt).name == ("brave-quiet-otter")


@pytest.mark.parametrize("prompt", ["nul", "COM1", "aux."])
def test_auto_worktree_uses_random_slug_for_reserved_device_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    _init_repo(tmp_path)
    monkeypatch.setattr(worktree_module, "create_slug", lambda: "brave-quiet-otter")

    assert _prepare_auto(tmp_path, prompt=prompt).name == ("brave-quiet-otter")


def test_auto_worktree_suffixes_when_the_name_is_taken(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    first = _prepare_auto(tmp_path, prompt="Fix the login bug")
    second = _prepare_auto(tmp_path, prompt="Fix the login bug")

    assert first.name == "fix-the-login-bug"
    assert second.name == "fix-the-login-bug-2"
    assert first.root != second.root
    branches = {head.name for head in repo.heads}
    assert {"vibe/fix-the-login-bug", "vibe/fix-the-login-bug-2"} <= branches


def test_auto_worktree_does_not_reuse_an_existing_worktree(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    existing = _prepare("fix-the-login-bug", tmp_path, branch="vibe/fix-the-login-bug")

    auto = _prepare_auto(tmp_path, prompt="Fix the login bug")

    assert auto.root != existing.root
    assert auto.created is True


def test_auto_worktree_skips_a_name_taken_by_a_bare_directory(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (_managed_worktree_root(repo) / "fix-the-login-bug").mkdir(parents=True)

    worktree = _prepare_auto(tmp_path, prompt="Fix the login bug")

    assert worktree.name == "fix-the-login-bug-2"


def test_auto_worktree_skips_a_name_whose_branch_already_exists(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    repo.create_head("vibe/fix-the-login-bug")

    worktree = _prepare_auto(tmp_path, prompt="Fix the login bug")

    assert worktree.name == "fix-the-login-bug-2"


def test_auto_worktree_removes_its_claim_when_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)

    def fail_create(*_args: Any, **_kwargs: Any) -> None:
        raise WorktreeError("create failed")

    monkeypatch.setattr(git_repo_module.GitRepo, "add_worktree", fail_create)

    with pytest.raises(WorktreeError, match="create failed"):
        _prepare_auto(tmp_path, prompt="Fix the login bug")

    assert not (_managed_worktree_root(repo) / "fix-the-login-bug").exists()


def test_auto_worktree_removes_the_branch_git_left_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    claim = WorktreeRepository._claim_auto_name

    def claim_then_lose_the_directory(
        *args: Any, **kwargs: Any
    ) -> tuple[str, str, Path]:
        name, branch, target = claim(*args, **kwargs)
        (target / "squatter").touch()
        return name, branch, target

    monkeypatch.setattr(
        WorktreeRepository, "_claim_auto_name", claim_then_lose_the_directory
    )

    with pytest.raises(GitError, match="Failed to create worktree"):
        _prepare_auto(tmp_path, prompt="Fix the login bug")

    # git creates the branch before it validates the path, so the add fails
    # with the branch already made. Leaving it would burn this name for good.
    assert "vibe/fix-the-login-bug" not in {head.name for head in repo.heads}


def test_auto_worktree_keeps_a_branch_it_did_not_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    unmerged = _commit_off_head(repo, "someone else's work")

    def lose_the_branch_race(*_args: Any, **_kwargs: Any) -> None:
        # Whoever won created it between branch_exists and the add, so the add
        # fails on a branch this call does not own.
        repo.create_head("vibe/fix-the-login-bug", commit=unmerged)
        raise WorktreeError("a branch named 'vibe/fix-the-login-bug' already exists")

    monkeypatch.setattr(git_repo_module.GitRepo, "add_worktree", lose_the_branch_race)

    with pytest.raises(WorktreeError, match="already exists"):
        _prepare_auto(tmp_path, prompt="Fix the login bug")

    head = repo.heads["vibe/fix-the-login-bug"]
    assert head.commit == unmerged


def test_auto_worktree_does_not_retry_after_a_git_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    calls = 0

    def fail_create(*_args: Any, **_kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        raise WorktreeError("disk full")

    monkeypatch.setattr(git_repo_module.GitRepo, "add_worktree", fail_create)

    with pytest.raises(WorktreeError, match="disk full"):
        _prepare_auto(tmp_path, prompt="Fix the login bug")

    assert calls == 1


def test_auto_worktree_raises_when_every_candidate_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(worktree_module, "_MAX_AUTO_WORKTREE_ATTEMPTS", 2)
    managed_root = _managed_worktree_root(repo)
    (managed_root / "fix-the-login-bug").mkdir(parents=True)
    (managed_root / "fix-the-login-bug-2").mkdir(parents=True)

    with pytest.raises(WorktreeError, match="Unable to find an unused worktree name"):
        _prepare_auto(tmp_path, prompt="Fix the login bug")


def test_auto_worktree_cleans_created_worktree_when_metadata_build_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)

    def fail_build_prepared(*_args: Any, **_kwargs: Any) -> object:
        raise RuntimeError("metadata failed")

    monkeypatch.setattr(WorktreeRepository, "_build_prepared", fail_build_prepared)

    with pytest.raises(RuntimeError, match="metadata failed"):
        _prepare_auto(tmp_path, prompt="Fix the login bug")

    assert _linked(tmp_path) == ()
    assert "vibe/fix-the-login-bug" not in (head.name for head in repo.heads)


def test_auto_worktree_preserves_source_subdirectory(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    package = tmp_path / "packages" / "app"
    package.mkdir(parents=True)
    (package / "main.py").write_text("print('hi')\n")
    repo.index.add(["packages/app/main.py"])
    repo.index.commit("add package")

    worktree = _prepare_auto(package, prompt="Fix the login bug")

    assert worktree.path == worktree.root / "packages" / "app"


def _clone_behind_origin(tmp_path: Path) -> tuple[Repo, str]:
    """A clone whose origin/main has moved on without it.

    Returns the clone and the commit only the remote knows about, which is what
    a new worktree branch should start from.
    """
    upstream = tmp_path / "upstream.git"
    Repo.init(upstream, bare=True, initial_branch="main")

    seed = _init_repo(tmp_path / "seed")
    seed.create_remote("origin", str(upstream))
    seed.git.push("origin", "main")

    clone = Repo.clone_from(str(upstream), str(tmp_path / "clone"))

    (Path(seed.working_dir) / "ahead.txt").write_text("ahead\n")
    seed.index.add(["ahead.txt"])
    ahead = seed.index.commit("moved on")
    seed.git.push("origin", "main")

    return clone, ahead.hexsha


def test_new_branch_starts_from_the_fetched_remote_default(tmp_path: Path) -> None:
    clone, ahead = _clone_behind_origin(tmp_path)
    root = Path(clone.working_dir)
    assert clone.head.commit.hexsha != ahead

    worktree = _prepare("feature", root)

    assert worktree.base_commit == ahead


def test_new_branch_falls_back_to_head_without_a_remote(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    worktree = _prepare("feature", tmp_path)

    assert worktree.base_commit == repo.head.commit.hexsha


def test_new_branch_uses_the_stale_remote_ref_when_the_fetch_fails(
    tmp_path: Path,
) -> None:
    clone, _ = _clone_behind_origin(tmp_path)
    root = Path(clone.working_dir)
    cloned_origin = clone.commit("origin/main").hexsha
    # Local HEAD moves so it is distinguishable from the remote-tracking ref,
    # which is the point: the fallback must not be HEAD.
    (root / "local.txt").write_text("local\n")
    clone.index.add(["local.txt"])
    local_head = clone.index.commit("local only")
    clone.remotes.origin.set_url(str(tmp_path / "gone.git"))

    worktree = _prepare("feature", root)

    assert worktree.base_commit == cloned_origin
    assert worktree.base_commit != local_head.hexsha


def test_existing_branch_is_checked_out_rather_than_rebased(tmp_path: Path) -> None:
    clone, _ = _clone_behind_origin(tmp_path)
    root = Path(clone.working_dir)
    existing = clone.create_head("existing")

    worktree = _prepare("feature", root, branch="existing")

    assert worktree.base_commit == existing.commit.hexsha


class _StubProcess:
    def __init__(self, *, hangs: bool) -> None:
        self._hangs = hangs
        self.returncode = 0
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        if self._hangs and timeout is not None:
            raise subprocess.TimeoutExpired("git fetch", timeout)
        return "", ""

    def kill(self) -> None:
        self.killed = True
        self._hangs = False


class _RecordingGit:
    """Stands in for GitPython's Git, whose __slots__ rule out patching it."""

    def __init__(self, *, hangs: bool = False) -> None:
        self.process = _StubProcess(hangs=hangs)
        self.fetch_kwargs: dict[str, Any] = {}

    def fetch(self, *_args: Any, **kwargs: Any) -> Any:
        self.fetch_kwargs = kwargs
        return type("_AutoInterrupt", (), {"proc": self.process})()


def _fetch_with(git: _RecordingGit) -> None:
    repo = cast(Any, type("_Repo", (), {"git": git})())
    git_repo_module.GitRepo(repo).fetch_branch("origin", "main")


def test_fetch_kills_a_remote_that_never_answers() -> None:
    git = _RecordingGit(hangs=True)

    with pytest.raises(GitError, match="Timed out"):
        _fetch_with(git)

    assert git.process.killed


def test_fetch_refuses_to_stop_and_ask_for_credentials() -> None:
    # A credential helper would block the fetch behind a prompt the user may
    # never see, and this refresh is optional enough to fail instead.
    git = _RecordingGit()

    _fetch_with(git)

    assert git.fetch_kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_fetch_does_not_use_the_timeout_windows_rejects() -> None:
    git = _RecordingGit()

    _fetch_with(git)

    assert "kill_after_timeout" not in git.fetch_kwargs
