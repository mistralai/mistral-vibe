from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from enum import StrEnum, auto
from functools import cached_property
import os
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import uuid4

from vibe.core.git.errors import GitError
from vibe.core.git.repo import GitRepo, GitStatus, RepoPaths, _git_python
from vibe.core.git.worktree.naming import (
    worktree_name_from_text,
    worktree_name_with_suffix,
)
from vibe.core.git.worktree.record import (
    WorktreeClaim,
    WorktreeRecord,
    WorktreeRecordError,
    WorktreeRecoveryRecord,
    managed_bucket_name,
    worktree_prune_lock,
)
from vibe.core.paths import WORKTREES_DIR
from vibe.core.utils.slug import create_slug
from vibe.observability.logging import logger

_INVALID_WORKTREE_NAME_CHARS = frozenset('<>:"/\\|?*')
_WORKTREE_REV_PARSE_PARTS = 2
_AUTO_WORKTREE_BRANCH_PREFIX = "vibe/"
_MAX_AUTO_WORKTREE_ATTEMPTS = 100
# Under `refs/vibe/` rather than `refs/heads/`, so a snapshot never appears in
# the branch picker, in `git branch`, or as something to push. It is a way back
# to work removed by explicit deletion or automatic retention.
SNAPSHOT_REF_PREFIX = "refs/vibe/reaped"
_RESERVED_WORKTREE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


class WorktreeError(GitError): ...


@dataclass(frozen=True)
class PendingSessionHold:
    """Temporary holder bridging worktree resolution and session attachment.

    Pruning must see the worktree as occupied before the session has an ID it
    can register as a durable holder. This hold closes that gap and is released
    after the session holder is installed, or when startup is abandoned.
    """

    claim: WorktreeClaim
    holder_id: str

    def release(self) -> None:
        self.claim.remove_holder(self.holder_id)


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
    pending_hold: PendingSessionHold | None = None

    def inspect_for_cleanup(self) -> WorktreeCleanupState:
        """Inspect worktree state relative to the session-start HEAD.

        Commit counts intentionally use the worktree's current HEAD instead of
        the named branch so detached-HEAD commits still block cleanup.
        """
        git = _git_python()
        try:
            repo = git.repo(self.root)
            status_lines = (
                _unhooked(repo)
                .status("--porcelain", "--untracked-files=all")
                .splitlines()
            )
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

    def snapshot(self) -> str:
        """Commit this worktree's whole state to a ref nothing checks out.

        Returns the ref. Raises when the state could not be saved, which is
        the caller's signal to keep the worktree instead of removing it.

        Explicit deletion and automatic retention can remove a worktree even
        when work was left behind. Untracked files are included; ignored files
        are not.

        Written through a second index, so the worktree's own is untouched: a
        failure here aborts the removal, and the worktree the user is then left
        with must be the one they had. That index lives in the worktree's own
        git directory, which the removal takes with it, rather than in a
        temporary directory that would outlive a crash.
        """
        git = _git_python()
        ref = f"{SNAPSHOT_REF_PREFIX}/{self.name}"
        try:
            repo = git.repo(self.root)
            index = Path(repo.git.rev_parse("--absolute-git-dir").strip())
            with repo.git.custom_environment(
                GIT_INDEX_FILE=str(index / "index.vibe-snapshot")
            ):
                _unhooked(repo).read_tree("HEAD")
                _unhooked(repo).add("--all", ".")
                tree = repo.git.write_tree().strip()
                head = repo.git.rev_parse("HEAD").strip()
                commit = repo.git.commit_tree(
                    tree,
                    "-p",
                    head,
                    "-m",
                    f"vibe: state of worktree {self.name} before it was removed",
                ).strip()
            repo.git.update_ref(ref, commit)
        except (git.invalid_git_repository_error, git.git_command_error) as e:
            raise WorktreeError(
                f"Failed to snapshot worktree {self.name!r}: {e}"
            ) from e
        return ref

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


