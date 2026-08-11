from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

# GitPython resolves the git executable at import time and raises when it is
# missing, so it is only imported at runtime by _git_python(). app-server must
# boot on machines without git; only worktree operations may require it.
if TYPE_CHECKING:
    from git import InvalidGitRepositoryError, Repo
    from git.exc import GitCommandError, NoSuchPathError

from vibe.core.paths import WORKTREES_DIR
from vibe.core.utils.slug import create_slug
from vibe.core.worktree_naming import worktree_name_from_text, worktree_name_with_suffix

_INVALID_WORKTREE_NAME_CHARS = frozenset('<>:"/\\|?*')
_GIT_USAGE_ERROR_STATUS = 129
_WORKTREE_REV_PARSE_PARTS = 2
_AUTO_WORKTREE_BRANCH_PREFIX = "vibe/"
_MAX_AUTO_WORKTREE_ATTEMPTS = 100
_RESERVED_WORKTREE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


class WorktreeError(Exception): ...


class WorktreeNotFoundError(WorktreeError): ...


class GitUnavailableError(WorktreeError): ...


@dataclass(frozen=True)
class _GitPython:
    repo: type[Repo]
    invalid_git_repository_error: type[InvalidGitRepositoryError]
    git_command_error: type[GitCommandError]
    no_such_path_error: type[NoSuchPathError]


def _git_python() -> _GitPython:
    try:
        from git import InvalidGitRepositoryError, Repo
        from git.exc import GitCommandError, NoSuchPathError
    except ImportError as e:
        raise GitUnavailableError(
            "Git worktree operations require git to be installed."
        ) from e
    return _GitPython(
        repo=Repo,
        invalid_git_repository_error=InvalidGitRepositoryError,
        git_command_error=GitCommandError,
        no_such_path_error=NoSuchPathError,
    )


@dataclass(frozen=True)
class PreparedWorktree:
    name: str
    branch: str
    root: Path
    path: Path
    repo_root: Path
    base_commit: str
    created: bool
    branch_created: bool


@dataclass(frozen=True)
class LinkedWorktree:
    name: str
    branch: str
    root: Path
    path: Path
    repo_root: Path


@dataclass(frozen=True)
class WorktreeCleanupState:
    has_uncommitted_changes: bool
    has_untracked_files: bool
    new_commit_count: int

    @property
    def is_clean(self) -> bool:
        return (
            not self.has_uncommitted_changes
            and not self.has_untracked_files
            and self.new_commit_count == 0
        )

    @property
    def reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.has_uncommitted_changes:
            reasons.append("uncommitted changes")
        if self.has_untracked_files:
            reasons.append("untracked files")
        if self.new_commit_count:
            noun = "commit" if self.new_commit_count == 1 else "commits"
            reasons.append(f"{self.new_commit_count} {noun} added during this session")
        return tuple(reasons)


def prepare_worktree(name: str, base: Path, *, branch: str | None = None) -> Path:
    return prepare_worktree_session(name, base, branch=branch).path


def prepare_worktree_session(
    name: str, base: Path, *, branch: str | None = None
) -> PreparedWorktree:
    _validate_worktree_name(name)
    repo = _open_repo(base)
    branch = name if branch is None else branch
    _validate_branch(repo, branch)

    common_git_dir = _common_git_dir(repo)
    repo_root = _primary_worktree_root(repo, common_git_dir)
    relative_base = _relative_base(repo, base)
    target = _worktree_root(repo_root, common_git_dir) / name

    if target.is_dir():
        _validate_existing_worktree(target, branch, common_git_dir)
        return _build_prepared(
            name,
            branch,
            target,
            relative_base,
            repo_root,
            created=False,
            branch_created=False,
        )

    branch_created = not _branch_exists(repo, branch)
    _create_worktree(repo, target, branch, branch_created=branch_created)
    try:
        return _build_prepared(
            name,
            branch,
            target,
            relative_base,
            repo_root,
            created=True,
            branch_created=branch_created,
        )
    except Exception as e:
        if note := _cleanup_failed_prepare(repo, target, branch, branch_created):
            e.add_note(note)
        raise


