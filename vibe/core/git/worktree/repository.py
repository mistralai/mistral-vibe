from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum, auto
from functools import cached_property
import os
from pathlib import Path, PureWindowsPath

from vibe.core.git.errors import GitError
from vibe.core.git.repo import GitRepo, RepoPaths, _git_python
from vibe.core.git.worktree.naming import (
    worktree_name_from_text,
    worktree_name_with_suffix,
)
from vibe.core.git.worktree.record import (
    WorktreeClaim,
    WorktreeRecord,
    managed_bucket_name,
)
from vibe.core.paths import WORKTREES_DIR
from vibe.core.utils.slug import create_slug
from vibe.core.utils.time import utc_now
from vibe.observability.logging import logger

_INVALID_WORKTREE_NAME_CHARS = frozenset('<>:"/\\|?*')
_WORKTREE_REV_PARSE_PARTS = 2
_AUTO_WORKTREE_BRANCH_PREFIX = "vibe/"
_MAX_AUTO_WORKTREE_ATTEMPTS = 100
# Long enough to cover a checkout of a large repo, so a session that is still
# starting is never mistaken for one that died.
_CLAIM_SWEEP_GRACE = timedelta(minutes=10)
_RESERVED_WORKTREE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


class WorktreeError(GitError): ...


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

    def inspect_for_cleanup(self) -> WorktreeCleanupState:
        """Inspect worktree state relative to the session-start HEAD.

        Commit counts intentionally use the worktree's current HEAD instead of
        the named branch so detached-HEAD commits still block cleanup.
        """
        git = _git_python()
        try:
            repo = git.repo(self.root)
            status_lines = repo.git.status(
                "--porcelain", "--untracked-files=all"
            ).splitlines()
            new_commit_count = int(
                repo.git.rev_list("--count", f"{self.base_commit}..HEAD").strip()
            )
        except (
            git.invalid_git_repository_error,
            git.git_command_error,
            ValueError,
        ) as e:
            raise WorktreeError(f"Failed to inspect worktree {self.name!r}: {e}") from e

        return WorktreeCleanupState(
            has_uncommitted_changes=any(
                not line.startswith("??") for line in status_lines
            ),
            has_untracked_files=any(line.startswith("??") for line in status_lines),
            new_commit_count=new_commit_count,
        )

    def remove(self, *, delete_branch: bool = True) -> None:
        git = _git_python()
        try:
            repo = git.repo(self.repo_root)
            repo.git.worktree("remove", "--force", str(self.root))
            if delete_branch:
                repo.git.branch("-D", self.branch)
        except (git.invalid_git_repository_error, git.git_command_error) as e:
            raise WorktreeError(f"Failed to remove worktree {self.name!r}: {e}") from e

    # Callers that own the process working directory must invoke this before
    # remove(); Windows refuses to delete a directory that is any process's cwd.
    # remove() itself must not chdir, because the app-server serves concurrent
    # sessions and resolves relative paths against the process cwd.
    def leave_if_current_directory(self) -> None:
        try:
            cwd = Path.cwd().resolve()
        except FileNotFoundError:
            os.chdir(self.repo_root)
            return
        if cwd.is_relative_to(self.root.resolve()):
            os.chdir(self.repo_root)


@dataclass(frozen=True)
class LinkedWorktree:
    name: str
    branch: str
    root: Path
    path: Path
    repo_root: Path


class WorktreeReleaseOutcome(StrEnum):
    REMOVED = auto()
    KEPT_DIRTY = auto()
    KEPT_IN_USE = auto()
    KEPT_UNMANAGED = auto()
    NOT_FOUND = auto()


@dataclass(frozen=True)
class WorktreeRelease:
    outcome: WorktreeReleaseOutcome
    root: Path | None = None
    branch: str | None = None
    branch_deleted: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)


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


