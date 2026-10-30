from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event, Lock
from typing import Any, cast

from git import Git, Repo
from git.exc import GitCommandError
import pytest

from vibe.core.git.errors import GitError
import vibe.core.git.repo as git_repo_module
from vibe.core.git.worktree import (
    SNAPSHOT_REF_PREFIX,
    LinkedWorktree,
    ManagedWorktree,
    PreparedWorktree,
    WorktreeError,
    WorktreeRelease,
    WorktreeReleaseOutcome,
    WorktreeRepository,
)
from vibe.core.git.worktree.record import (
    HOLDERS_DIR_NAME,
    REMOVING_HOLDER,
    WorktreeClaim,
    WorktreeRecord,
    WorktreeRecordError,
    WorktreeRecoveryRecord,
    managed_bucket_name,
)
import vibe.core.git.worktree.repository as worktree_module
from vibe.core.trusted_folders import TrustedFoldersManager

_OPEN_REPOS: list[Repo] = []


@pytest.fixture(autouse=True)
def _close_git_repos() -> Iterator[None]:
    yield
    for repo in _OPEN_REPOS:
        repo.close()
    _OPEN_REPOS.clear()


def _track_repo(repo: Repo) -> Repo:
    _OPEN_REPOS.append(repo)
    return repo


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


def _prepare_fork(base: Path, *, suggested_name: str) -> PreparedWorktree:
    with WorktreeRepository.open(base) as repository:
        return repository.prepare_fork(suggested_name=suggested_name)


def _finish_starts(*worktrees: PreparedWorktree) -> None:
    for worktree in worktrees:
        _hold(worktree.root, "test-session")
        managed = ManagedWorktree.at(worktree.root)
        assert managed is not None
        managed.release_holder("test-session")


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


def _write_hook(repo: Repo, marker: Path, name: str) -> None:
    # The marker has to be absolute: hooks run with the new worktree (or
    # wherever git runs them) as their cwd, so a relative marker would be
    # written somewhere else and the test would pass even when the hook
    # runs.
    hooks = Path(repo.git_dir) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / name
    hook.write_text(f'#!/bin/sh\necho ran > "{marker}"\n')
    hook.chmod(0o755)


def _skip_unless_control_fires(
    root: Path, marker: Path, *control: tuple[str, ...]
) -> None:
    # Control: every command here is one that would execute the thing under
    # test, so confirm this environment runs it at all. Without it the
    # assertions below could pass on a machine where it never runs.
    fired = False
    for args in control:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
        if marker.exists():
            fired = True
            marker.unlink()
    if not fired:
        pytest.skip("git did not execute the control command in this environment")


# Creating and then deleting a branch is the smallest pair of commands that
# both update a ref, which is what fires reference-transaction.
_REF_UPDATE_CONTROL = (
    ("branch", "ref-hook-control"),
    ("branch", "-D", "ref-hook-control"),
)


def _commit_on_head(repo: Repo, message: str) -> None:
    (Path(repo.working_dir) / "file.txt").write_text(f"{message}\n")
    repo.index.add(["file.txt"])
    repo.index.commit(message)