def prepare_auto_worktree_session(
    base: Path, *, prompt: str | None = None, suggested_name: str | None = None
) -> PreparedWorktree:
    repo = _open_repo(base)
    common_git_dir = _common_git_dir(repo)
    repo_root = _primary_worktree_root(repo, common_git_dir)
    relative_base = _relative_base(repo, base)
    worktree_root = _worktree_root(repo_root, common_git_dir)

    name, branch, target = _claim_auto_worktree(
        repo, worktree_root, _auto_worktree_name(prompt, suggested_name)
    )
    try:
        _create_worktree(repo, target, branch, branch_created=True)
    except Exception:
        _discard_worktree_claim(repo, target, branch)
        raise

    try:
        return _build_prepared(
            name,
            branch,
            target,
            relative_base,
            repo_root,
            created=True,
            branch_created=True,
        )
    except Exception as e:
        if note := _cleanup_failed_prepare(repo, target, branch, branch_created=True):
            e.add_note(note)
        raise


def _auto_worktree_name(prompt: str | None, suggested: str | None = None) -> str:
    # The model's answer and the raw prompt go through the same slugifier, so a
    # name that reaches git is portable however it was produced. Either can
    # reduce to "" or to a reserved device name such as "nul", neither of which
    # survives _validate_worktree_name, hence the walk down to a random slug.
    for text in (suggested, prompt):
        if text is None:
            continue
        name = worktree_name_from_text(text)
        if _is_portable_worktree_name(name):
            return name
    return create_slug()


def _auto_worktree_candidates(base_name: str) -> Iterator[str]:
    yield base_name
    for suffix in range(2, _MAX_AUTO_WORKTREE_ATTEMPTS + 1):
        yield worktree_name_with_suffix(base_name, suffix)


def _claim_auto_worktree(
    repo: Repo, worktree_root: Path, base_name: str
) -> tuple[str, str, Path]:
    # mkdir is the atomic claim: git worktree add cannot report *why* it failed
    # (a taken path and an invalid ref both exit 128), so a lost race has to be
    # detected before git runs or it is indistinguishable from a real failure.
    for name in _auto_worktree_candidates(base_name):
        branch = f"{_AUTO_WORKTREE_BRANCH_PREFIX}{name}"
        if _branch_exists(repo, branch):
            continue
        target = worktree_root / name
        try:
            target.mkdir(parents=True)
        except FileExistsError:
            continue
        except OSError as e:
            raise WorktreeError(
                f"Failed to claim worktree directory {target}: {e}"
            ) from e
        return name, branch, target

    raise WorktreeError(
        f"Unable to find an unused worktree name for {base_name!r} after "
        f"{_MAX_AUTO_WORKTREE_ATTEMPTS} attempts."
    )


def _discard_worktree_claim(repo: Repo, target: Path, branch: str) -> None:
    git = _git_python()
    # rmdir refuses a populated directory, so a partial checkout is never lost,
    # and git's own remove_junk may have removed it already.
    with suppress(OSError):
        target.rmdir()
    # `worktree add -b` creates the branch before it validates the path, so a
    # failed add leaves it behind. _claim_auto_worktree would then skip this
    # name for every later call, walking the suffix chain until it reports
    # exhaustion instead of the real failure.
    #
    # -d rather than -D: the branch this call created still points at HEAD, so
    # a safe delete removes it, while a racing branch carrying commits is
    # refused. That is a partial guard, not a full one -- a branch someone else
    # created at HEAD between the _branch_exists check and the add is merged
    # too, so it is deleted (recoverable from the reflog). Proving ownership
    # would mean leaving the branch and burning the name on every failure,
    # which costs more than the narrow race it closes.
    with suppress(git.git_command_error):
        repo.git.branch("-d", branch)