class WorktreeRepository:
    """The managed worktrees of one git repository.

    Opened against a path inside the repository rather than the repository
    root, because that path decides two things at once: which repository is
    meant, and which subdirectory of a prepared worktree the caller lands in.
    """

    def __init__(self, git: GitRepo, base: Path) -> None:
        self._git = git
        self._base = base

    # GitPython keeps `git cat-file --batch` children alive holding handles into
    # .git until the Repo is closed, which is why closing is the context
    # manager's job rather than each caller's: a background sweep that forgot
    # would hold those handles past app-server teardown.
    @classmethod
    @contextmanager
    def open(cls, base: Path) -> Iterator[WorktreeRepository]:
        git = GitRepo.open(base)
        try:
            yield cls(git, base)
        finally:
            git.close()

    # Identifies the repository a path belongs to, for a caller telling two
    # repositories apart that has no reason to care whether the path is in one.
    @classmethod
    def bucket_for(cls, base: Path) -> str | None:
        try:
            with cls.open(base) as repository:
                return repository.bucket
        except GitError:
            return None

    @classmethod
    def sweep_claims(cls, base: Path) -> None:
        try:
            with cls.open(base) as repository:
                repository.sweep()
        except GitError:
            return

    @property
    def root(self) -> Path:
        return self._paths.repo_root

    @property
    def bucket(self) -> str:
        paths = self._paths
        return managed_bucket_name(paths.repo_root, paths.common_git_dir)

    # Derived on access, not eagerly: _worktree_root enforces containment under
    # WORKTREES_DIR and raises, so callers that only read git metadata must not
    # pay for a check they never needed.
    @property
    def worktree_root(self) -> Path:
        paths = self._paths
        return _worktree_root(paths.repo_root, paths.common_git_dir)

    @property
    def _paths(self) -> RepoPaths:
        return self._git.paths

    @cached_property
    def _relative_base(self) -> Path:
        return self._git.relative_base(self._base)

    def prepare(self, name: str, *, branch: str | None = None) -> PreparedWorktree:
        _validate_worktree_name(name)
        branch = name if branch is None else branch
        self._git.validate_branch(branch)

        paths = self._paths
        target = self.worktree_root / name

        # mkdir is the atomic claim here for the reason _claim_auto_name gives,
        # and for one more: without it two callers for the same name both write
        # the claim and both run `git worktree add`, and the loser's cleanup
        # deletes the record out from under the winner's live worktree, leaving
        # it unmanaged. Reserving the path first means the loser never gets far
        # enough to clean anything up.
        try:
            target.mkdir(parents=True)
        except FileExistsError:
            _validate_existing_worktree(target, branch, paths.common_git_dir)
            return self._build_prepared(
                name, branch, target, created=False, branch_created=False
            )
        except OSError as e:
            raise WorktreeError(
                f"Failed to claim worktree directory {target}: {e}"
            ) from e

        branch_created = not self._git.branch_exists(branch)
        claim = WorktreeClaim(bucket=self.bucket, name=name)
        record = WorktreeRecord.new(
            name=name,
            branch=branch,
            repo_root=paths.repo_root,
            branch_created=branch_created,
        )
        # Reserved before the add, as the auto path does. A crash in between
        # then leaves a record with no base_commit, which the sweep reclaims;
        # recorded after the add instead, the same crash would leave a worktree
        # no claim describes, and nothing recovers that - the sweep walks
        # claims, and release reports an unrecorded worktree unmanaged.
        claim.write(record)
        return self._create(claim, record, target, branch_created=branch_created)

    def prepare_auto(
        self, *, prompt: str | None = None, suggested_name: str | None = None
    ) -> PreparedWorktree:
        paths = self._paths
        name, branch, target = self._claim_auto_name(
            _auto_worktree_name(prompt, suggested_name)
        )
        claim = WorktreeClaim(bucket=self.bucket, name=name)
        record = WorktreeRecord.new(
            name=name, branch=branch, repo_root=paths.repo_root, branch_created=True
        )
        claim.write(record)
        return self._create(claim, record, target, branch_created=True)

    def linked(self) -> tuple[LinkedWorktree, ...]:
        paths = self._paths
        # Resolved up front, not inside the loop: a base that escapes the
        # checkout is the caller's error, and reading it lazily would let the
        # per-record except swallow it as "skip this worktree" - or, in a
        # repository with no linked worktrees, never check it at all.
        relative_base = self._relative_base
        records = self._git.records()
        linked: list[LinkedWorktree] = []

        for record in records[1:]:
            if record.branch is None or record.prunable:
                continue
            try:
                _validate_existing_worktree(
                    record.root, record.branch, paths.common_git_dir
                )
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
                    repo_root=paths.repo_root,
                )
            )

        return tuple(sorted(linked, key=lambda worktree: str(worktree.path)))

    def sweep(self) -> None:
        try:
            worktree_root = self.worktree_root
        except GitError:
            return
        cutoff = utc_now() - _CLAIM_SWEEP_GRACE

        for claim in WorktreeClaim.in_bucket(self.bucket):
            record = claim.read()
            # An app-server that died leaves a worktree with a stale holder and
            # no way to know whether its session was finished with. It is kept:
            # the session is still on disk and resumable, and its worktree is
            # the work. Only a reservation that never became a worktree is
            # swept - an empty directory from a mkdir claim whose `git worktree
            # add` never landed. It holds nothing and no session ever saw it.
            if record is None or record.base_commit is not None:
                continue
            # The grace period guards the live claim: between the mkdir and the
            # add completing, the directory is legitimately empty.
            if record.claimed_at > cutoff or claim.holders():
                continue
            target = worktree_root / claim.name
            if not _is_empty_dir(target):
                logger.warning(
                    "Keeping worktree %s: its claim records no base commit, so "
                    "there is no baseline to judge it against",
                    target,
                )
                continue
            self._discard_claim(
                target, record.branch, branch_created=record.branch_created
            )
            logger.info("Discarded an abandoned worktree reservation at %s", target)

    def _base_ref(self) -> str | None:
        """The ref a newly created worktree branch starts from.

        The remote's default branch rather than the invoking checkout's HEAD,
        which is whatever the user happened to have checked out and, for a
        `main` that is rarely pulled, weeks behind.

        None leaves the start point off the `git worktree add` entirely, so a
        repository with no remote keeps git's own default.
        """
        ref = self._git.remote_default_branch_ref()
        if ref is None:
            return None
        remote, _, branch = ref.partition("/")
        try:
            self._git.fetch_branch(remote, branch)
        except GitError as e:
            # Offline, or the remote refused. The remote-tracking ref is still
            # a better base than HEAD even when stale, because it advances on
            # any fetch while a local branch only moves when pulled.
            logger.warning("Could not refresh %s before branching: %s", ref, e)
        return ref

    def _create(
        self,
        claim: WorktreeClaim,
        record: WorktreeRecord,
        target: Path,
        *,
        branch_created: bool,
    ) -> PreparedWorktree:
        branch = record.branch
        start_point = self._base_ref() if branch_created else None
        try:
            self._git.add_worktree(
                target, branch, branch_created=branch_created, start_point=start_point
            )
        except Exception:
            self._discard_claim(target, branch, branch_created=branch_created)
            raise
        try:
            prepared = self._build_prepared(
                record.name, branch, target, created=True, branch_created=branch_created
            )
        except Exception as e:
            if note := self._clean_up_failed_prepare(target, branch, branch_created):
                e.add_note(note)
            raise
        claim.write(record.model_copy(update={"base_commit": prepared.base_commit}))
        return prepared

    def _build_prepared(
        self,
        name: str,
        branch: str,
        target: Path,
        *,
        created: bool,
        branch_created: bool,
    ) -> PreparedWorktree:
        # base_commit is the worktree's own HEAD at session start, so cleanup
        # counts only commits added during this session (not commits an attached
        # or reused branch already carried, which the invoking checkout's HEAD
        # would miss).
        return PreparedWorktree(
            name=name,
            branch=branch,
            root=target,
            path=_target_cwd(target, self._relative_base),
            repo_root=self._paths.repo_root,
            base_commit=GitRepo.head_commit_at(target),
            created=created,
            branch_created=branch_created,
        )

    def _claim_auto_name(self, base_name: str) -> tuple[str, str, Path]:
        # mkdir is the atomic claim: git worktree add cannot report *why* it
        # failed (a taken path and an invalid ref both exit 128), so a lost race
        # has to be detected before git runs or it is indistinguishable from a
        # real failure.
        worktree_root = self.worktree_root
        for name in _auto_worktree_candidates(base_name):
            branch = f"{_AUTO_WORKTREE_BRANCH_PREFIX}{name}"
            if self._git.branch_exists(branch):
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

    def _discard_claim(
        self, target: Path, branch: str, *, branch_created: bool
    ) -> None:
        _delete_record_for(target)
        # rmdir refuses a populated directory, so a partial checkout is never
        # lost, and git's own remove_junk may have removed it already.
        with suppress(OSError):
            target.rmdir()
        # Only a branch this reservation brought into being. A named worktree
        # can ask for a branch that already existed and holds the user's work,
        # and that one has to survive the reservation being discarded.
        if not branch_created:
            return
        # `worktree add -b` creates the branch before it validates the path, so
        # a failed add leaves it behind. _claim_auto_name would then skip this
        # name for every later call, walking the suffix chain until it reports
        # exhaustion instead of the real failure.
        #
        # A safe delete rather than a forced one: the branch this call created
        # still points at HEAD, so it goes, while a racing branch carrying
        # commits is refused. That is a partial guard, not a full one -- a
        # branch someone else created at HEAD between the branch_exists check
        # and the add is merged too, so it is deleted (recoverable from the
        # reflog). Proving ownership would mean leaving the branch and burning
        # the name on every failure, which costs more than the race it closes.
        with suppress(GitError):
            self._git.delete_branch(branch)

    def _clean_up_failed_prepare(
        self, target: Path, branch: str, branch_created: bool
    ) -> str | None:
        _delete_record_for(target)
        try:
            self._git.remove_worktree(target)
            if branch_created:
                self._git.delete_branch(branch, force=True)
        except GitError as e:
            return (
                f"Failed to clean up worktree {target.name!r} after prepare "
                f"failure: {e}"
            )
        return None