@dataclass(frozen=True)
class RetainedRepositoryMapping:
    root: Path
    cwd: Path


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
    # Where the work went when a removed worktree still had some. None when it
    # had none, so a caller can tell "nothing to recover" from "recover here".
    snapshot_ref: str | None = None


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
    # manager's job rather than each caller's: a caller that forgot would hold
    # those handles past app-server teardown.
    @classmethod
    @contextmanager
    def open(
        cls, base: Path, *, repository_root: Path | None = None
    ) -> Iterator[WorktreeRepository]:
        git = GitRepo.open(base if repository_root is None else repository_root)
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

    @property
    def root(self) -> Path:
        return self._paths.repo_root

    @property
    def repository_counterpart(self) -> Path | None:
        """Where the base sits when mapped onto the main checkout.

        ``linked()`` reports this same mapping for each worktree, preserving the
        subdirectory the caller opened at. The main checkout has no entry there,
        so a caller comparing against both needs this to complete the set.

        None when there is no usable counterpart. It goes through the checks
        ``linked()`` applies rather than resolving the path directly, so a
        symlink that leaves the checkout is refused instead of being reported
        as a position inside it.
        """
        try:
            return _target_cwd(self._paths.repo_root, self._relative_base)
        except WorktreeError:
            return None

    @property
    def repository_mapped_cwd(self) -> Path:
        return self._paths.repo_root / self._relative_base

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
        claim = WorktreeClaim(bucket=self.bucket, name=name)
        if claim.has_recovery() and not target.exists():
            raise WorktreeError(
                f"Worktree {name!r} is reserved by a retained session snapshot."
            )

        # mkdir is the atomic claim here for the reason _claim_auto_name gives,
        # and for one more: without it two callers for the same name both write
        # the claim and both run `git worktree add`, and the loser's cleanup
        # deletes the record out from under the winner's live worktree, leaving
        # it unmanaged. Reserving the path first means the loser never gets far
        # enough to clean anything up.
        try:
            target.mkdir(parents=True)
        except FileExistsError:
            managed = ManagedWorktree(
                claim=WorktreeClaim(bucket=self.bucket, name=name)
            )
            pending_hold = managed.hold_for_attachment()
            try:
                _validate_existing_worktree(target, branch, paths.common_git_dir)
                return self._build_prepared(
                    name,
                    branch,
                    target,
                    created=False,
                    branch_created=False,
                    pending_hold=pending_hold,
                )
            except BaseException:
                if pending_hold is not None:
                    pending_hold.release()
                raise
        except OSError as e:
            raise WorktreeError(
                f"Failed to claim worktree directory {target}: {e}"
            ) from e

        branch_created = not self._git.branch_exists(branch)
        record = WorktreeRecord.new(
            name=name,
            branch=branch,
            repo_root=paths.repo_root,
            branch_created=branch_created,
        )
        # Reserved before the add, as the auto path does. Recording after the add
        # would let a crash leave a worktree with no ownership record, which all
        # automatic cleanup must treat as unmanaged.
        self._record_starting_claim(claim, record, target)
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
        self._record_starting_claim(claim, record, target)
        return self._create(claim, record, target, branch_created=True)

    @staticmethod
    def _record_starting_claim(
        claim: WorktreeClaim, record: WorktreeRecord, target: Path
    ) -> None:
        try:
            claim.mark_starting()
            claim.write(record)
        except BaseException:
            claim.delete()
            with suppress(OSError):
                target.rmdir()
            raise

    def status(self) -> GitStatus:
        """This repository's own checkout, off the open this object holds.

        The listing and the status answer about the same repository, and
        opening it is what costs: each open leaves ``git cat-file --batch``
        children holding handles into .git until it is closed. Read together
        they cost one open instead of two.
        """
        return self._git.status()

    def checkouts(self) -> tuple[tuple[Path, str | None], ...]:
        """Every checkout git reports for this repository, and its branch.

        Wider than ``linked()``, which keeps only the managed worktrees a
        session may be moved into. A session can be *sitting* in one that is
        detached, prunable, or fails validation, and such a checkout is absent
        from that listing: asked from it alone, the repository looks as though
        it does not hold the session at all, and the branch reported for it is
        the main checkout's rather than the one the session is on.

        Branch is None for a detached checkout, which is a real answer here
        rather than a missing one.

        Read off the same open as ``linked()``, so a caller needing both pays
        for one repository rather than two.
        """
        return tuple(
            (record.root.resolve(), record.branch) for record in self._git.records()
        )

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
        start_point: str | None = None,
    ) -> PreparedWorktree:
        branch = record.branch
        if branch_created and start_point is None:
            start_point = self._base_ref()
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

    def restore(
        self, claim: WorktreeClaim, recovery: WorktreeRecoveryRecord
    ) -> PreparedWorktree:
        if claim.bucket != self.bucket or claim.name != recovery.name:
            raise WorktreeError(
                "Worktree recovery metadata does not match its repository."
            )

        target = self.worktree_root / claim.name
        try:
            claim.mark_starting()
        except WorktreeRecordError as exc:
            raise WorktreeError(
                f"Worktree restore is already in progress for {target}."
            ) from exc

        reserved = False
        try:
            branch = self._available_recovery_branch(recovery.branch)
            record = WorktreeRecord.new(
                name=claim.name,
                branch=branch,
                repo_root=self._paths.repo_root,
                branch_created=True,
            )
            try:
                target.mkdir(parents=True)
                reserved = True
            except FileExistsError as exc:
                raise WorktreeError(
                    f"Cannot restore occupied worktree path {target}."
                ) from exc
            except OSError as exc:
                raise WorktreeError(
                    f"Failed to reserve worktree path {target}: {exc}"
                ) from exc
            claim.write(record)
        except BaseException:
            claim.finish_starting()
            if reserved:
                with suppress(OSError):
                    target.rmdir()
            raise

        try:
            restored = self._create(
                claim,
                record,
                target,
                branch_created=True,
                start_point=recovery.snapshot_ref,
            )
        except BaseException:
            claim.finish_starting()
            raise
        return restored

    def _available_recovery_branch(self, preferred: str) -> str:
        if not self._git.branch_exists(preferred):
            return preferred
        base = f"{preferred}-restored"
        for suffix in range(1, _MAX_AUTO_WORKTREE_ATTEMPTS + 1):
            candidate = base if suffix == 1 else f"{base}-{suffix}"
            if not self._git.branch_exists(candidate):
                return candidate
        raise WorktreeError(
            f"Unable to find an unused recovery branch for {preferred!r}."
        )

    def _build_prepared(
        self,
        name: str,
        branch: str,
        target: Path,
        *,
        created: bool,
        branch_created: bool,
        pending_hold: PendingSessionHold | None = None,
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
            pending_hold=pending_hold,
        )

    def _claim_auto_name(self, base_name: str) -> tuple[str, str, Path]:
        # mkdir is the atomic claim: git worktree add cannot report *why* it
        # failed (a taken path and an invalid ref both exit 128), so a lost race
        # has to be detected before git runs or it is indistinguishable from a
        # real failure.
        worktree_root = self.worktree_root
        for name in _auto_worktree_candidates(base_name):
            branch = f"{_AUTO_WORKTREE_BRANCH_PREFIX}{name}"
            claim = WorktreeClaim(bucket=self.bucket, name=name)
            if self._git.branch_exists(branch) or claim.has_recovery():
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

    def retained_repository_mapping(
        self, cwd: Path
    ) -> RetainedRepositoryMapping | None:
        recovery = self.claim.read_recovery()
        if recovery is None:
            return None
        try:
            relative = cwd.expanduser().resolve().relative_to(self.root.resolve())
            root = recovery.repo_root.expanduser().resolve()
            return RetainedRepositoryMapping(root=root, cwd=root / relative)
        except (OSError, RuntimeError, ValueError):
            return None

    def hold_for_attachment(self) -> PendingSessionHold | None:
        """Prevent pruning until a resolved worktree gains its session holder."""
        holder_id = f"attach-{uuid4().hex}"
        with worktree_prune_lock():
            if self.claim.read() is None:
                return None
            self.claim.add_holder(holder_id)
        return PendingSessionHold(claim=self.claim, holder_id=holder_id)

    def hold(
        self, session_id: str, pending_hold: PendingSessionHold | None = None
    ) -> None:
        if pending_hold is not None and pending_hold.claim != self.claim:
            pending_hold.release()
            raise WorktreeError("Pending session hold does not match the session cwd")
        # A directory under the managed root with no record is not ours to
        # hold: either it is someone else's, or the claim is already released.
        if self.claim.read() is None:
            if pending_hold is not None:
                pending_hold.release()
            return
        try:
            self.claim.add_holder(session_id)
        finally:
            if pending_hold is not None:
                pending_hold.release()
        self.claim.finish_starting()

    def release_holder(self, session_id: str) -> None:
        self.claim.remove_holder(session_id)

    def holders(self) -> frozenset[str]:
        return self.claim.holders()

    @classmethod
    def prune(cls, limit: int) -> int:
        """Snapshot and remove oldest inactive managed worktrees."""
        if limit < 0:
            raise ValueError("limit must be non-negative")

        with worktree_prune_lock():
            cls._reclaim_abandoned_reservations()
            claimed = sorted(
                (
                    (record.claimed_at, claim)
                    for claim in WorktreeClaim.all()
                    if (record := claim.read()) is not None
                    and record.base_commit is not None
                ),
                key=lambda item: (item[0], item[1].bucket, item[1].name),
            )
            excess = len(claimed) - limit
            removed = 0
            for _, claim in claimed:
                if excess <= 0:
                    break
                if claim.is_starting():
                    continue
                try:
                    release = cls(claim=claim)._prune_with_snapshot()
                except (GitError, OSError) as exc:
                    logger.warning(
                        "Keeping managed worktree %s/%s: retention cleanup failed",
                        claim.bucket,
                        claim.name,
                        exc_info=exc,
                    )
                    continue
                if release.outcome in {
                    WorktreeReleaseOutcome.REMOVED,
                    WorktreeReleaseOutcome.NOT_FOUND,
                }:
                    excess -= 1
                if release.outcome is WorktreeReleaseOutcome.REMOVED:
                    removed += 1
            return removed

    @classmethod
    def _reclaim_abandoned_reservations(cls) -> None:
        for claim in WorktreeClaim.all():
            record = claim.read()
            if (
                record is None
                or record.base_commit is not None
                or claim.is_starting()
                or claim.holders()
            ):
                continue
            target = cls(claim=claim).root
            try:
                if target.is_symlink() or (target.exists() and not target.is_dir()):
                    continue
            except OSError:
                continue
            if (target / ".git").is_file():
                try:
                    base_commit = GitRepo.head_commit_at(target)
                except GitError:
                    continue
                claim.write(record.model_copy(update={"base_commit": base_commit}))
                continue
            try:
                is_empty = not target.exists() or not any(target.iterdir())
            except OSError:
                continue
            if not is_empty:
                continue
            claim.delete()
            with suppress(OSError):
                target.rmdir()
            if record.branch_created:
                try:
                    with GitRepo.open(record.repo_root) as git:
                        with suppress(GitError):
                            git.delete_branch(record.branch)
                except GitError:
                    pass

    # For a caller that removed the worktree itself, like the CLI's interactive
    # exit cleanup. Leaving the record behind would keep a claim for a directory
    # that no longer exists.
    def forget(self) -> None:
        self.claim.delete()

    def release(self, session_id: str | None = None) -> WorktreeRelease:
        record = self.claim.read()
        if record is None:
            if (
                session_id is None
                and (recovery := self.claim.read_recovery()) is not None
            ):
                self._discard_retained_snapshot(recovery)
                return WorktreeRelease(
                    WorktreeReleaseOutcome.REMOVED, branch=recovery.branch
                )
            return WorktreeRelease(WorktreeReleaseOutcome.KEPT_UNMANAGED)
        if self.root.is_dir() and self.claim.is_starting():
            return WorktreeRelease(
                WorktreeReleaseOutcome.KEPT_IN_USE, branch=record.branch
            )

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

    def _discard_retained_snapshot(self, recovery: WorktreeRecoveryRecord) -> None:
        self.claim.delete_recovery()
        git = _git_python()
        try:
            with git.repo(recovery.repo_root) as repository:
                repository.git.update_ref("-d", recovery.snapshot_ref)
        except (
            git.invalid_git_repository_error,
            git.no_such_path_error,
            git.git_command_error,
        ) as exc:
            logger.warning(
                "Failed to delete retained snapshot %s: %s", recovery.snapshot_ref, exc
            )

    def _prune_with_snapshot(self) -> WorktreeRelease:
        record = self.claim.read()
        if record is None:
            return WorktreeRelease(WorktreeReleaseOutcome.KEPT_UNMANAGED)
        if self.claim.is_starting() or self.claim.holders():
            return WorktreeRelease(
                WorktreeReleaseOutcome.KEPT_IN_USE, branch=record.branch
            )

        root = self.root
        if not root.is_dir():
            self.claim.delete()
            return WorktreeRelease(WorktreeReleaseOutcome.NOT_FOUND)
        if record.base_commit is None:
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
        try:
            snapshot = prepared.snapshot()
        except (WorktreeError, OSError) as exc:
            logger.warning(
                "Keeping worktree %s: retention snapshot failed", root, exc_info=exc
            )
            return WorktreeRelease(
                WorktreeReleaseOutcome.KEPT_DIRTY, root=root, branch=record.branch
            )

        late = self.claim.holders()
        if self.claim.is_starting() or late:
            logger.info("Keeping worktree %s: claimed during inspection", root)
            release = WorktreeRelease(
                WorktreeReleaseOutcome.KEPT_IN_USE, root=root, branch=record.branch
            )
        else:
            self.claim.write_recovery(
                WorktreeRecoveryRecord.new(record, snapshot_ref=snapshot)
            )
            prepared.remove(delete_branch=record.branch_created)
            self.claim.delete()
            logger.info("Pruned worktree %s after saving %s", root, snapshot)
            release = WorktreeRelease(
                WorktreeReleaseOutcome.REMOVED,
                root=root,
                branch=record.branch,
                branch_deleted=record.branch_created,
                snapshot_ref=snapshot,
            )
        return release

    def restore(self, cwd: Path) -> bool:
        requested = cwd.expanduser().resolve()
        root = self.root
        try:
            requested.relative_to(root)
        except ValueError as exc:
            raise WorktreeError(
                f"Path {requested} is outside worktree {root}."
            ) from exc

        with worktree_prune_lock():
            return self._restore_locked(requested, root)

    def _restore_locked(self, requested: Path, root: Path) -> bool:
        recovery = self.claim.read_recovery()
        if recovery is None:
            return False
        if self.claim.is_starting():
            raise WorktreeError(f"Worktree restore is already in progress for {root}.")

        # A restore interrupted between mkdir and recording its claim can leave
        # only the empty path reservation behind. Remove that reservation so
        # recovery can be retried, while failing closed for occupied paths.
        try:
            if not root.is_symlink() and root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except OSError as exc:
            raise WorktreeError(
                f"Cannot restore occupied worktree path {root}."
            ) from exc

        if root.exists():
            record = self.claim.read()
            if record is None or record.base_commit is None:
                raise WorktreeError(f"Cannot restore occupied worktree path {root}.")
            with WorktreeRepository.open(record.repo_root) as repository:
                _validate_existing_worktree(
                    root, record.branch, repository._paths.common_git_dir
                )
            _ensure_saved_directory(requested)
            self.claim.delete_recovery()
            return False

        with WorktreeRepository.open(recovery.repo_root) as repository:
            repository.restore(self.claim, recovery)
        try:
            _ensure_saved_directory(requested)
            self.claim.delete_recovery()
        except BaseException:
            self.claim.finish_starting()
            raise
        logger.info(
            "Restored retained worktree %s from %s", root, recovery.snapshot_ref
        )
        return True

    def _release_unheld(self, record: WorktreeRecord) -> WorktreeRelease:
        root = self.root
        if not root.is_dir():
            self.claim.delete()
            return WorktreeRelease(WorktreeReleaseOutcome.NOT_FOUND)

        if record.base_commit is None:
            # The claim never became a worktree, or the second record write was
            # lost. Either way there is no baseline to judge cleanliness
            # against, so leave it rather than guess.
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
        snapshot: str | None = None
        if not state.is_clean:
            # Work left behind is a reason to save it, not a reason to keep the
            # directory. Keeping was safe and unbounded: a session deleted after
            # writing one uncommitted file left a worktree nothing would ever
            # collect, and dogfooding grew a pile of them. So the removal is
            # made recoverable instead, and only a snapshot that will not
            # write is still worth stopping for.
            try:
                snapshot = prepared.snapshot()
            except (WorktreeError, OSError) as exc:
                logger.warning(
                    "Keeping worktree %s on branch %s: %s could not be saved (%s)",
                    root,
                    record.branch,
                    ", ".join(state.reasons),
                    exc,
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
        if snapshot is not None:
            # At INFO with the command spelled out, because this is the only
            # trace of the work left and a ref nobody can name is not a way
            # back. `git branch -D` above orphans any commits the session made;
            # the ref is what keeps them reachable.
            logger.info(
                "Removed worktree %s, which still had %s. Recover it with: "
                "git -C %s switch -c %s %s",
                root,
                ", ".join(state.reasons),
                record.repo_root,
                self.claim.name,
                snapshot,
            )
        return WorktreeRelease(
            WorktreeReleaseOutcome.REMOVED,
            root=root,
            branch=record.branch,
            branch_deleted=record.branch_created,
            snapshot_ref=snapshot,
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


def _unhooked(repo: Any) -> Any:
    """This repository's git, with any fsmonitor hook disabled.

    `core.fsmonitor` is a command git runs to ask what changed, and a
    repository can name any command it likes. Every read of a working tree
    here is a read of somebody else's repository, so the hook is somebody
    else's command running with the user's privileges -- the same reason
    `vibe.core.system_prompt` passes `-c core.fsmonitor=` to every git it
    spawns.

    It matters more here than it did: automatic retention inspects worktrees
    without an explicit user action.
    """
    return repo.git(c="core.fsmonitor=")


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


def _ensure_saved_directory(requested: Path) -> None:
    # Git does not store empty directories or ignored-only trees. Recreate a
    # missing saved cwd before consuming the recovery record.
    if requested.is_dir():
        return
    try:
        requested.mkdir(parents=True)
    except OSError as exc:
        raise WorktreeError(
            f"Restored worktree does not contain the saved directory {requested}."
        ) from exc


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
    # Landing inside the worktree is not enough. `root` is already resolved, so
    # anything the join still moves is a link below it, and one pointing at a
    # sibling satisfies the check above while naming a directory nobody asked
    # for. That directory becomes the session's position, its write root and a
    # trust grant, so the redirect is refused rather than followed. Committing
    # the link would otherwise make this the branch's choice, not the user's.
    if resolved_cwd != target_cwd:
        raise WorktreeError(
            f"Worktree path {target_cwd} is reached through a symbolic link to "
            f"{resolved_cwd}."
        )
    current = resolved_cwd
    while current != root:
        git_marker = current / ".git"
        if git_marker.exists() or git_marker.is_symlink():
            raise WorktreeError(
                f"Worktree path {resolved_cwd} belongs to a different git repository."
            )
        current = current.parent
    return resolved_cwd