def list_linked_worktrees(base: Path) -> tuple[LinkedWorktree, ...]:
    repo = _open_repo(base)
    common_git_dir = _common_git_dir(repo)
    records = _worktree_records(repo)
    repo_root = _primary_worktree_root(repo, common_git_dir)
    relative_base = _relative_base(repo, base)
    linked: list[LinkedWorktree] = []

    for record in records[1:]:
        if record.branch is None or record.prunable:
            continue
        try:
            _validate_existing_worktree(record.root, record.branch, common_git_dir)
            root = record.root.resolve()
            path = _target_cwd(root, relative_base)
        except WorktreeError:
            continue
        linked.append(
            LinkedWorktree(
                name=root.name,
                branch=record.branch,
                root=root,
                path=path,
                repo_root=repo_root,
            )
        )

    return tuple(sorted(linked, key=lambda worktree: str(worktree.path)))


def _open_repo(base: Path) -> Repo:
    git = _git_python()
    try:
        return git.repo(base, search_parent_directories=True)
    except (git.invalid_git_repository_error, git.no_such_path_error) as e:
        raise WorktreeNotFoundError("Path is not inside a git repository.") from e


def _create_worktree(
    repo: Repo, target: Path, branch: str, *, branch_created: bool
) -> None:
    git = _git_python()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if branch_created:
            repo.git.worktree("add", "-b", branch, str(target))
        else:
            repo.git.worktree("add", str(target), branch)
    except git.git_command_error as e:
        raise WorktreeError(
            f"Failed to create worktree {target.name!r} for branch {branch!r}: {e}"
        ) from e


def _cleanup_failed_prepare(
    repo: Repo, target: Path, branch: str, branch_created: bool
) -> str | None:
    git = _git_python()
    try:
        repo.git.worktree("remove", "--force", str(target))
        if branch_created:
            repo.git.branch("-D", branch)
    except git.git_command_error as e:
        return f"Failed to clean up worktree {target.name!r} after prepare failure: {e}"
    return None


def _build_prepared(
    name: str,
    branch: str,
    target: Path,
    relative_base: Path,
    repo_root: Path,
    *,
    created: bool,
    branch_created: bool,
) -> PreparedWorktree:
    # base_commit is the worktree's own HEAD at session start, so cleanup counts
    # only commits added during this session (not commits an attached or reused
    # branch already carried, which the invoking checkout's HEAD would miss).
    return PreparedWorktree(
        name=name,
        branch=branch,
        root=target,
        path=_target_cwd(target, relative_base),
        repo_root=repo_root,
        base_commit=_base_commit(target),
        created=created,
        branch_created=branch_created,
    )


def _base_commit(target: Path) -> str:
    git = _git_python()
    try:
        return git.repo(target).head.commit.hexsha
    except (
        git.invalid_git_repository_error,
        git.no_such_path_error,
        git.git_command_error,
        ValueError,
    ) as e:
        raise WorktreeError(
            f"Failed to inspect worktree HEAD for {target.name!r}: {e}"
        ) from e


def inspect_worktree_for_cleanup(worktree: PreparedWorktree) -> WorktreeCleanupState:
    """Inspect worktree state relative to the session-start HEAD.

    Commit counts intentionally use the worktree's current HEAD instead of the
    named branch so detached-HEAD commits still block cleanup.
    """
    git = _git_python()
    try:
        repo = git.repo(worktree.root)
        status_lines = repo.git.status(
            "--porcelain", "--untracked-files=all"
        ).splitlines()
        new_commit_count = int(
            repo.git.rev_list("--count", f"{worktree.base_commit}..HEAD").strip()
        )
    except (git.invalid_git_repository_error, git.git_command_error, ValueError) as e:
        raise WorktreeError(f"Failed to inspect worktree {worktree.name!r}: {e}") from e

    return WorktreeCleanupState(
        has_uncommitted_changes=any(not line.startswith("??") for line in status_lines),
        has_untracked_files=any(line.startswith("??") for line in status_lines),
        new_commit_count=new_commit_count,
    )


def remove_worktree(worktree: PreparedWorktree, *, delete_branch: bool = True) -> None:
    git = _git_python()
    _leave_worktree_if_current_directory(worktree)
    try:
        repo = git.repo(worktree.repo_root)
        repo.git.worktree("remove", "--force", str(worktree.root))
        if delete_branch:
            repo.git.branch("-D", worktree.branch)
    except (git.invalid_git_repository_error, git.git_command_error) as e:
        raise WorktreeError(f"Failed to remove worktree {worktree.name!r}: {e}") from e