@dataclass(frozen=True)
class ManagedWorktree:
    """A worktree Vibe created, addressed by a path inside it.

    Wraps the claim so holding, releasing and removing all go through one
    value. `at` returning None is the whole "is this one of ours?" question: an
    unmanaged directory has no claim, and each caller decides what that means
    rather than having a silent no-op decide for it.
    """

    claim: WorktreeClaim

    # Deliberately not conditioned on the record existing: the CLI deletes the
    # record with forget() and only then drops its holder, and that second call
    # still has to find the claim to clean up after itself.
    @classmethod
    def at(cls, cwd: Path) -> ManagedWorktree | None:
        claim = WorktreeClaim.locate(cwd)
        return None if claim is None else cls(claim=claim)

    @property
    def name(self) -> str:
        return self.claim.name

    @property
    def root(self) -> Path:
        return WORKTREES_DIR.path.resolve() / self.claim.bucket / self.claim.name

    def hold(self, session_id: str) -> None:
        # A directory under the managed root with no record is not ours to
        # hold: either it is someone else's, or the claim is already released.
        if self.claim.read() is None:
            return
        self.claim.add_holder(session_id)

    def release_holder(self, session_id: str) -> None:
        self.claim.remove_holder(session_id)

    def holders(self) -> frozenset[str]:
        return self.claim.holders()

    # For a caller that removed the worktree itself, like the CLI's interactive
    # exit cleanup. Leaving the record behind would keep a claim for a directory
    # that no longer exists.
    def forget(self) -> None:
        self.claim.delete()

    def release(self, session_id: str | None = None) -> WorktreeRelease:
        record = self.claim.read()
        if record is None:
            return WorktreeRelease(WorktreeReleaseOutcome.KEPT_UNMANAGED)

        # None means the caller never held it - a delete arriving after the
        # session already closed. Every other holder still has to be gone.
        if session_id is not None:
            self.claim.remove_holder(session_id)
        if remaining := self.claim.holders():
            logger.debug(
                "Keeping worktree %s: still held by %d session(s)",
                self.claim.name,
                len(remaining),
            )
            return WorktreeRelease(
                WorktreeReleaseOutcome.KEPT_IN_USE, branch=record.branch
            )

        return self._release_unheld(record)

    def _release_unheld(self, record: WorktreeRecord) -> WorktreeRelease:
        root = self.root
        if not root.is_dir():
            self.claim.delete()
            return WorktreeRelease(WorktreeReleaseOutcome.NOT_FOUND)

        if record.base_commit is None:
            # The claim never became a worktree, or the second record write was
            # lost. Either way there is no baseline to judge cleanliness
            # against, so leave it for the sweep rather than guess.
            return WorktreeRelease(WorktreeReleaseOutcome.KEPT_UNMANAGED)

        prepared = PreparedWorktree(
            name=self.claim.name,
            branch=record.branch,
            root=root,
            path=root,
            repo_root=record.repo_root,
            base_commit=record.base_commit,
            created=True,
            branch_created=record.branch_created,
        )
        state = prepared.inspect_for_cleanup()
        if not state.is_clean:
            logger.info(
                "Keeping worktree %s on branch %s: %s",
                root,
                record.branch,
                ", ".join(state.reasons),
            )
            return WorktreeRelease(
                WorktreeReleaseOutcome.KEPT_DIRTY,
                root=root,
                branch=record.branch,
                reasons=state.reasons,
            )

        # Re-checked immediately before the destructive step. Inspecting a
        # worktree shells out to git, which is long enough for a session in
        # another process to claim it. This narrows the window rather than
        # closing it: without a cross-process lock, a holder written during
        # remove() itself still loses. Losing means a live session in a deleted
        # directory, so the check is worth the extra stat even though it is not
        # a guarantee.
        if late := self.claim.holders():
            logger.info(
                "Keeping worktree %s: %d session(s) joined during inspection",
                root,
                len(late),
            )
            return WorktreeRelease(
                WorktreeReleaseOutcome.KEPT_IN_USE, root=root, branch=record.branch
            )

        prepared.remove(delete_branch=record.branch_created)
        self.claim.delete()
        return WorktreeRelease(
            WorktreeReleaseOutcome.REMOVED,
            root=root,
            branch=record.branch,
            branch_deleted=record.branch_created,
        )


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


def _is_empty_dir(target: Path) -> bool:
    try:
        return not any(target.iterdir())
    except OSError:
        return False


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


def _delete_record_for(target: Path) -> None:
    if claim := WorktreeClaim.locate(target):
        claim.delete()


def _worktree_root(repo_root: Path, common_git_dir: Path) -> Path:
    repo_dir = managed_bucket_name(repo_root, common_git_dir)
    managed_root = WORKTREES_DIR.path.resolve()
    target_root = (managed_root / repo_dir).resolve()
    if not target_root.is_relative_to(managed_root):
        raise WorktreeError(
            f"Managed worktree root {target_root} resolves outside {managed_root}."
        )
    return target_root


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

    existing_common_git_dir = GitRepo(existing_repo).resolve_git_dir(common_dir_value)
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