def _init_repo(root: Path, *, separate_git_dir: Path | None = None) -> Repo:
    repo = _track_repo(
        Repo.init(
            root,
            initial_branch="main",
            separate_git_dir=separate_git_dir,
            allow_unsafe_options=True,
        )
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


def test_worktree_creation_does_not_run_repository_hooks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    marker = tmp_path / "hook-ran"
    _write_hook(repo, marker, "post-checkout")
    _skip_unless_control_fires(
        tmp_path,
        marker,
        ("worktree", "add", str(tmp_path / "control"), "-b", "control"),
    )

    worktree = _prepare("hooked-worktree", tmp_path, branch="feat/hooked")

    assert not marker.exists()
    assert Path(worktree.root).is_dir()


def test_worktree_reuse_of_existing_branch_does_not_run_repository_hooks(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    repo.create_head("feat/existing")
    marker = tmp_path / "hook-ran"
    _write_hook(repo, marker, "post-checkout")
    _skip_unless_control_fires(
        tmp_path,
        marker,
        ("worktree", "add", str(tmp_path / "control"), "-b", "control"),
    )

    worktree = _prepare("existing-worktree", tmp_path, branch="feat/existing")

    assert not marker.exists()
    assert Path(worktree.root).is_dir()


def test_fetching_a_base_ref_does_not_run_repository_hooks(tmp_path: Path) -> None:
    origin = _init_repo(tmp_path / "origin")
    clone = _track_repo(Repo.clone_from(origin.working_dir, tmp_path / "clone"))
    marker = tmp_path / "hook-ran"
    _write_hook(clone, marker, "reference-transaction")
    _skip_unless_control_fires(Path(clone.working_dir), marker, *_REF_UPDATE_CONTROL)

    _commit_on_head(origin, "second")
    git_repo_module.GitRepo(clone).fetch_branch("origin", "main")

    assert not marker.exists()
    # The fetch really moved the ref; without a ref update no hook could
    # have fired anyway.
    assert clone.rev_parse("origin/main").hexsha == origin.head.commit.hexsha


@pytest.mark.parametrize("force", [True, False])
def test_branch_deletion_does_not_run_repository_hooks(
    tmp_path: Path, force: bool
) -> None:
    repo = _init_repo(tmp_path)
    marker = tmp_path / "hook-ran"
    _write_hook(repo, marker, "reference-transaction")
    _skip_unless_control_fires(tmp_path, marker, *_REF_UPDATE_CONTROL)

    # Points at HEAD, so the safe `-d` delete accepts it too.
    repo.create_head("feat/deleted")
    git_repo_module.GitRepo(repo).delete_branch("feat/deleted", force=force)

    assert not marker.exists()
    assert not git_repo_module.GitRepo(repo).branch_exists("feat/deleted")


def test_managed_worktree_removal_does_not_run_repository_hooks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    marker = tmp_path / "hook-ran"
    _write_hook(repo, marker, "reference-transaction")
    _skip_unless_control_fires(tmp_path, marker, *_REF_UPDATE_CONTROL)

    worktree = _prepare("removed-worktree", tmp_path, branch="feat/removed")
    worktree.remove()

    assert not marker.exists()
    assert not git_repo_module.GitRepo(repo).branch_exists("feat/removed")


def test_snapshotting_a_worktree_does_not_run_repository_hooks(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="snapshotted")
    marker = tmp_path / "hook-ran"
    _write_hook(repo, marker, "reference-transaction")
    _skip_unless_control_fires(Path(repo.working_dir), marker, *_REF_UPDATE_CONTROL)

    ref = worktree.snapshot()

    assert not marker.exists()
    # The snapshot really wrote the ref; without a ref update no hook could
    # have fired anyway.
    assert repo.rev_parse(ref).hexsha


def test_discarding_a_retained_snapshot_does_not_run_repository_hooks(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="discarded")
    _finish_starts(worktree)
    claim = _claim(repo, worktree.name)
    assert ManagedWorktree.prune(limit=0) == 1
    recovery = claim.read_recovery()
    assert recovery is not None
    marker = tmp_path / "hook-ran"
    _write_hook(repo, marker, "reference-transaction")
    _skip_unless_control_fires(Path(repo.working_dir), marker, *_REF_UPDATE_CONTROL)

    assert _release(worktree.root).outcome is WorktreeReleaseOutcome.REMOVED

    assert not marker.exists()
    # The discard really deleted the ref; without a ref update no hook could
    # have fired anyway.
    with pytest.raises(GitCommandError):
        repo.git.show_ref("--verify", recovery.snapshot_ref)


def test_worktree_creation_does_not_run_the_repository_fsmonitor_command(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    marker = tmp_path / "fsmonitor-ran"
    fsmonitor = tmp_path / "fsmonitor"
    fsmonitor.write_text(f'#!/bin/sh\necho ran > "{marker}"\n')
    fsmonitor.chmod(0o755)
    repo.config_writer().set_value("core", "fsmonitor", str(fsmonitor)).release()
    _skip_unless_control_fires(
        tmp_path,
        marker,
        ("worktree", "add", str(tmp_path / "control"), "-b", "control"),
    )

    worktree = _prepare("fsmonitor-worktree", tmp_path, branch="feat/fsmonitor")

    assert not marker.exists()
    assert Path(worktree.root).is_dir()


def _commit_filtered_text(repo: Repo, root: Path) -> None:
    (root / ".gitattributes").write_text("*.txt filter=probe\n")
    repo.index.add([".gitattributes"])
    repo.index.commit("select the probe filter")


def test_worktree_creation_does_not_run_a_repository_filter_driver(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    _commit_filtered_text(repo, tmp_path)
    marker = tmp_path / "filter-ran"
    smudge = tmp_path / "smudge"
    smudge.write_text(f'#!/bin/sh\necho ran > "{marker}"\ncat\n')
    smudge.chmod(0o755)
    with repo.config_writer() as config:
        config.set_value('filter "probe"', "smudge", str(smudge))
        config.set_value('filter "probe"', "required", "true")
    _skip_unless_control_fires(
        tmp_path,
        marker,
        ("worktree", "add", str(tmp_path / "control"), "-b", "control"),
    )

    worktree = _prepare("filtered-worktree", tmp_path, branch="feat/filtered")

    assert not marker.exists()
    assert (Path(worktree.root) / "file.txt").read_text() == "hello\n"


def test_worktree_creation_keeps_a_filter_driver_the_user_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text('[filter "probe"]\n\tsmudge = tr a-z A-Z\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    repo = _init_repo(tmp_path / "repo")
    _commit_filtered_text(repo, tmp_path / "repo")

    worktree = _prepare("user-filter", tmp_path / "repo", branch="feat/user-filter")

    assert (Path(worktree.root) / "file.txt").read_text() == "HELLO\n"


@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("condition", ["gitdir:./worktrees/", "onbranch:vibe/**"])
def test_worktree_creation_blocks_filters_in_worktree_conditional_includes(
    tmp_path: Path, condition: str, auto: bool
) -> None:
    root = tmp_path / "repo"
    repo = _init_repo(root)
    _commit_filtered_text(repo, root)
    marker = tmp_path / "conditional-filter-ran"
    smudge = tmp_path / "smudge"
    smudge.write_text(f'#!/bin/sh\necho ran > "{marker}"\ncat\n')
    smudge.chmod(0o755)
    included = root / "attacker-filters"
    included.write_text(f'[filter "probe"]\nsmudge = "{smudge}"\nrequired = true\n')
    with repo.config_writer() as config:
        config.set_value(f'includeIf "{condition}"', "path", "../attacker-filters")
    # Confirm Git activates the payload only after entering a linked worktree.
    control = tmp_path / "control"
    repo.git.worktree("add", str(control), "-b", "vibe/control")
    assert marker.exists()
    marker.unlink()
    repo.git.worktree("remove", "--force", str(control))

    worktree = (
        _prepare_auto(root, suggested_name="conditional-filter")
        if auto
        else _prepare("conditional-filter", root, branch="vibe/named-filter")
    )

    assert not marker.exists()
    assert (worktree.root / "file.txt").read_text() == "hello\n"


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize("driver", ["lfs", "user-lfs-alias"])
def test_release_handles_repository_lfs_extensions_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trusted: bool, driver: str
) -> None:
    from vibe.core.trusted_folders import trusted_folders_manager

    if shutil.which("git-lfs") is None:
        pytest.skip("git-lfs is not installed")
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        f'[filter "{driver}"]\n'
        "clean = git-lfs clean -- %f\n"
        "smudge = git-lfs smudge -- %f\n"
        "process = git-lfs filter-process\n"
        "required = true\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    root = tmp_path / "repo"
    repo = _init_repo(root)
    (root / ".gitattributes").write_text(f"*.txt filter={driver}\n")
    repo.git.add(".gitattributes", "file.txt")
    repo.index.commit("LFS pointer")
    worktree = _prepare_auto(root, suggested_name="lfs-extension")
    _hold(worktree.root, "session")
    marker = tmp_path / "lfs-extension-ran"
    clean = tmp_path / "extension-clean"
    clean.write_text(f'#!/bin/sh\necho ran > "{marker}"\ntr a-z A-Z\n')
    clean.chmod(0o755)
    with repo.config_writer() as config:
        config.set_value('lfs.extension "probe"', "clean", str(clean))
        config.set_value('lfs.extension "probe"', "priority", "1")
    # Exercise the real installed LFS extension, without fetching any object.
    subprocess.run(
        ["git", "lfs", "clean", "--", "file.txt"],
        cwd=root,
        input=b"control\n",
        check=True,
        capture_output=True,
    )
    assert marker.exists()
    marker.unlink()
    if trusted:
        trusted_folders_manager.add_trusted(root)
    (worktree.root / "file.txt").write_text("changed\n")

    release = _release(worktree.root, "session")

    assert marker.exists() is trusted
    if trusted:
        assert release.outcome is WorktreeReleaseOutcome.REMOVED
        assert release.snapshot_ref is not None
        saved = repo.commit(release.snapshot_ref).tree["file.txt"].data_stream.read()
        assert saved.startswith(b"version https://git-lfs.github.com/spec/v1\n")
    else:
        assert release.outcome is WorktreeReleaseOutcome.KEPT_DIRTY
        assert release.snapshot_ref is None
        assert (worktree.root / "file.txt").read_text() == "changed\n"


def test_conditional_filter_can_depend_on_a_global_remote_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text('[remote "user"]\nurl = https://example.invalid/repo\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    root = tmp_path / "repo"
    repo = _init_repo(root)
    _commit_filtered_text(repo, root)
    included = root / "attacker-filters"
    included.write_text('[filter "probe"]\nsmudge = tr a-z A-Z\n')
    repo.git.config(
        "includeIf.hasconfig:remote.*.url:https://example.invalid/**.path",
        "../attacker-filters",
    )
    try:
        repo.git.config("--includes", "--get-regexp", r"^filter\.")
    except GitCommandError:
        pytest.skip("Git does not support includeIf hasconfig")
    control = tmp_path / "control"
    repo.git.worktree("add", str(control), "-b", "control")
    assert (control / "file.txt").read_text() == "HELLO\n"
    repo.git.worktree("remove", "--force", str(control))

    # Exercise the guard directly: GitPython's unrelated config_reader recurses
    # on hasconfig includes while selecting checkout worker settings.
    target = tmp_path / "filtered-worktree"
    repo.git.worktree("add", "--no-checkout", str(target), "-b", "feat/filter")
    git_repo_module.sanitized_git(repo, cwd=target).checkout("--force")

    assert (target / "file.txt").read_text() == "hello\n"


def test_old_git_filter_discovery_falls_back_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text('[filter "probe"]\nsmudge = tr a-z A-Z\n')
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    _init_repo(tmp_path / "repo")
    execute = Git.execute

    def without_scopes(self: Git, command: Any, **kwargs: Any) -> Any:
        if "--show-scope" in command:
            raise GitCommandError(command, 129, stderr="unknown option: show-scope")
        return execute(self, command, **kwargs)

    monkeypatch.setattr(Git, "execute", without_scopes)
    repo = _track_repo(Repo(tmp_path / "repo"))

    overrides = git_repo_module._repository_filter_overrides(repo)

    assert "filter.probe.smudge=" in overrides
    assert "filter.probe.required=false" in overrides


def test_git_filter_discovery_errors_do_not_run_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    execute = Git.execute
    status_called = False

    def failing_config(self: Git, command: Any, **kwargs: Any) -> Any:
        nonlocal status_called
        if "--show-scope" in command:
            raise GitCommandError(command, 128, stderr="bad config")
        if "status" in command:
            status_called = True
        return execute(self, command, **kwargs)

    monkeypatch.setattr(Git, "execute", failing_config)
    with pytest.raises(GitError, match="Failed to read"):
        git_repo_module.sanitized_git(repo).status("--porcelain")
    assert not status_called


def test_worktree_creation_keeps_a_trusted_repository_filter_driver(
    tmp_path: Path,
) -> None:
    from vibe.core.trusted_folders import trusted_folders_manager

    repo = _init_repo(tmp_path)
    _commit_filtered_text(repo, tmp_path)
    with repo.config_writer() as config:
        config.set_value('filter "probe"', "smudge", "tr a-z A-Z")
    trusted_folders_manager.trust_for_session(tmp_path)

    worktree = _prepare("trusted-filter", tmp_path, branch="feat/trusted-filter")

    assert (Path(worktree.root) / "file.txt").read_text() == "HELLO\n"


def test_status_stays_out_of_an_untrusted_repository_s_submodules(
    tmp_path: Path,
) -> None:
    from vibe.core.trusted_folders import trusted_folders_manager

    repo = _init_repo(tmp_path)

    assert git_repo_module.untrusted_status_options(repo) == (
        "--ignore-submodules=all",
    )
    trusted_folders_manager.trust_for_session(tmp_path)
    assert git_repo_module.untrusted_status_options(repo) == ()


def test_worktree_checkout_and_snapshot_use_the_session_trust_store(
    tmp_path: Path,
) -> None:
    from vibe.core.trusted_folders import trusted_folders_manager

    root = tmp_path / "repo"
    repo = _init_repo(root)
    _commit_filtered_text(repo, root)
    with repo.config_writer() as config:
        config.set_value('filter "probe"', "smudge", "tr a-z A-Z")
        config.set_value('filter "probe"', "clean", "tr A-Z a-z")
    store = trusted_folders_manager.for_session()
    store.trust_for_session(root)
    assert trusted_folders_manager.is_trusted(root) is not True

    with WorktreeRepository.open(root, trust_store=store) as repository:
        worktree = repository.prepare("session-trust")
    assert (worktree.root / "file.txt").read_text() == "HELLO\n"
    (worktree.root / "file.txt").write_text("CHANGED\n")
    ref = worktree.snapshot()
    assert repo.git.show(f"{ref}:file.txt") == "changed"


def test_snapshot_uses_persisted_source_trust(tmp_path: Path) -> None:
    from vibe.core.trusted_folders import trusted_folders_manager

    root = tmp_path / "repo"
    repo = _init_repo(root)
    _commit_filtered_text(repo, root)
    worktree = _prepare_auto(root, suggested_name="source-trust")
    with repo.config_writer() as config:
        config.set_value('filter "probe"', "clean", "tr a-z A-Z")
    trusted_folders_manager.add_trusted(root)
    (worktree.root / "file.txt").write_text("changed\n")
    ref = worktree.snapshot()
    assert repo.git.show(f"{ref}:file.txt") == "CHANGED"


def test_trusting_the_managed_parent_does_not_trust_the_source(tmp_path: Path) -> None:
    from vibe.core.trusted_folders import trusted_folders_manager

    root = tmp_path / "repo"
    repo = _init_repo(root)
    _commit_filtered_text(repo, root)
    worktree = _prepare_auto(root, suggested_name="untrusted-source")
    trusted_folders_manager.add_trusted(worktree.root.parent)
    trusted_folders_manager.add_untrusted(root)
    with repo.config_writer() as config:
        config.set_value('filter "probe"', "clean", "tr a-z A-Z")
    (worktree.root / "file.txt").write_text("changed\n")
    with pytest.raises(WorktreeError, match="untrusted Git filters"):
        worktree.snapshot()
    assert (worktree.root / "file.txt").read_text() == "changed\n"


@pytest.mark.parametrize("operation", ["snapshot", "inspect_for_cleanup"])
def test_worktree_wraps_filter_discovery_errors(tmp_path: Path, operation: str) -> None:
    root = tmp_path / "repo"
    repo = _init_repo(root)
    worktree = _prepare_auto(root, suggested_name="bad-filter")
    repo.git.config("filter.a=b.clean", "cat")
    with pytest.raises(WorktreeError, match="filter"):
        getattr(worktree, operation)()


@pytest.mark.parametrize("removal", ["none", "staged", "committed"])
def test_untrusted_submodules_are_kept_without_inspecting_their_filters(
    tmp_path: Path, removal: str
) -> None:
    root = tmp_path / "repo"
    repo = _init_repo(root)
    repo.git.update_index(
        "--add", "--cacheinfo", f"160000,{repo.head.commit.hexsha},sub"
    )
    repo.index.commit("submodule")
    worktree = _prepare_auto(root, suggested_name="retained-checkout")
    _finish_starts(worktree)
    _init_repo(worktree.root / "sub")
    (worktree.root / "sub" / "file.txt").write_text("unsaved submodule work\n")
    if removal != "none":
        checkout = _track_repo(Repo(worktree.root))
        checkout.git.rm("--cached", "sub")
        if removal == "committed":
            checkout.index.commit("remove submodule from tracking")
    state = worktree.inspect_for_cleanup()
    assert state.has_uncommitted_changes
    with pytest.raises(WorktreeError) as refusal:
        worktree.snapshot()
    assert str(refusal.value) == (
        "Cannot safely snapshot untrusted submodule contents; keep the worktree."
    )
    assert refusal.value.__cause__ is None
    assert _release(worktree.root).outcome is WorktreeReleaseOutcome.KEPT_DIRTY
    assert worktree.root.is_dir()
    assert (
        worktree.root / "sub" / "file.txt"
    ).read_text() == "unsaved submodule work\n"


def test_session_git_trust_does_not_spread_to_other_sessions_or_storage(
    tmp_path: Path,
) -> None:
    from vibe.app_server._unified_harness_backend_adapter import _worktree_git_trust
    from vibe.core.trusted_folders import trusted_folders_manager

    root = tmp_path / "repo"
    _init_repo(root)
    store = trusted_folders_manager.for_session()
    store.trust_for_session(root)
    worktree = _prepare_auto(root, suggested_name="isolated-trust")
    git_store = _worktree_git_trust(root, store)
    store.revoke_session_trust(root)
    store.trust_for_session(worktree.root)
    assert git_store.is_trusted(root) is True
    assert trusted_folders_manager.for_session().is_trusted(root) is not True
    assert _worktree_git_trust(worktree.root, store).is_trusted(root) is not True


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize(
    "destination_kind", ["same", "subdirectory", "unrelated", "missing"]
)
def test_relocation_git_trust_is_scoped_to_the_source_repository(
    tmp_path: Path,
    trusted: bool,
    destination_kind: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from vibe.app_server._unified_harness_backend_adapter import (
        _relocated_worktree_git_trust,
    )
    from vibe.core.trusted_folders import trusted_folders_manager

    root = tmp_path / "repo"
    _init_repo(root)
    prepared = _prepare_auto(root, suggested_name="source-checkout")
    previous_store = trusted_folders_manager.for_session()
    if trusted:
        previous_store.trust_for_session(root)
    store = trusted_folders_manager.for_session()
    if destination_kind in {"same", "subdirectory"}:
        destination = prepared.root
        if destination_kind == "subdirectory":
            destination = destination / "nested"
            destination.mkdir()
    else:
        destination = tmp_path / destination_kind
        if destination_kind == "unrelated":
            _init_repo(destination)
    store.trust_for_session(destination)
    caplog.set_level("DEBUG")
    scoped = _relocated_worktree_git_trust(root, destination, previous_store, store)
    if destination_kind == "missing":
        assert "Dropping relocated source Git trust" in caplog.text
        assert str(root) in caplog.text and str(destination) in caplog.text
    expected_source_trust = trusted and destination_kind in {"same", "subdirectory"}
    assert (scoped.is_trusted(root) is True) == expected_source_trust
    if destination_kind == "unrelated":
        assert scoped.is_trusted(destination) is True
    elif destination_kind == "missing":
        assert scoped.is_trusted(destination) is not True
    assert store.is_trusted(root) is not True
    assert trusted_folders_manager.is_trusted(root) is not True


@pytest.mark.parametrize("operation", ["remove", "forget", "discard"])
def test_ref_cleanup_honors_explicit_session_git_trust(
    tmp_path: Path, operation: str
) -> None:
    from vibe.core.trusted_folders import trusted_folders_manager

    root = tmp_path / "repo"
    repo = _init_repo(root)
    store = trusted_folders_manager.for_session()
    store.trust_for_session(root)
    with WorktreeRepository.open(root, trust_store=store) as repository:
        prepared = repository.prepare("cleanup")
    _finish_starts(prepared)
    # An unoverridable filter name fails sanitization only without the grant.
    repo.git.config("filter.a=b.clean", "cat")
    assert trusted_folders_manager.is_trusted(root) is not True
    if operation == "remove":
        prepared.remove()
        assert not prepared.root.exists()
    elif operation == "forget":
        admin = worktree_module._checkout_admin_dir(prepared.root)
        shutil.rmtree(prepared.root)
        worktree_module._forget_checkout(
            admin, root, prepared.branch, trust_store=store
        )
    else:
        managed = ManagedWorktree.at(prepared.root)
        assert managed is not None
        record = managed.claim.read()
        assert record is not None
        ref = prepared.snapshot()
        recovery = WorktreeRecoveryRecord.new(record, snapshot_ref=ref)
        managed.claim.write_recovery(recovery)
        managed._discard_retained_snapshot(recovery, trust_store=store)
        assert not managed.claim.has_recovery()
        assert ref not in repo.git.for_each_ref("--format=%(refname)").splitlines()
        return
    assert prepared.branch not in (head.name for head in repo.heads)


def test_creates_named_worktree_for_separate_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    worktree = _prepare("feature-worktree", tmp_path, branch="feat/feature")

    assert worktree.name == "feature-worktree"
    assert worktree.branch == "feat/feature"
    assert _track_repo(Repo(worktree.root)).active_branch.name == "feat/feature"
    assert "feat/feature" in (head.name for head in repo.heads)


def test_prepare_worktree_forwards_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    path = _prepare("feature-worktree", tmp_path, branch="feat/feature").path

    assert _track_repo(Repo(path)).active_branch.name == "feat/feature"
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
    worktree_repo = _track_repo(Repo(worktree.root))
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
    assert _claim(repo, worktree.name).is_starting() is True


def test_auto_worktree_stops_starting_when_held(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    claim = _claim(repo, worktree.name)

    _hold(worktree.root, "session-a")

    assert claim.is_starting() is False


def test_auto_worktree_records_the_claim_before_creating_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    add_worktree = git_repo_module.GitRepo.add_worktree
    seen: list[Any] = []

    def record_then_create(*args: Any, **kwargs: Any) -> None:
        # The reservation must already be on disk here so a concurrent creator
        # cannot claim the same managed worktree name while git is adding it.
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


@pytest.mark.parametrize(
    ("git_cli_repo", "block_ref_files"),
    [("files", True), ("reftable", False)],
    indirect=["git_cli_repo"],
)
def test_release_removes_the_branch_without_gitpython_ref_files(
    tmp_path: Path,
    git_cli_repo: Repo,
    block_ref_files: bool,
    request: pytest.FixtureRequest,
) -> None:
    # Reftable needs git 2.45 or later; blocking GitPython's ref file reads
    # checks the same code on any git.
    repo = _track_repo(git_cli_repo)
    repo.git.commit("--allow-empty", "-m", "initial")
    if block_ref_files:
        request.getfixturevalue("no_gitpython_ref_files")
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert release.branch_deleted is True
    assert repo.git.branch("--list", worktree.branch) == ""


@pytest.mark.parametrize("dirt", ["uncommitted", "untracked", "commit"])
def test_release_saves_the_work_before_removing_a_worktree(
    tmp_path: Path, dirt: str
) -> None:
    # Work left behind is a reason to save it, not to keep the directory: the
    # old rule kept the worktree and nothing ever collected it.
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")
    match dirt:
        case "uncommitted":
            (worktree.root / "file.txt").write_text("changed\n")
        case "untracked":
            (worktree.root / "new.txt").write_text("new\n")
        case "commit":
            worktree_repo = _track_repo(Repo(worktree.root))
            (worktree.root / "file.txt").write_text("changed\n")
            worktree_repo.index.add(["file.txt"])
            worktree_repo.index.commit("change")

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert not worktree.root.exists()
    assert _claim(repo, worktree.name).read() is None

    # The whole point of removing it anyway: the work is still reachable.
    assert release.snapshot_ref is not None
    saved = repo.commit(release.snapshot_ref)
    match dirt:
        case "uncommitted":
            assert saved.tree["file.txt"].data_stream.read() == b"changed\n"
        case "untracked":
            assert saved.tree["new.txt"].data_stream.read() == b"new\n"
        case "commit":
            # `git branch -D` orphaned it; the ref is what keeps it reachable.
            assert saved.parents[0].message.strip() == "change"


def test_release_keeps_a_worktree_whose_work_could_not_be_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed snapshot is the one remaining reason to keep: removing then
    # would be the data loss the snapshot exists to prevent.
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")
    (worktree.root / "new.txt").write_text("new\n")

    def _fail(self: PreparedWorktree) -> str:
        raise WorktreeError("no room on device")

    monkeypatch.setattr(PreparedWorktree, "snapshot", _fail)

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.KEPT_DIRTY
    assert release.reasons
    assert worktree.root.is_dir()
    assert worktree.branch in (head.name for head in repo.heads)
    assert _claim(repo, worktree.name).read() is not None


def test_release_leaves_no_snapshot_for_a_worktree_nothing_happened_in(
    tmp_path: Path,
) -> None:
    # None rather than a ref, so a caller can tell "nothing to recover" from
    # "recover here" without reading the commit.
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert release.snapshot_ref is None


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


def test_force_release_forgets_a_stale_claim_whose_root_is_gone(tmp_path: Path) -> None:
    # A crashed start can leave the starting marker behind with the root
    # already gone: the claim is forgotten, not kept in use forever.
    repo = _init_repo(tmp_path)
    target = _managed_worktree_root(repo) / "stale"
    target.mkdir(parents=True)
    claim = _claim(repo, "stale")
    claim.mark_starting()
    claim.write(
        WorktreeRecord.new(
            name="stale", branch="vibe/stale", repo_root=tmp_path, branch_created=True
        )
    )
    shutil.rmtree(target)

    stale = ManagedWorktree.at(target)
    assert stale is not None
    release = stale.force_release("session-a")

    assert release.outcome is WorktreeReleaseOutcome.NOT_FOUND
    assert claim.read() is None


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


def test_prune_snapshots_and_removes_the_oldest_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    oldest = _prepare_auto(Path(repo.working_dir), suggested_name="oldest")
    newest = _prepare_auto(Path(repo.working_dir), suggested_name="newest")
    _finish_starts(oldest, newest)
    _age_claim(_claim(repo, oldest.name), 30)

    removed = ManagedWorktree.prune(limit=1)

    assert removed == 1
    assert not oldest.root.exists()
    assert newest.root.is_dir()
    recovery = _claim(repo, oldest.name).read_recovery()
    assert recovery is not None
    saved = repo.commit(recovery.snapshot_ref)
    assert saved.parents[0].hexsha == oldest.base_commit


def test_prune_with_zero_removes_all_unheld_worktrees(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    first = _prepare_auto(Path(repo.working_dir), suggested_name="first")
    second = _prepare_auto(Path(repo.working_dir), suggested_name="second")
    _finish_starts(first, second)

    removed = ManagedWorktree.prune(limit=0)

    assert removed == 2
    assert not first.root.exists()
    assert not second.root.exists()


def test_explicit_release_discards_a_retained_snapshot(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="retained")
    _finish_starts(worktree)
    (worktree.root / "saved.txt").write_text("recover me\n")
    claim = _claim(repo, worktree.name)

    assert ManagedWorktree.prune(limit=0) == 1
    recovery = claim.read_recovery()
    assert recovery is not None
    assert repo.commit(recovery.snapshot_ref).hexsha

    assert (
        _release(worktree.root, "closed-session").outcome
        is WorktreeReleaseOutcome.KEPT_UNMANAGED
    )
    assert claim.read_recovery() is not None
    assert _release(worktree.root).outcome is WorktreeReleaseOutcome.REMOVED

    assert claim.read_recovery() is None
    assert not claim.directory.exists()
    with pytest.raises(GitCommandError):
        repo.git.show_ref("--verify", recovery.snapshot_ref)


def test_prune_keeps_a_worktree_that_has_not_been_held_yet(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    starting = _prepare_auto(Path(repo.working_dir), suggested_name="starting")
    removable = _prepare_auto(Path(repo.working_dir), suggested_name="removable")
    _finish_starts(removable)

    removed = ManagedWorktree.prune(limit=0)

    assert removed == 1
    assert starting.root.is_dir()
    assert not removable.root.exists()


def test_prune_keeps_a_worktree_held_while_a_session_attaches(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="reserved")
    _finish_starts(worktree)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    pending_hold = managed.hold_for_attachment()
    assert pending_hold is not None

    assert ManagedWorktree.prune(limit=0) == 0

    managed.hold("session-a", pending_hold)
    assert managed.holders() == frozenset({"session-a"})
    managed.release_holder("session-a")


def test_prune_keeps_an_incomplete_worktree_held_while_a_session_attaches(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    target = _managed_worktree_root(repo) / "reserved"
    target.mkdir(parents=True)
    repo.create_head("vibe/reserved")
    claim = _claim(repo, "reserved")
    claim.write(
        WorktreeRecord.new(
            name="reserved",
            branch="vibe/reserved",
            repo_root=tmp_path,
            branch_created=True,
        )
    )
    managed = ManagedWorktree(claim=claim)
    pending_hold = managed.hold_for_attachment()
    assert pending_hold is not None

    assert ManagedWorktree.prune(limit=0) == 0

    assert target.is_dir()
    assert claim.read() is not None
    assert "vibe/reserved" in (head.name for head in repo.heads)
    pending_hold.release()


def test_hold_preserves_a_record_completed_during_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="attaching")
    claim = _claim(repo, worktree.name)
    incomplete = claim.read()
    assert incomplete is not None
    claim.write(incomplete.model_copy(update={"base_commit": None}))
    managed = ManagedWorktree(claim=claim)
    pending_hold = managed.hold_for_attachment()
    assert pending_hold is not None
    add_holder = WorktreeClaim.add_holder

    def complete_during_add(current: WorktreeClaim, session_id: str) -> None:
        add_holder(current, session_id)
        if current == claim and session_id == "session-a":
            current.write(incomplete)

    monkeypatch.setattr(WorktreeClaim, "add_holder", complete_during_add)

    managed.hold("session-a", pending_hold)

    record = claim.read()
    assert record is not None
    assert record.base_commit == worktree.base_commit
    managed.release_holder("session-a")


def test_concurrent_prunes_do_not_remove_below_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    oldest = _prepare_auto(Path(repo.working_dir), suggested_name="oldest")
    newest = _prepare_auto(Path(repo.working_dir), suggested_name="newest")
    _finish_starts(oldest, newest)
    _age_claim(_claim(repo, oldest.name), 30)
    inspection_started = Event()
    release_inspection = Event()
    snapshot = PreparedWorktree.snapshot

    def pause_snapshot(prepared: PreparedWorktree) -> str:
        if prepared.name == oldest.name:
            inspection_started.set()
            assert release_inspection.wait(timeout=5)
        return snapshot(prepared)

    real_prune_lock = worktree_module.worktree_prune_lock
    lock_attempts = 0
    attempts_lock = Lock()
    second_started = Event()

    @contextmanager
    def observe_prune_lock() -> Iterator[None]:
        nonlocal lock_attempts
        with attempts_lock:
            lock_attempts += 1
            if lock_attempts == 2:
                second_started.set()
        with real_prune_lock():
            yield

    monkeypatch.setattr(PreparedWorktree, "snapshot", pause_snapshot)
    monkeypatch.setattr(worktree_module, "worktree_prune_lock", observe_prune_lock)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(ManagedWorktree.prune, 1)
        assert inspection_started.wait(timeout=5)
        second = executor.submit(ManagedWorktree.prune, 1)
        assert second_started.wait(timeout=5)
        release_inspection.set()
        assert sorted((first.result(timeout=10), second.result(timeout=10))) == [0, 1]

    assert not oldest.root.exists()
    assert newest.root.is_dir()


def test_prune_discards_an_abandoned_empty_reservation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = _managed_worktree_root(repo) / "stranded"
    target.mkdir(parents=True)
    repo.create_head("vibe/stranded")
    claim = _claim(repo, "stranded")
    claim.write(
        WorktreeRecord.new(
            name="stranded",
            branch="vibe/stranded",
            repo_root=tmp_path,
            branch_created=True,
        )
    )
    marker = claim.directory / "holders" / ".starting"
    marker.parent.mkdir()
    marker.touch()

    removed = ManagedWorktree.prune(limit=15)

    assert removed == 0
    assert not target.exists()
    assert claim.read() is None
    assert "vibe/stranded" not in (head.name for head in repo.heads)
    assert _prepare_auto(tmp_path, suggested_name="stranded").name == "stranded"


def test_prune_keeps_an_active_empty_reservation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    target = _managed_worktree_root(repo) / "starting"
    target.mkdir(parents=True)
    claim = _claim(repo, "starting")
    claim.mark_starting()
    claim.write(
        WorktreeRecord.new(
            name="starting",
            branch="vibe/starting",
            repo_root=tmp_path,
            branch_created=True,
        )
    )

    ManagedWorktree.prune(limit=0)

    assert target.is_dir()
    assert claim.read() is not None
    claim.delete()
    target.rmdir()


def test_prune_snapshots_a_populated_incomplete_reservation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="interrupted")
    claim = _claim(repo, worktree.name)
    record = claim.read()
    assert record is not None
    claim.write(record.model_copy(update={"base_commit": None}))
    claim.finish_starting()

    ManagedWorktree.prune(limit=0)

    assert not worktree.root.exists()
    assert claim.read() is None
    recovery = claim.read_recovery()
    assert recovery is not None
    assert repo.commit(recovery.snapshot_ref).hexsha


def test_prune_keeps_a_populated_incomplete_reservation_without_gitlink(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    target = _managed_worktree_root(repo) / "interrupted"
    target.mkdir(parents=True)
    (target / "partial.txt").write_text("partial\n")
    repo.create_head("vibe/interrupted")
    claim = _claim(repo, "interrupted")
    claim.write(
        WorktreeRecord.new(
            name="interrupted",
            branch="vibe/interrupted",
            repo_root=tmp_path,
            branch_created=True,
        )
    )

    ManagedWorktree.prune(limit=0)

    assert target.is_dir()
    assert (target / "partial.txt").read_text() == "partial\n"
    assert claim.read() is not None
    assert "vibe/interrupted" in (head.name for head in repo.heads)


def test_prune_snapshots_worktrees_with_unpushed_commits(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    unpushed = _prepare_auto(Path(repo.working_dir), suggested_name="unpushed")
    newest = _prepare_auto(Path(repo.working_dir), suggested_name="newest")
    _finish_starts(unpushed, newest)
    unpushed_repo = _track_repo(Repo(unpushed.root))
    (unpushed.root / "local.txt").write_text("local\n")
    unpushed_repo.index.add(["local.txt"])
    unpushed_repo.index.commit("local only")
    _age_claim(_claim(repo, unpushed.name), 30)

    removed = ManagedWorktree.prune(limit=1)

    assert removed == 1
    assert not unpushed.root.exists()
    assert newest.root.is_dir()
    recovery = _claim(repo, unpushed.name).read_recovery()
    assert recovery is not None
    saved = repo.commit(recovery.snapshot_ref)
    assert saved.tree["local.txt"].data_stream.read() == b"local\n"
    assert saved.parents[0].message.strip() == "local only"


def test_prune_snapshots_dirty_and_untracked_but_keeps_held_worktrees(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    dirty = _prepare_auto(Path(repo.working_dir), suggested_name="dirty")
    untracked = _prepare_auto(Path(repo.working_dir), suggested_name="untracked")
    held = _prepare_auto(Path(repo.working_dir), suggested_name="held")
    removable = _prepare_auto(Path(repo.working_dir), suggested_name="removable")
    newest = _prepare_auto(Path(repo.working_dir), suggested_name="newest")
    _finish_starts(dirty, untracked, held, removable, newest)
    (dirty.root / "file.txt").write_text("changed\n")
    (untracked.root / "untracked.txt").write_text("local\n")
    _hold(held.root, "session-a")
    _age_claim(_claim(repo, dirty.name), 50)
    _age_claim(_claim(repo, untracked.name), 40)
    _age_claim(_claim(repo, held.name), 30)
    _age_claim(_claim(repo, removable.name), 20)

    removed = ManagedWorktree.prune(limit=3)

    assert removed == 2
    assert not dirty.root.exists()
    assert not untracked.root.exists()
    assert held.root.is_dir()
    assert removable.root.is_dir()
    assert newest.root.is_dir()
    dirty_recovery = _claim(repo, dirty.name).read_recovery()
    untracked_recovery = _claim(repo, untracked.name).read_recovery()
    assert dirty_recovery is not None
    assert untracked_recovery is not None
    assert (
        repo.commit(dirty_recovery.snapshot_ref).tree["file.txt"].data_stream.read()
        == b"changed\n"
    )
    untracked_snapshot = repo.commit(untracked_recovery.snapshot_ref)
    assert untracked_snapshot.tree["untracked.txt"].data_stream.read() == b"local\n"


def test_prune_keeps_a_worktree_when_snapshot_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    kept = _prepare_auto(Path(repo.working_dir), suggested_name="kept")
    removed = _prepare_auto(Path(repo.working_dir), suggested_name="removed")
    _finish_starts(kept, removed)
    _age_claim(_claim(repo, kept.name), 30)
    _age_claim(_claim(repo, removed.name), 20)
    snapshot = PreparedWorktree.snapshot

    def fail_oldest(prepared: PreparedWorktree) -> str:
        if prepared.name == kept.name:
            raise WorktreeError("no room on device")
        return snapshot(prepared)

    monkeypatch.setattr(PreparedWorktree, "snapshot", fail_oldest)

    removed_count = ManagedWorktree.prune(limit=1)

    assert removed_count == 1
    assert kept.root.is_dir()
    assert _claim(repo, kept.name).read_recovery() is None
    assert not removed.root.exists()


def test_restore_recreates_a_pruned_worktree_from_its_snapshot(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    nested = worktree.root / "packages" / "app"
    nested.mkdir(parents=True)
    (nested / "saved.txt").write_text("recover me\n")

    assert ManagedWorktree.prune(limit=0) == 1
    assert not worktree.root.exists()

    managed = ManagedWorktree.at(nested)
    assert managed is not None
    assert managed.restore(nested) is True

    assert (nested / "saved.txt").read_text() == "recover me\n"
    assert _claim(repo, worktree.name).read_recovery() is None
    restored_record = _claim(repo, worktree.name).read()
    assert restored_record is not None
    assert restored_record.branch == worktree.branch


def test_restore_recreates_a_missing_empty_nested_cwd(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    nested = worktree.root / "packages" / "app"
    nested.mkdir(parents=True)

    assert ManagedWorktree.prune(limit=0) == 1
    assert not worktree.root.exists()

    managed = ManagedWorktree.at(nested)
    assert managed is not None
    assert managed.restore(nested) is True

    assert nested.is_dir()
    assert _claim(repo, worktree.name).read_recovery() is None


def test_restore_keeps_recovery_when_the_saved_directory_cannot_be_recreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    nested = (worktree.root / "packages" / "app").resolve()
    nested.mkdir(parents=True)

    assert ManagedWorktree.prune(limit=0) == 1
    mkdir = Path.mkdir

    def fail_saved_directory(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == nested:
            raise OSError("cannot recreate saved directory")
        mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_saved_directory)
    managed = ManagedWorktree.at(nested)
    assert managed is not None
    with pytest.raises(WorktreeError, match="does not contain the saved directory"):
        managed.restore(nested)

    assert worktree.root.is_dir()
    assert _claim(repo, worktree.name).read_recovery() is not None
    assert _claim(repo, worktree.name).is_starting() is False


def test_restore_replaces_an_empty_leftover_when_cwd_is_nested(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    nested = worktree.root / "packages" / "app"
    nested.mkdir(parents=True)
    (nested / "saved.txt").write_text("recover me\n")

    assert ManagedWorktree.prune(limit=0) == 1
    worktree.root.mkdir(parents=True)

    managed = ManagedWorktree.at(nested)
    assert managed is not None
    assert managed.restore(nested) is True

    assert (nested / "saved.txt").read_text() == "recover me\n"
    assert _claim(repo, worktree.name).read_recovery() is None


def test_restore_rejects_an_occupied_leftover_checkout_directory(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    (worktree.root / "saved.txt").write_text("recover me\n")

    assert ManagedWorktree.prune(limit=0) == 1
    worktree.root.mkdir(parents=True)
    occupied = worktree.root / "unrelated.txt"
    occupied.write_text("do not replace\n")

    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    with pytest.raises(WorktreeError, match="occupied worktree path"):
        managed.restore(worktree.root)

    assert occupied.read_text() == "do not replace\n"
    assert _claim(repo, worktree.name).read_recovery() is not None


def test_restore_keeps_an_active_empty_reservation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    (worktree.root / "saved.txt").write_text("recover me\n")

    assert ManagedWorktree.prune(limit=0) == 1
    claim = _claim(repo, worktree.name)
    claim.mark_starting()
    worktree.root.mkdir(parents=True)

    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    with pytest.raises(WorktreeError, match="already in progress"):
        managed.restore(worktree.root)

    assert worktree.root.is_dir()
    assert claim.read_recovery() is not None
    claim.finish_starting()
    worktree.root.rmdir()


def test_concurrent_restore_keeps_the_completed_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    (worktree.root / "saved.txt").write_text("recover me\n")
    assert ManagedWorktree.prune(limit=0) == 1

    reserved = Event()
    release = Event()
    pause_lock = Lock()
    paused = False
    mkdir = Path.mkdir

    def pause_first_reservation(path: Path, *args: Any, **kwargs: Any) -> None:
        nonlocal paused
        mkdir(path, *args, **kwargs)
        if path != worktree.root:
            return
        with pause_lock:
            first = not paused
            paused = True
        if first:
            reserved.set()
            assert release.wait(timeout=5)

    monkeypatch.setattr(Path, "mkdir", pause_first_reservation)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(managed.restore, worktree.root)
        assert reserved.wait(timeout=5)
        second = executor.submit(managed.restore, worktree.root)
        release.set()
        assert first.result(timeout=10) is True
        assert second.result(timeout=10) is False

    record = _claim(repo, worktree.name).read()
    assert record is not None
    assert record.base_commit is not None
    assert (worktree.root / "saved.txt").read_text() == "recover me\n"
    _claim(repo, worktree.name).finish_starting()


def test_prune_excludes_restore_during_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="busy")
    _finish_starts(worktree)
    claim = _claim(repo, worktree.name)
    snapshot = PreparedWorktree.snapshot

    def start_restore_during_snapshot(prepared: PreparedWorktree) -> str:
        with pytest.raises(WorktreeRecordError, match="being removed"):
            claim.mark_starting()
        return snapshot(prepared)

    monkeypatch.setattr(PreparedWorktree, "snapshot", start_restore_during_snapshot)

    assert ManagedWorktree.prune(limit=0) == 1
    assert not worktree.root.exists()
    assert claim.read() is None
    assert claim.read_recovery() is not None


def test_restore_waits_for_in_flight_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    (worktree.root / "saved.txt").write_text("recover me\n")
    snapshot_started = Event()
    finish_snapshot = Event()
    restore_started = Event()
    restore_entered = Event()
    snapshot = PreparedWorktree.snapshot
    restore_locked = ManagedWorktree._restore_locked

    def pause_snapshot(prepared: PreparedWorktree) -> str:
        snapshot_started.set()
        assert finish_snapshot.wait(timeout=5)
        return snapshot(prepared)

    def observe_restore(
        managed: ManagedWorktree,
        requested: Path,
        root: Path,
        *,
        trust_store: TrustedFoldersManager | None = None,
    ) -> bool:
        restore_entered.set()
        return restore_locked(managed, requested, root, trust_store=trust_store)

    monkeypatch.setattr(PreparedWorktree, "snapshot", pause_snapshot)
    monkeypatch.setattr(ManagedWorktree, "_restore_locked", observe_restore)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None

    def run_restore() -> bool:
        restore_started.set()
        return managed.restore(worktree.root)

    with ThreadPoolExecutor(max_workers=2) as executor:
        prune = executor.submit(ManagedWorktree.prune, 0)
        assert snapshot_started.wait(timeout=5)
        restore = executor.submit(run_restore)
        assert restore_started.wait(timeout=5)
        assert not restore_entered.is_set()
        finish_snapshot.set()
        assert prune.result(timeout=10) == 1
        assert restore.result(timeout=10) is True

    assert restore_entered.is_set()
    assert (worktree.root / "saved.txt").read_text() == "recover me\n"
    assert _claim(repo, worktree.name).read_recovery() is None
    _claim(repo, worktree.name).finish_starting()


def test_restore_does_not_drop_a_completed_claim_when_the_path_is_occupied(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    (worktree.root / "saved.txt").write_text("recover me\n")

    assert ManagedWorktree.prune(limit=0) == 1
    claim = _claim(repo, worktree.name)
    recovery = claim.read_recovery()
    assert recovery is not None
    with WorktreeRepository.open(Path(repo.working_dir)) as repository:
        repository.restore(claim, recovery)
        claim.finish_starting()
        claim.write_recovery(recovery)
        with pytest.raises(WorktreeError, match="occupied worktree path"):
            repository.restore(claim, recovery)

    restored = claim.read()
    assert restored is not None
    assert restored.base_commit is not None
    _hold(worktree.root, "session-a")
    assert _holders(worktree.root) == frozenset({"session-a"})
    assert (worktree.root / "saved.txt").read_text() == "recover me\n"


def test_restore_discards_stale_recovery_for_a_completed_checkout(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    (worktree.root / "saved.txt").write_text("recover me\n")

    assert ManagedWorktree.prune(limit=0) == 1
    claim = _claim(repo, worktree.name)
    recovery = claim.read_recovery()
    assert recovery is not None
    with WorktreeRepository.open(Path(repo.working_dir)) as repository:
        repository.restore(claim, recovery)
    claim.write_recovery(recovery)
    claim.finish_starting()

    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    assert managed.restore(worktree.root) is False

    assert (worktree.root / "saved.txt").read_text() == "recover me\n"
    assert claim.read_recovery() is None


def test_restore_does_not_leave_a_directory_when_the_branch_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    (worktree.root / "saved.txt").write_text("recover me\n")
    assert ManagedWorktree.prune(limit=0) == 1

    def fail_branch(_repository: WorktreeRepository, preferred: str) -> str:
        raise WorktreeError(
            f"Unable to find an unused recovery branch for {preferred!r}."
        )

    monkeypatch.setattr(WorktreeRepository, "_available_recovery_branch", fail_branch)

    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    with pytest.raises(WorktreeError, match="unused recovery branch"):
        managed.restore(worktree.root)

    assert not worktree.root.exists()
    assert _claim(repo, worktree.name).read_recovery() is not None


def test_restore_releases_starting_marker_when_recovery_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="restore-me")
    _finish_starts(worktree)
    assert ManagedWorktree.prune(limit=0) == 1
    claim = _claim(repo, worktree.name)

    delete_recovery = WorktreeClaim.delete_recovery

    def fail_delete_recovery(candidate: WorktreeClaim) -> None:
        if candidate == claim:
            raise OSError("disk full")
        delete_recovery(candidate)

    monkeypatch.setattr(WorktreeClaim, "delete_recovery", fail_delete_recovery)
    managed = ManagedWorktree(claim=claim)

    with pytest.raises(OSError, match="disk full"):
        managed.restore(worktree.root)

    assert claim.is_starting() is False


def test_auto_name_does_not_reuse_a_retained_snapshot(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    worktree = _prepare_auto(Path(repo.working_dir), suggested_name="reserved")
    _finish_starts(worktree)
    assert ManagedWorktree.prune(limit=0) == 1

    replacement = _prepare_auto(Path(repo.working_dir), suggested_name=worktree.name)

    assert replacement.name == "reserved-2"


def test_prune_applies_the_limit_across_repositories(tmp_path: Path) -> None:
    first_repo = _init_repo(tmp_path / "first")
    second_repo = _init_repo(tmp_path / "second")
    oldest = _prepare_auto(Path(first_repo.working_dir), suggested_name="oldest")
    newest = _prepare_auto(Path(second_repo.working_dir), suggested_name="newest")
    _finish_starts(oldest, newest)
    _age_claim(_claim(first_repo, oldest.name), 30)

    removed = ManagedWorktree.prune(limit=1)

    assert removed == 1
    assert not oldest.root.exists()
    assert newest.root.is_dir()


def test_release_refuses_a_new_holder_while_inspecting_for_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _hold(worktree.root, "session-a")
    inspect = PreparedWorktree.inspect_for_cleanup

    def join_during_inspection(prepared: Any) -> Any:
        with pytest.raises(WorktreeError, match="being removed"):
            _hold(worktree.root, "session-b")
        return inspect(prepared)

    monkeypatch.setattr(PreparedWorktree, "inspect_for_cleanup", join_during_inspection)

    release = _release(worktree.root, "session-a")

    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert not worktree.root.exists()
    assert worktree.branch not in (head.name for head in repo.heads)


def _join(cwd: Path, session_dir: Path) -> None:
    """A session that worked in the worktree and has since closed."""
    managed = ManagedWorktree.at(cwd)
    assert managed is not None
    managed.hold(session_dir.name, session_dir=session_dir)
    managed.release_holder(session_dir.name)


def _trash_entries() -> list[Path]:
    trash = WorktreeClaim.trash_root()
    return list(trash.iterdir()) if trash.is_dir() else []


def test_a_worktree_is_reaped_once_every_session_that_worked_in_it_is_finished(
    tmp_path: Path,
) -> None:
    """*Prepare*: Two sessions worked in one worktree; only one is archived.
    *Do*: Reap it, then archive the second session and reap again.
    *Assert*: It is kept until both are finished, then its state is saved and
    the worktree, its branch and its trash are gone.
    """
    # Prepare
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="shared")
    first, second = tmp_path / "session-a", tmp_path / "session-b"
    _join(worktree.root, first)
    _join(worktree.root, second)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    finished = {first}

    # Do
    kept = managed.reap_if_finished(finished.__contains__)

    # Assert
    assert kept is None
    assert worktree.root.is_dir()

    # Do
    finished.add(second)
    release = managed.reap_if_finished(finished.__contains__)

    # Assert
    assert release is not None
    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert release.snapshot_ref == f"{SNAPSHOT_REF_PREFIX}/{worktree.name}"
    saved = repo.commit(release.snapshot_ref)
    assert saved.tree["file.txt"].data_stream.read() == b"hello\n"
    assert not worktree.root.exists()
    assert worktree.branch not in (head.name for head in repo.heads)
    assert str(worktree.root) not in repo.git.worktree("list")
    assert _trash_entries() == []
    assert managed.claim.read() is None


def test_a_worktree_no_session_worked_in_is_not_reaped(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="never-joined")
    _finish_starts(worktree)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None

    assert managed.reap_if_finished(lambda _dir: True) is None
    assert worktree.root.is_dir()


def _request_removal_like_an_older_version(managed: ManagedWorktree) -> None:
    record = managed.claim.read()
    assert record is not None
    managed.claim.write(record.model_copy(update={"reap_requested": True}))


def test_a_removal_an_older_version_requested_is_honoured(tmp_path: Path) -> None:
    """*Prepare*: A worktree an older Vibe asked to remove before upgrading,
    which records no sessions.
    *Do*: Reap it.
    *Assert*: It is removed, as the older version would have.
    """
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="older-request")
    _finish_starts(worktree)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    _request_removal_like_an_older_version(managed)

    release = managed.reap_if_finished(lambda _dir: False)

    assert release is not None
    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert ManagedWorktree.joined() == ()


def test_a_session_resuming_withdraws_an_older_versions_request(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="resumed")
    _finish_starts(worktree)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    _request_removal_like_an_older_version(managed)

    _join(worktree.root, tmp_path / "session-a")

    assert managed.reap_if_finished(lambda _dir: False) is None
    assert worktree.root.is_dir()


def test_a_session_joins_a_worktree_once(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="joined")
    session_dir = tmp_path / "session-a"

    _join(worktree.root, session_dir)
    _join(worktree.root, session_dir)

    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    record = managed.claim.read()
    assert record is not None
    assert record.session_dirs == {session_dir}
    assert ManagedWorktree.joined() == (managed,)


def test_a_failed_join_leaves_the_worktree_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start left marked, or a hold left taken, would keep the worktree
    from ever being pruned.
    """
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="join-fails")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    assert managed.claim.is_starting()

    def fail(_self: ManagedWorktree, _session_dir: Path) -> None:
        raise OSError("record unwritable")

    monkeypatch.setattr(ManagedWorktree, "_join", fail)

    with pytest.raises(OSError, match="record unwritable"):
        managed.hold("session-a", session_dir=tmp_path / "session-a")

    assert not managed.claim.is_starting()
    assert managed.holders() == frozenset()


def test_reap_keeps_a_held_worktree(tmp_path: Path) -> None:
    """*Prepare*: An archived session still running in its worktree.
    *Do*: Reap it, then release the session and reap again.
    *Assert*: It is kept while held and removed once released.
    """
    # Prepare
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="running-session")
    session_dir = tmp_path / "session-a"
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("session-a", session_dir=session_dir)

    # Do
    release = managed.reap_if_finished(lambda _dir: True)

    # Assert
    assert release is not None
    assert release.outcome is WorktreeReleaseOutcome.KEPT_IN_USE
    assert worktree.root.is_dir()

    # Do
    managed.release_holder("session-a")
    completed = managed.reap_if_finished(lambda _dir: True)

    # Assert
    assert completed is not None
    assert completed.outcome is WorktreeReleaseOutcome.REMOVED
    assert not worktree.root.exists()


def test_a_session_unarchived_during_the_snapshot_keeps_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An archived session's worktree whose reap is snapshotting.
    *Do*: The session is unarchived during the snapshot.
    *Assert*: The check before the move sees it and keeps the worktree whole.
    """
    # Prepare
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="unarchived")
    session_dir = tmp_path / "session-a"
    _join(worktree.root, session_dir)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    archived = {session_dir}
    snapshot = PreparedWorktree.snapshot

    def unarchive_during_snapshot(prepared: PreparedWorktree) -> str:
        archived.clear()
        return snapshot(prepared)

    monkeypatch.setattr(PreparedWorktree, "snapshot", unarchive_during_snapshot)

    # Do
    release = managed.reap_if_finished(archived.__contains__)

    # Assert
    assert release is not None
    assert release.outcome is WorktreeReleaseOutcome.KEPT_CANCELLED
    assert worktree.root.is_dir()
    assert managed.claim.read() is not None
    assert managed.claim.read_recovery() is None


def test_a_worktree_that_cannot_be_moved_is_kept_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An archived session's worktree that another program has a
    file open in, so it cannot be moved, as on Windows.
    *Do*: Reap it.
    *Assert*: The reap fails with nothing touched: the checkout, its branch,
    its claim are as they were, and no recovery points at a removal.
    """
    # Prepare
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="locked")
    _join(worktree.root, tmp_path / "session-a")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None

    def refuse(self: Path, target: Path) -> Path:
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "rename", refuse)

    # Do
    with pytest.raises(PermissionError):
        managed.reap_if_finished(lambda _dir: True)

    # Assert
    assert (worktree.root / "file.txt").read_text() == "hello\n"
    assert worktree.branch in (head.name for head in repo.heads)
    assert str(worktree.root) in repo.git.worktree("list")
    assert managed.claim.read() is not None
    assert managed.claim.read_recovery() is None
    assert _trash_entries() == []


def test_a_removal_interrupted_after_the_move_keeps_its_first_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A reap that dies right after moving the worktree to the trash.
    *Do*: Reap again, as the next process would.
    *Assert*: Nothing is snapshotted twice: the saved work is the first
    snapshot's, the claim is cleared and the trash is emptied.
    """
    # Prepare
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="interrupted")
    (worktree.root / "work.txt").write_text("unsaved\n")
    _join(worktree.root, tmp_path / "session-a")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    forget = worktree_module._forget_checkout

    def die_after_the_move(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("process killed")

    monkeypatch.setattr(worktree_module, "_forget_checkout", die_after_the_move)
    with pytest.raises(RuntimeError):
        managed.reap_if_finished(lambda _dir: True)
    saved = repo.commit(f"{SNAPSHOT_REF_PREFIX}/{worktree.name}").hexsha
    monkeypatch.setattr(worktree_module, "_forget_checkout", forget)
    snapshots: list[str] = []
    snapshot = PreparedWorktree.snapshot

    def counted_snapshot(prepared: PreparedWorktree) -> str:
        snapshots.append(prepared.name)
        return snapshot(prepared)

    monkeypatch.setattr(PreparedWorktree, "snapshot", counted_snapshot)

    # Do
    release = managed.reap_if_finished(lambda _dir: True)

    # Assert
    assert release is not None
    assert release.outcome is WorktreeReleaseOutcome.NOT_FOUND
    assert snapshots == []
    assert repo.commit(f"{SNAPSHOT_REF_PREFIX}/{worktree.name}").hexsha == saved
    assert managed.claim.read() is None
    ManagedWorktree.empty_trash()
    assert _trash_entries() == []


def test_a_retry_finishes_forgetting_a_checkout_git_could_not_forget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A reap whose deletion of Git's record of the worktree fails
    once, after the checkout was moved to the trash.
    *Do*: Reap again.
    *Assert*: Git no longer lists the worktree, its branch is deleted and the
    claim is cleared.
    """
    # Prepare
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="admin-locked")
    _join(worktree.root, tmp_path / "session-a")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    rmtree = shutil.rmtree

    def fail_once(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path).parent.name != "worktrees":
            return rmtree(path, *args, **kwargs)
        monkeypatch.setattr(shutil, "rmtree", rmtree)
        raise PermissionError("index in use")

    monkeypatch.setattr(shutil, "rmtree", fail_once)
    with pytest.raises(PermissionError):
        managed.reap_if_finished(lambda _dir: True)
    assert not worktree.root.exists()

    # Do
    release = managed.reap_if_finished(lambda _dir: True)

    # Assert
    assert release is not None
    assert release.outcome is WorktreeReleaseOutcome.NOT_FOUND
    assert "prunable" not in repo.git.worktree("list", "--porcelain")
    assert worktree.branch not in (head.name for head in repo.heads)
    assert managed.claim.read() is None


def test_trash_left_by_an_interrupted_delete_is_emptied_by_the_next_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A reap whose deletion of the moved worktree fails.
    *Do*: Run retention cleanup.
    *Assert*: The worktree was gone at once, and the leftover is deleted.
    """
    # Prepare
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="slow-delete")
    _join(worktree.root, tmp_path / "session-a")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    rmtree = shutil.rmtree

    def fail_once(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path).parent != WorktreeClaim.trash_root():
            return rmtree(path, *args, **kwargs)
        monkeypatch.setattr(shutil, "rmtree", rmtree)
        raise OSError("file in use")

    monkeypatch.setattr(shutil, "rmtree", fail_once)
    release = managed.reap_if_finished(lambda _dir: True)
    assert release is not None
    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert not worktree.root.exists()
    assert len(_trash_entries()) == 1

    # Do
    ManagedWorktree.prune(limit=10)

    # Assert
    assert _trash_entries() == []


def test_reap_snapshots_a_clean_worktree_before_removal(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="archived-session")
    _join(worktree.root, tmp_path / "session-a")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None

    release = managed.reap_if_finished(lambda _dir: True)

    assert release is not None
    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert release.snapshot_ref is not None
    assert repo.commit(release.snapshot_ref)


@contextmanager
def held_snapshot(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Event, list[str]]]:
    """Stop every snapshot until the test lets the first one through.

    Yields the event set once a snapshot has started, and the names of the
    worktrees snapshotted so far.
    """
    snapshot = PreparedWorktree.snapshot
    started = Event()
    release = Event()
    snapshotted: list[str] = []

    def stopped_snapshot(prepared: PreparedWorktree) -> str:
        snapshotted.append(prepared.name)
        started.set()
        release.wait(timeout=30)
        return snapshot(prepared)

    monkeypatch.setattr(PreparedWorktree, "snapshot", stopped_snapshot)
    try:
        yield started, snapshotted
    finally:
        release.set()


def test_a_worktree_being_removed_is_in_use_for_a_second_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An archived session's worktree whose reap is stopped mid-snapshot.
    *Do*: Reap it again, then force-remove it, while the first reap runs.
    *Assert*: Both report the worktree in use without touching it, and the
    first reap removes it and keeps its recovery snapshot.
    """
    # Prepare
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="archived-session")
    _join(worktree.root, tmp_path / "session-a")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None

    def finished(_dir: Path) -> bool:
        return True

    with ThreadPoolExecutor(max_workers=1) as pool:
        with held_snapshot(monkeypatch) as (snapshot_started, snapshotted):
            first = pool.submit(managed.reap_if_finished, finished)
            assert snapshot_started.wait(timeout=10)

            # Do
            second_reap = managed.reap_if_finished(finished)
            forced = managed.force_release()

            # Assert
            assert second_reap is not None
            assert second_reap.outcome is WorktreeReleaseOutcome.KEPT_IN_USE
            assert forced.outcome is WorktreeReleaseOutcome.KEPT_IN_USE
            assert snapshotted == [worktree.name]
            assert worktree.root.is_dir()

        first_release = first.result(timeout=30)
    assert first_release is not None
    assert first_release.outcome is WorktreeReleaseOutcome.REMOVED
    assert not worktree.root.exists()
    recovery = managed.claim.read_recovery()
    assert recovery is not None
    assert recovery.snapshot_ref == first_release.snapshot_ref


def test_attaching_during_removal_cannot_join_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="attaching")
    _join(worktree.root, tmp_path / "session-a")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None

    with ThreadPoolExecutor(max_workers=1) as pool:
        with held_snapshot(monkeypatch) as (snapshot_started, _):
            removal = pool.submit(managed.reap_if_finished, lambda _dir: True)
            assert snapshot_started.wait(timeout=10)
            with pytest.raises(WorktreeError, match="being removed"):
                managed.hold_for_attachment()
            with pytest.raises(WorktreeError, match="being removed"):
                managed.hold("late-session")
            assert managed.holders() == frozenset()
            assert worktree.root.is_dir()
        result = removal.result(timeout=30)
        assert result is not None
        assert result.outcome is WorktreeReleaseOutcome.REMOVED
    assert not worktree.root.exists()
    assert managed.holders() == frozenset()


def test_a_removal_left_by_a_dead_process_does_not_block_the_next_one(
    tmp_path: Path,
) -> None:
    """*Prepare*: An archived session's worktree with a removal marker that no
    live process holds, as a crash during a removal leaves it.
    *Do*: Reap the worktree.
    *Assert*: The stale marker is discarded and the worktree is removed.
    """
    # Prepare
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="crashed-removal")
    _join(worktree.root, tmp_path / "session-a")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    stale_marker = managed.claim.directory / HOLDERS_DIR_NAME / REMOVING_HOLDER
    stale_marker.parent.mkdir(parents=True, exist_ok=True)
    stale_marker.touch()

    # Do
    release = managed.reap_if_finished(lambda _dir: True)

    # Assert
    assert release is not None
    assert release.outcome is WorktreeReleaseOutcome.REMOVED
    assert not worktree.root.exists()


def test_prune_applies_the_limit_to_a_worktree_whose_sessions_are_not_finished(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="active")
    _join(worktree.root, tmp_path / "session-a")

    removed = ManagedWorktree.prune(0, session_finished=lambda _dir: False)

    assert removed == 1
    assert not worktree.root.exists()


def test_prune_leaves_an_archived_worktree_to_the_cleanup_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prune holds the lock every session start needs, so it must not spend
    an archived worktree's snapshot under it.
    """
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, suggested_name="archived")
    _join(worktree.root, tmp_path / "session-a")
    snapshots: list[str] = []
    monkeypatch.setattr(
        PreparedWorktree, "snapshot", lambda prepared: snapshots.append(prepared.name)
    )

    removed = ManagedWorktree.prune(limit=0, session_finished=lambda _dir: True)

    assert removed == 0
    assert snapshots == []
    assert worktree.root.is_dir()


def test_the_limit_does_not_count_a_worktree_about_to_be_reaped(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    archived = _prepare_auto(tmp_path, suggested_name="archived")
    _join(archived.root, tmp_path / "session-a")
    active = _prepare_auto(tmp_path, suggested_name="active")
    _join(active.root, tmp_path / "session-b")
    finished = {tmp_path / "session-a"}

    removed = ManagedWorktree.prune(limit=1, session_finished=finished.__contains__)

    assert removed == 0
    assert active.root.is_dir()


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
    worktree_repo = _track_repo(Repo(worktree.root))
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
    _track_repo(Repo.init(worktree.path))

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


def _record_checkout_config(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    extra: list[str] = []
    sanitized = git_repo_module.sanitized_git

    def record(
        repo: Repo, *extra_config: str, cwd: Path | None = None, **kwargs: Any
    ) -> Git:
        extra.extend(extra_config)
        return sanitized(repo, *extra_config, cwd=cwd, **kwargs)

    monkeypatch.setattr(git_repo_module, "sanitized_git", record)
    return extra


def test_worktree_creation_checks_out_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    monkeypatch.setattr(git_repo_module, "_CHECKOUT_WORKERS", 3)
    extra = _record_checkout_config(monkeypatch)

    worktree = _prepare("feature", tmp_path)

    assert extra == ["checkout.workers=3"]
    assert (worktree.root / "file.txt").read_text() == "hello\n"


def test_worktree_creation_reports_checkout_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Git holds progress back for its first two seconds, which a checkout this
    # small never reaches.
    monkeypatch.setenv("GIT_PROGRESS_DELAY", "0")
    repo = _init_repo(tmp_path)
    for index in range(150):
        (tmp_path / f"extra-{index}.txt").write_text(f"{index}\n")
    repo.index.add([f"extra-{index}.txt" for index in range(150)])
    repo.index.commit("more files")
    reported: list[tuple[int, int]] = []
    target = tmp_path.parent / f"{tmp_path.name}-progress"

    with git_repo_module.GitRepo.open(tmp_path) as git:
        git.add_worktree(
            target,
            "feat/progress",
            branch_created=True,
            on_checkout_progress=lambda done, total: reported.append((done, total)),
        )

    assert reported
    assert reported[-1] == (151, 151)
    assert all(total == 151 for _, total in reported)
    assert _track_repo(Repo(target)).active_branch.name == "feat/progress"
    assert _track_repo(Repo(target)).is_dirty(untracked_files=True) is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            GitError("disk full"),
            "Failed to create worktree 'feature' for branch 'feature'",
        ),
        (RuntimeError("progress report failed"), "progress report failed"),
    ],
)
def test_worktree_creation_unregisters_a_worktree_whose_checkout_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: str
) -> None:
    repo = _init_repo(tmp_path)

    def fail_checkout(*_args: Any, **_kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(git_repo_module.GitRepo, "_check_out", fail_checkout)

    with pytest.raises(type(error), match=expected):
        _prepare("feature", tmp_path)

    assert _linked(tmp_path) == ()
    assert "feature" not in (head.name for head in repo.heads)


def test_worktree_creation_keeps_the_users_checkout_worker_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    repo.config_writer().set_value("checkout", "workers", "2").release()
    extra = _record_checkout_config(monkeypatch)

    _prepare("feature", tmp_path)

    assert extra == []


def test_worktree_creation_logs_its_phase_timings(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _init_repo(tmp_path)

    with caplog.at_level("INFO", logger="vibe"):
        _prepare("feature", tmp_path)

    [message] = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Worktree created")
    ]
    assert message.startswith("Worktree created name=feature total_ms=")
    assert "base_ref_ms=" in message
    assert "checkout_ms=" in message


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
    # worktree would exist with no claim naming it and automatic cleanup would
    # have to treat it as unmanaged.
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
    assert (
        _track_repo(Repo(worktree.root)).active_branch.name == "vibe/fix-the-login-bug"
    )
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


def test_fork_worktree_starts_from_source_head_in_an_independent_checkout(
    tmp_path: Path,
) -> None:
    """*Prepare*: A managed worktree with a commit absent from the primary checkout.
    *Do*: Fork that worktree through the repository lifecycle.
    *Assert*: The fork has that commit on a distinct branch and filesystem path.
    """
    # Prepare
    _init_repo(tmp_path)
    source = _prepare("source", tmp_path, branch="vibe/source")
    source_project = source.path / "project"
    source_project.mkdir()
    source_file = source_project / "source-only.txt"
    source_file.write_text("committed in source\n")
    source_repo = _track_repo(Repo(source.root))
    source_repo.index.add([str(source_file.relative_to(source.root))])
    source_repo.index.commit("source change")

    # Do
    forked = _prepare_fork(source_project, suggested_name="source-fork")

    # Assert
    assert forked.path == forked.root / "project"
    assert forked.path != source_project
    assert forked.branch == "vibe/source-fork"
    assert (forked.path / source_file.name).read_text() == "committed in source\n"
    assert forked.source_had_uncommitted_changes is False
    (forked.path / "fork-only.txt").write_text("fork\n")
    assert not (source_project / "fork-only.txt").exists()


def test_fork_worktree_reports_changes_left_in_the_source(tmp_path: Path) -> None:
    """*Prepare*: A managed source with an untracked file after its latest commit.
    *Do*: Fork the source from its committed HEAD.
    *Assert*: The fork records that source-only work was not copied.
    """
    # Prepare
    _init_repo(tmp_path)
    source = _prepare("source", tmp_path, branch="vibe/source")
    (source.path / "untracked.txt").write_text("source only\n")

    # Do
    forked = _prepare_fork(source.path, suggested_name="source-fork")

    # Assert
    assert forked.source_had_uncommitted_changes is True
    assert not (forked.path / "untracked.txt").exists()


def test_fork_dirty_check_ignores_the_primary_checkout(tmp_path: Path) -> None:
    """*Prepare*: A clean source worktree and dirty primary checkout.
    *Do*: Fork the source from its committed HEAD.
    *Assert*: Only source-local changes control the omitted-work warning.
    """
    # Prepare
    _init_repo(tmp_path)
    source = _prepare("source", tmp_path, branch="vibe/source")
    (tmp_path / "primary-untracked.txt").write_text("primary only\n")

    # Do
    forked = _prepare_fork(source.path, suggested_name="source-fork")

    # Assert
    assert forked.source_had_uncommitted_changes is False
    assert not (forked.path / "primary-untracked.txt").exists()


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
    _track_repo(Repo.init(upstream, bare=True, initial_branch="main"))

    seed = _init_repo(tmp_path / "seed")
    seed.create_remote("origin", str(upstream))
    seed.git.push("origin", "main")

    clone = _track_repo(Repo.clone_from(str(upstream), str(tmp_path / "clone")))

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
        # What the next command will run with, and what __call__ last
        # applied; they come apart because fetch() consumes the overrides.
        self.config_kwargs: dict[str, Any] = {}
        self.applied_config: dict[str, Any] = {}
        self.GIT_PYTHON_GIT_EXECUTABLE = Git.GIT_PYTHON_GIT_EXECUTABLE

    def __call__(self, **config: Any) -> _RecordingGit:
        # GitPython's Git is callable: git(c=...) returns a git with that
        # config applied. Stand in for the returned handle.
        self.config_kwargs = config
        self.applied_config = config
        return self

    def fetch(self, *_args: Any, **kwargs: Any) -> Any:
        # GitPython consumes the -c overrides after one command; model that,
        # so a stored handle cannot keep reporting itself sanitized.
        self.config_kwargs = {}
        self.fetch_kwargs = kwargs
        return type("_AutoInterrupt", (), {"proc": self.process})()


def _fetch_with(git: _RecordingGit, git_dir: Path) -> None:
    # The secure fetch preflight inspects repository-scoped HTTP configuration
    # with the real Git executable before handing execution to this test double.
    Repo.init(git_dir, bare=True).close()
    config = type(
        "_Config",
        (),
        {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *_args: None,
            "get": lambda self, _key: "https://example.invalid/repo.git",
            "sections": lambda self: [],
        },
    )()
    remote = type("_Remote", (), {"config_reader": config})()
    repo = cast(
        Any,
        type(
            "_Repo",
            (),
            {
                "common_dir": git_dir,
                "git_dir": git_dir,
                "git": git,
                "remote": lambda self, _name: remote,
                "config_reader": lambda self, _level: config,
            },
        )(),
    )
    git_repo_module.GitRepo(repo).fetch_branch("origin", "main")


def test_fetch_kills_a_remote_that_never_answers(tmp_path: Path) -> None:
    git = _RecordingGit(hangs=True)

    with pytest.raises(GitError, match="Timed out"):
        _fetch_with(git, tmp_path)

    assert git.process.killed


def test_fetch_refuses_to_stop_and_ask_for_credentials(tmp_path: Path) -> None:
    # A credential helper would block the fetch behind a prompt the user may
    # never see, and this refresh is optional enough to fail instead.
    git = _RecordingGit()

    _fetch_with(git, tmp_path)

    assert git.fetch_kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    env = git.fetch_kwargs["env"]
    config = {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert config["core.fsmonitor"] == ""
    assert (
        config["core.hooksPath"] == git_repo_module._NO_HOOKS_CONFIG.partition("=")[2]
    )
    assert git.config_kwargs == {}


def test_fetch_does_not_use_the_timeout_windows_rejects(tmp_path: Path) -> None:
    git = _RecordingGit()

    _fetch_with(git, tmp_path)

    assert "kill_after_timeout" not in git.fetch_kwargs


def test_inspecting_a_worktree_does_not_execute_a_malicious_fsmonitor_hook(
    tmp_path: Path,
) -> None:
    """The same RCE `vibe.core.system_prompt` already guards against, here.

    `core.fsmonitor` is a command git runs to ask what changed, and a
    repository's own config can name any command. Reading a worktree is
    reading somebody else's repository, so without `-c core.fsmonitor=` the
    hook runs with the user's privileges.

    It matters more for automatic retention than for explicit deletion: the
    read is no longer something the user asked for.
    """
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, prompt="inspect me")
    payload = tmp_path / "PWNED"
    # On the repository, not the worktree: a linked worktree shares its
    # config, which is exactly why one repository can poison every checkout.
    with repo.config_writer() as config:
        config.set_value("core", "fsmonitor", f"touch {payload}")

    worktree.inspect_for_cleanup()

    assert not payload.exists(), "the worktree's fsmonitor hook was executed"
    assert repo.working_dir is not None


def _session_filtered_checkout(
    tmp_path: Path,
) -> tuple[Repo, PreparedWorktree, TrustedFoldersManager]:
    from vibe.core.trusted_folders import trusted_folders_manager

    root = tmp_path / "source"
    repo = _init_repo(root)
    repo.git.config("filter.round3.clean", "sed 's/^S://'")
    repo.git.config("filter.round3.smudge", "sed 's/^/S:/'")
    (root / ".gitattributes").write_text("*.txt filter=round3\n")
    repo.index.add([".gitattributes"])
    repo.index.commit("filtered files")
    store = trusted_folders_manager.for_session()
    store.trust_for_session(root)
    assert trusted_folders_manager.is_trusted(root) is not True
    with WorktreeRepository.open(root, trust_store=store) as repository:
        prepared = repository.prepare_auto(suggested_name="filtered-release")
    _finish_starts(prepared)
    assert (prepared.root / "file.txt").read_text() == "S:hello\n"
    return repo, prepared, store


@pytest.mark.parametrize("operation", ["release", "prune", "reap"])
def test_public_cleanup_preserves_session_filtered_content(
    tmp_path: Path, operation: str
) -> None:
    from vibe.core.git.worktree.repository import RefreshedBase

    repo, prepared, store = _session_filtered_checkout(tmp_path)
    managed = ManagedWorktree.at(prepared.root)
    assert managed is not None
    (prepared.root / "file.txt").write_text("S:changed\n")
    if operation == "release":
        result = managed.release(trust_store=store)
        assert result.outcome is WorktreeReleaseOutcome.REMOVED
        ref = result.snapshot_ref
    elif operation == "prune":
        assert ManagedWorktree.prune(0, trust_store=store) == 1
        recovery = managed.claim.read_recovery()
        assert recovery is not None
        ref = recovery.snapshot_ref
    else:
        managed.hold("finished", session_dir=tmp_path / "finished-session")
        managed.release_holder("finished")
        result = managed.reap_if_finished(lambda _: True, trust_store=store)
        assert result is not None and result.outcome is WorktreeReleaseOutcome.REMOVED
        ref = result.snapshot_ref
    assert ref is not None
    assert repo.git.show(f"{ref}:file.txt") == "changed"
    if operation == "release":
        with WorktreeRepository.open(
            Path(repo.working_dir), trust_store=store
        ) as repository:
            restored = repository.prepare_auto(
                suggested_name="recovered-release", base=RefreshedBase(ref)
            )
        assert (restored.root / "file.txt").read_text() == "S:changed\n"
    else:
        assert managed.restore(prepared.root, trust_store=store)
        assert (prepared.root / "file.txt").read_text() == "S:changed\n"


@pytest.mark.parametrize("operation", ["prune", "reap"])
def test_background_cleanup_keeps_content_when_filters_would_be_disabled(
    tmp_path: Path, operation: str
) -> None:
    _, prepared, store = _session_filtered_checkout(tmp_path)
    managed = ManagedWorktree.at(prepared.root)
    assert managed is not None
    (prepared.root / "file.txt").write_text("S:changed\n")
    persisted = store.for_session()
    assert persisted.is_trusted(prepared.repo_root) is not True
    if operation == "prune":
        assert ManagedWorktree.prune(0, trust_store=persisted) == 0
    else:
        managed.hold("finished", session_dir=tmp_path / "finished-session")
        managed.release_holder("finished")
        result = managed.reap_if_finished(lambda _: True, trust_store=persisted)
        assert (
            result is not None and result.outcome is WorktreeReleaseOutcome.KEPT_DIRTY
        )
    assert prepared.root.is_dir()
    assert (prepared.root / "file.txt").read_text() == "S:changed\n"
    assert managed.claim.read_recovery() is None


@pytest.mark.parametrize("operation", ["probe", "force", "missing", "discard"])
def test_public_ref_cleanup_uses_selected_store(tmp_path: Path, operation: str) -> None:
    repo, prepared, store = _session_filtered_checkout(tmp_path)
    managed = ManagedWorktree.at(prepared.root)
    assert managed is not None
    repo.git.config("filter.a=b.clean", "cat")
    if operation == "probe":
        result = managed.probe_release(trust_store=store)
        assert result.outcome is WorktreeReleaseOutcome.REMOVED
        assert prepared.root.exists()
    elif operation == "force":
        result = managed.force_release(trust_store=store)
        assert result.outcome is WorktreeReleaseOutcome.REMOVED
        assert prepared.branch not in (head.name for head in repo.heads)
    elif operation == "missing":
        admin = worktree_module._checkout_admin_dir(prepared.root)
        record = managed.claim.read()
        assert record is not None
        managed.claim.write(record.model_copy(update={"moved_checkout_admin": admin}))
        shutil.rmtree(prepared.root)
        assert (
            managed.release(trust_store=store).outcome
            is WorktreeReleaseOutcome.NOT_FOUND
        )
        assert prepared.branch not in (head.name for head in repo.heads)
    else:
        assert ManagedWorktree.prune(0, trust_store=store) == 1
        recovery = managed.claim.read_recovery()
        assert recovery is not None
        assert (
            managed.release(trust_store=store).outcome is WorktreeReleaseOutcome.REMOVED
        )
        assert not managed.claim.has_recovery()
        assert (
            recovery.snapshot_ref
            not in repo.git.for_each_ref("--format=%(refname)").splitlines()
        )