def _validate_worktree_name(name: str) -> None:
    if not _is_portable_worktree_name(name):
        raise WorktreeError(
            "--worktree NAME must be a single path segment with a portable filename."
        )


def _is_portable_worktree_name(name: str) -> bool:
    if not name or name in {".", ".."}:
        return False
    if name[-1] in {" ", "."} or not name.isprintable():
        return False
    if any(char in _INVALID_WORKTREE_NAME_CHARS for char in name):
        return False
    if name.split(".", 1)[0].upper() in _RESERVED_WORKTREE_NAMES:
        return False
    return Path(name).parts == (name,) and PureWindowsPath(name).parts == (name,)


def _validate_branch(repo: Repo, branch: str) -> None:
    git = _git_python()
    try:
        repo.git.check_ref_format("--branch", branch)
    except (git.git_command_error, ValueError) as e:
        raise WorktreeError(f"Invalid worktree branch {branch!r}.") from e


def _branch_exists(repo: Repo, branch: str) -> bool:
    git = _git_python()
    try:
        repo.git.show_ref("--verify", "--quiet", f"refs/heads/{branch}")
    except git.git_command_error as e:
        if e.status == 1:
            return False
        raise WorktreeError(f"Failed to inspect worktree branch {branch!r}: {e}") from e
    return True


def _common_git_dir(repo: Repo) -> Path:
    return _resolve_git_dir(repo, repo.git.rev_parse("--git-common-dir"))


def _resolve_git_dir(repo: Repo, value: str) -> Path:
    common_git_dir = Path(value)
    if common_git_dir.is_absolute():
        return common_git_dir.resolve()
    return (Path(repo.working_dir) / common_git_dir).resolve()


def _worktree_root(repo_root: Path, common_git_dir: Path) -> Path:
    repo_hash = hashlib.sha256(str(common_git_dir).encode()).hexdigest()[:12]
    repo_dir = f"{repo_root.name}-{repo_hash}"
    managed_root = WORKTREES_DIR.path.resolve()
    target_root = (managed_root / repo_dir).resolve()
    if not target_root.is_relative_to(managed_root):
        raise WorktreeError(
            f"Managed worktree root {target_root} resolves outside {managed_root}."
        )
    return target_root


def _relative_base(repo: Repo, base: Path) -> Path:
    working_root = Path(repo.working_dir).resolve()
    try:
        return base.resolve().relative_to(working_root)
    except ValueError as e:
        raise WorktreeError(
            f"Path {base.resolve()} is outside Git worktree {working_root}."
        ) from e


def _validate_existing_worktree(
    target: Path, expected_branch: str, expected_common_git_dir: Path
) -> None:
    git = _git_python()
    if _has_linked_path_component(target):
        raise WorktreeError(
            f"Path {target} contains a symbolic link or junction, "
            "not a stable git worktree path."
        )
    if not (target / ".git").is_file():
        raise WorktreeError(f"Path {target} already exists but is not a git worktree.")

    try:
        existing_repo = git.repo(target)
    except git.invalid_git_repository_error as e:
        raise WorktreeError(
            f"Path {target} already exists but is not a git worktree."
        ) from e

    try:
        rev_parse_parts = existing_repo.git.rev_parse(
            "--git-common-dir", "--abbrev-ref", "HEAD"
        ).splitlines()
    except git.git_command_error as e:
        raise WorktreeError(f"Failed to inspect worktree {target}: {e}") from e
    if len(rev_parse_parts) != _WORKTREE_REV_PARSE_PARTS:
        raise WorktreeError(
            f"Failed to inspect worktree {target}: expected git rev-parse to return "
            f"{_WORKTREE_REV_PARSE_PARTS} lines, got {len(rev_parse_parts)}."
        )
    common_dir_value, branch = rev_parse_parts

    existing_common_git_dir = _resolve_git_dir(existing_repo, common_dir_value)
    if existing_common_git_dir != expected_common_git_dir:
        raise WorktreeError(f"Path {target} belongs to a different git repository.")

    if branch == "HEAD" or branch != expected_branch:
        actual = "detached HEAD" if branch == "HEAD" else branch
        raise WorktreeError(
            f"Path {target} is checked out on {actual!r}, expected {expected_branch!r}."
        )


def _has_linked_path_component(path: Path) -> bool:
    current = Path(path.anchor) if path.anchor else Path()
    parts = path.parts[1:] if path.anchor else path.parts
    for index, part in enumerate(parts):
        current /= part
        # Root-level aliases such as macOS /tmp -> /private/tmp are controlled by
        # the operating system, not by the worktree directory hierarchy.
        if path.anchor and index == 0:
            continue
        is_junction = getattr(current, "is_junction", None)
        if current.is_symlink() or (is_junction is not None and is_junction()):
            return True
    return False


def _target_cwd(target: Path, relative_base: Path) -> Path:
    root = target.resolve()
    target_cwd = root / relative_base
    try:
        resolved_cwd = target_cwd.resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise WorktreeError(
            f"Worktree path {target_cwd} does not exist after checkout."
        ) from e
    if not resolved_cwd.is_dir():
        raise WorktreeError(f"Worktree path {target_cwd} is not a directory.")
    try:
        resolved_cwd.relative_to(root)
    except ValueError as e:
        raise WorktreeError(
            f"Worktree path {target_cwd} resolves outside worktree {root}."
        ) from e
    current = resolved_cwd
    while current != root:
        git_marker = current / ".git"
        if git_marker.exists() or git_marker.is_symlink():
            raise WorktreeError(
                f"Worktree path {resolved_cwd} belongs to a different git repository."
            )
        current = current.parent
    return resolved_cwd


@dataclass(frozen=True)
class _WorktreeRecord:
    root: Path
    branch: str | None
    prunable: bool


def _worktree_records(repo: Repo) -> tuple[_WorktreeRecord, ...]:
    git = _git_python()
    try:
        output = _worktree_list_porcelain(repo, null_terminated=True)
        return _parse_worktree_records(output, separator="\0")
    except git.git_command_error as e:
        if e.status != _GIT_USAGE_ERROR_STATUS:
            raise WorktreeError(f"Failed to list git worktrees: {e}") from e
    try:
        output = _worktree_list_porcelain(repo, null_terminated=False)
    except git.git_command_error as e:
        raise WorktreeError(f"Failed to list git worktrees: {e}") from e
    return _parse_worktree_records(output, separator="\n")


def _worktree_list_porcelain(repo: Repo, *, null_terminated: bool) -> str:
    args = ["list", "--porcelain"]
    if null_terminated:
        args.append("-z")
    return repo.git.worktree(*args)


def _parse_worktree_records(
    output: str, *, separator: str
) -> tuple[_WorktreeRecord, ...]:
    records: list[_WorktreeRecord] = []
    current: _WorktreeRecord | None = None

    for token in output.split(separator):
        if not token:
            if current is not None:
                records.append(current)
            current = None
            continue

        field, _, value = token.partition(" ")
        if field == "worktree":
            if current is not None:
                records.append(current)
            current = _WorktreeRecord(Path(value), None, False)
        elif field == "branch" and current is not None:
            current = _WorktreeRecord(
                current.root, value.removeprefix("refs/heads/"), current.prunable
            )
        elif field == "prunable" and current is not None:
            current = _WorktreeRecord(current.root, current.branch, True)

    if current is not None:
        records.append(current)
    return tuple(records)


def _primary_worktree_root(repo: Repo, common_git_dir: Path) -> Path:
    # Repositories using --separate-git-dir report that directory as the first
    # worktree, so the primary checkout's working directory is authoritative.
    if Path(repo.git_dir).resolve() == common_git_dir:
        return Path(repo.working_dir).resolve()
    if common_git_dir.name == ".git":
        return common_git_dir.parent
    raise WorktreeError(
        "Cannot determine the primary checkout from a linked worktree "
        "using a separate Git directory."
    )


def _leave_worktree_if_current_directory(worktree: PreparedWorktree) -> None:
    try:
        cwd = Path.cwd().resolve()
    except FileNotFoundError:
        os.chdir(worktree.repo_root)
        return
    if cwd.is_relative_to(worktree.root.resolve()):
        os.chdir(worktree.repo_root)
