from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from copy import copy
from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import shutil
import tempfile
import time
import tomllib

import tomli_w

from vibe.core.paths import (
    AGENTS_MD_FILENAME,
    SCRATCH_DIR,
    TRUSTED_FOLDERS_FILE,
    find_local_config_dirs,
)
from vibe.core.utils.file_lock import acquire_file_lock, release_file_lock
from vibe.observability.logging import logger


class WorkspaceTrustDecision(StrEnum):
    TRUST_REPO = "trust_repo"
    TRUST_CWD = "trust_cwd"
    TRUST_SESSION = "trust_session"
    DECLINE = "decline"


class WorkspaceTrustStatus(StrEnum):
    TRUSTED = "trusted"
    SESSION = "session"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True)
class WorkspaceTrustPrompt:
    cwd: Path
    repo_root: Path | None
    detected_files: list[str]
    repo_detected_files: list[str]
    offer_repo_trust: bool
    repo_explicitly_untrusted: bool


def has_agents_md_file(path: Path) -> bool:
    agents_md = path / AGENTS_MD_FILENAME
    try:
        return agents_md.is_file()
    except OSError as e:
        logger.warning("Skipping unreadable path=%s: %s", agents_md, e)
        return False


def _is_git_repo_root(path: Path) -> bool:
    git_dir = path / ".git"
    try:
        return git_dir.is_dir() and (git_dir / "HEAD").is_file()
    except OSError as e:
        logger.warning("Skipping unreadable git dir=%s: %s", git_dir, e)
        return False


def find_git_repo_ancestor(path: Path) -> Path | None:
    """Closest ancestor (or *path*) with a real ``.git/HEAD``.

    Excludes the home directory and the filesystem root.
    """
    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    current = resolved
    while current not in {home, current.parent}:
        if _is_git_repo_root(current):
            return current
        current = current.parent
    return None


def find_trustable_files(path: Path) -> list[str]:
    """Relative paths of files/dirs under *path* that would modify agent behavior."""
    resolved = path.resolve()
    found: list[str] = []

    if has_agents_md_file(path):
        found.append(AGENTS_MD_FILENAME)

    for config_dir in find_local_config_dirs(path).config_dirs:
        label = f"{config_dir.relative_to(resolved)}/"
        if label not in found:
            found.append(label)

    return sorted(found)


def find_repo_trustable_files_for_cwd(cwd: Path, repo_root: Path | None) -> list[str]:
    """Repo-context files that influence *cwd* when inside a git repository.

    Includes:
    - all trustable files at ``repo_root``
    - all ``AGENTS.md`` files on ancestors between ``cwd`` and ``repo_root``
    """
    if repo_root is None:
        return []

    resolved_cwd = cwd.resolve()
    resolved_repo_root = repo_root.resolve()
    if resolved_repo_root not in resolved_cwd.parents:
        return []

    found = set(find_trustable_files(resolved_repo_root))

    current = resolved_cwd.parent
    while current != resolved_repo_root:
        if has_agents_md_file(current):
            relative_path = (current / AGENTS_MD_FILENAME).relative_to(
                resolved_repo_root
            )
            found.add(relative_path.as_posix())
        current = current.parent

    return sorted(found)


def find_untrusted_config_dirs(
    cwd: Path, *, manager: TrustedFoldersManager | None = None
) -> list[Path]:
    """Config dirs under a trusted *cwd* that are explicitly untrusted.

    These are the folders left in a broken state by the fixed bug where trusting
    a folder also marked its nested ``.vibe``/``.agents`` untrusted. Returns an
    empty list unless *cwd* itself is trusted.
    """
    manager = manager or trusted_folders_manager
    resolved_cwd = cwd.resolve()
    if manager.is_trusted(resolved_cwd) is not True:
        return []

    return sorted(
        config_dir
        for config_dir in find_local_config_dirs(resolved_cwd).config_dirs
        if manager.is_explicitly_untrusted(config_dir)
    )


def maybe_build_workspace_trust_prompt(
    cwd: Path,
    *,
    include_explicitly_untrusted: bool = False,
    manager: TrustedFoldersManager | None = None,
) -> WorkspaceTrustPrompt | None:
    manager = manager or trusted_folders_manager
    resolved_cwd = cwd.resolve()
    # Home trusts everything under it, and a scratch folder is never trusted.
    if resolved_cwd == Path.home().resolve() or is_in_scratch_dir(resolved_cwd):
        return None

    if manager.is_trusted(cwd) is True:
        return None
    if not include_explicitly_untrusted and manager.is_explicitly_untrusted(cwd):
        return None

    repo_root = find_git_repo_ancestor(cwd)
    detected_files = find_trustable_files(cwd)
    repo_detected_files = find_repo_trustable_files_for_cwd(cwd, repo_root)
    if not detected_files and not repo_detected_files:
        return None

    resolved_repo_root = repo_root.resolve() if repo_root else None
    offer_repo_trust = (
        resolved_repo_root is not None
        and resolved_repo_root in resolved_cwd.parents
        and manager.is_trusted(resolved_repo_root) is not True
        and (
            include_explicitly_untrusted
            or not manager.is_explicitly_untrusted(resolved_repo_root)
        )
    )
    repo_explicitly_untrusted = (
        resolved_repo_root is not None
        and manager.is_explicitly_untrusted(resolved_repo_root)
    )

    return WorkspaceTrustPrompt(
        cwd=cwd,
        repo_root=resolved_repo_root,
        detected_files=detected_files,
        repo_detected_files=repo_detected_files,
        offer_repo_trust=offer_repo_trust,
        repo_explicitly_untrusted=repo_explicitly_untrusted,
    )


def available_workspace_trust_decisions(
    prompt: WorkspaceTrustPrompt, *, include_session: bool = False
) -> list[WorkspaceTrustDecision]:
    decisions = [WorkspaceTrustDecision.TRUST_CWD, WorkspaceTrustDecision.DECLINE]
    if include_session:
        decisions.insert(1, WorkspaceTrustDecision.TRUST_SESSION)
    if prompt.offer_repo_trust:
        decisions.insert(0, WorkspaceTrustDecision.TRUST_REPO)
    return decisions


def apply_workspace_trust_decision(
    prompt: WorkspaceTrustPrompt,
    decision: WorkspaceTrustDecision,
    *,
    manager: TrustedFoldersManager | None = None,
) -> None:
    manager = manager or trusted_folders_manager
    match decision:
        case WorkspaceTrustDecision.TRUST_REPO if (
            prompt.offer_repo_trust and prompt.repo_root is not None
        ):
            manager.add_trusted(prompt.repo_root)
        case WorkspaceTrustDecision.TRUST_CWD:
            manager.add_trusted(prompt.cwd)
        case WorkspaceTrustDecision.TRUST_SESSION:
            manager.trust_for_session(prompt.cwd)
        case WorkspaceTrustDecision.DECLINE:
            manager.add_untrusted(prompt.cwd)
        case _:
            raise ValueError(f"Unsupported trust decision: {decision}")


class TrustedFoldersManager:
    def __init__(self) -> None:
        self._file_path = TRUSTED_FOLDERS_FILE.path
        self._trusted: list[str] = []
        self._untrusted: list[str] = []
        self._session_trusted: list[str] = []
        self._load()

    def for_session(self) -> TrustedFoldersManager:
        """Share persisted decisions without inheriting temporary grants."""
        manager = copy(self)
        manager._session_trusted = []
        return manager

    def persisted_decisions(self) -> dict[str, list[str]]:
        """What ``for_session`` keeps, for another process: ``from_decisions``."""
        return {"trusted": list(self._trusted), "untrusted": list(self._untrusted)}

    @classmethod
    def read_file(cls) -> TrustedFoldersManager:
        """A manager of its own over the trust file as it is now, for editing it.

        It shares nothing with the managers of running sessions, so its edits
        reach the file but not their in-memory decisions. Unlike the first
        load, an unreadable file raises ``OSError`` or ``TrustFileError``
        and is left as it is: it may be another process's write in progress.
        A missing file means no decisions.
        """
        trusted, untrusted = _read_while_locked(TRUSTED_FOLDERS_FILE.path)
        return cls.from_decisions({"trusted": trusted, "untrusted": untrusted})

    @classmethod
    def from_decisions(cls, decisions: dict[str, list[str]]) -> TrustedFoldersManager:
        """Answers as the manager ``persisted_decisions`` came from, without
        reading or rewriting the trust file.
        """
        manager = cls.__new__(cls)
        manager._file_path = TRUSTED_FOLDERS_FILE.path
        manager._trusted = list(decisions["trusted"])
        manager._untrusted = list(decisions["untrusted"])
        manager._session_trusted = []
        return manager

    def trust_for_session(self, path: Path) -> None:
        self._session_trusted.append(self._normalize_path(path))

    def revoke_session_trust(self, path: Path) -> None:
        """Undo one ``trust_for_session`` grant.

        Grants are counted rather than deduplicated, so revoking removes the
        one the caller made and leaves any other in place. Trust that came from
        the stored lists is untouched.
        """
        normalized = self._normalize_path(path)
        if normalized in self._session_trusted:
            self._session_trusted.remove(normalized)

    def _normalize_path(self, path: Path) -> str:
        return str(path.expanduser().resolve())

    def _load(self) -> None:
        """Read the file, creating it empty when missing.

        A file that cannot be read is left as it is, with no decision in
        memory: it may hold the user's decisions, and writing over it would
        lose them.
        """
        try:
            with ExitStack() as stack:
                locked = _try_lock(stack, self._file_path)
                if locked and not self._file_path.exists():
                    _write_lists(self._file_path, [], [])
                trusted, untrusted = _read_lists(self._file_path)
        except (OSError, TrustFileError) as exc:
            logger.warning("Ignoring the trust store: %s", exc)
            trusted, untrusted = [], []
        self._trusted[:] = trusted
        self._untrusted[:] = untrusted

    def _change(self, entry: str, trusted: bool) -> None:
        """The trust prompt's answer: in memory, then merged into the file as it is now.

        Only this change is written, under a lock, so decisions another
        manager or process wrote since this one loaded survive. A file that
        cannot be read is never overwritten: it may hold the user's own
        decisions. Such a file or a failed write leaves the decision in
        memory only, as the trust prompt always did.
        """
        _move(self._trusted, self._untrusted, entry, trusted)
        try:
            with _locked(self._file_path):
                on_disk = _read_lists(self._file_path)
                _move(*on_disk, entry, trusted)
                _write_lists(self._file_path, *on_disk)
        except (OSError, TrustFileError) as exc:
            logger.warning("Trust decision for %s not saved: %s", entry, exc)

    def edit(self, entry: str, trusted: bool | None) -> bool:
        """Move or forget an entry the file stores, as it is now; false when it stores none.

        *entry* is taken as stored, without normalization, so an entry whose
        path no longer resolves to itself can still be edited. Checked and
        written under one lock, so an entry another process just forgot is
        never written back. This manager then holds the file as written.
        Raises ``OSError`` or ``TrustFileError`` when the file cannot be read
        or written, leaving it as it was.
        """
        with _locked(self._file_path):
            on_disk = _read_lists(self._file_path)
            if entry not in on_disk[0] and entry not in on_disk[1]:
                self._trusted[:], self._untrusted[:] = on_disk
                return False
            _move(*on_disk, entry, trusted)
            _write_lists(self._file_path, *on_disk)
        self._trusted[:], self._untrusted[:] = on_disk
        return True

    def trust(self, path: Path) -> None:
        """Trust *path* in the file as it is now; this manager then holds the file as written.

        Raises ``OSError`` or ``TrustFileError`` when the file cannot be read
        or written, leaving it as it was.
        """
        entry = self._normalize_path(path)
        with _locked(self._file_path):
            on_disk = _read_lists(self._file_path)
            _move(*on_disk, entry, True)
            _write_lists(self._file_path, *on_disk)
        self._trusted[:], self._untrusted[:] = on_disk

    def _closest_decision(self, path: Path) -> tuple[bool, Path] | None:
        """``(trusted, ancestor)`` for the closest decision, ``None`` if undecided."""
        current = Path(self._normalize_path(path))
        if is_in_scratch_dir(current):
            return False, current
        while True:
            s = str(current)
            if s in self._trusted or s in self._session_trusted:
                return True, current
            if s in self._untrusted:
                return False, current
            if current.parent == current:
                return None
            current = current.parent

    def is_trusted(self, path: Path) -> bool | None:
        """Tri-state closest decision; ``None`` when no ancestor has one."""
        match self._closest_decision(path):
            case (trusted, _):
                return trusted
            case None:
                return None

    def trust_status(self, path: Path) -> WorkspaceTrustStatus:
        current = Path(self._normalize_path(path))
        if is_in_scratch_dir(current):
            return WorkspaceTrustStatus.UNTRUSTED
        while True:
            s = str(current)
            if s in self._session_trusted:
                return WorkspaceTrustStatus.SESSION
            if s in self._trusted:
                return WorkspaceTrustStatus.TRUSTED
            if s in self._untrusted:
                return WorkspaceTrustStatus.UNTRUSTED
            if current.parent == current:
                return WorkspaceTrustStatus.UNTRUSTED
            current = current.parent

    def is_explicitly_untrusted(self, path: Path) -> bool:
        """*path* literally in the untrusted list (no ancestor walk)."""
        return self._normalize_path(path) in self._untrusted

    def find_trust_root(self, path: Path) -> Path | None:
        """Closest explicitly trusted ancestor; ``None`` if a closer untrust blocks."""
        match self._closest_decision(path):
            case (True, root):
                return root
            case _:
                return None

    def add_trusted(self, path: Path) -> None:
        self._change(self._normalize_path(path), True)

    def add_untrusted(self, path: Path) -> None:
        self._change(self._normalize_path(path), False)


def is_in_scratch_dir(path: Path) -> bool:
    # A scratch folder holds what its projectless session wrote, so no decision trusts it.
    return (
        path
        .expanduser()
        .resolve()
        .is_relative_to(SCRATCH_DIR.path.expanduser().resolve())
    )


def _move(
    trusted: list[str], untrusted: list[str], entry: str, target: bool | None
) -> None:
    """Put *entry* in the list *target* names, once, and in no other."""
    for stored in (trusted, untrusted):
        while entry in stored:
            stored.remove(entry)
    if target is not None:
        (trusted if target else untrusted).append(entry)


class TrustFileError(ValueError):
    """The trust file is not the two lists of paths Vibe writes."""


def _read_lists(path: Path) -> tuple[list[str], list[str]]:
    """The trust file's ``(trusted, untrusted)``; a missing file holds none."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return [], []
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise TrustFileError(f"{path} is not valid TOML: {exc}") from exc
    lists = data.get("trusted", []), data.get("untrusted", [])
    for value in lists:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise TrustFileError(f"{path} must hold two lists of paths")
    return list(lists[0]), list(lists[1])


def _read_while_locked(path: Path) -> tuple[list[str], list[str]]:
    """``_read_lists`` under the lock when this user can take it."""
    with ExitStack() as stack:
        _try_lock(stack, path)
        return _read_lists(path)


def _try_lock(stack: ExitStack, path: Path) -> bool:
    """Hold the lock on *stack* for a reader; false when it cannot be taken.

    A reader only takes the lock to keep out of a writer's way, so one that
    cannot open it (left by a `sudo` run, a read-only home or disk) or wait
    for it reads without it. The file it reads is always whole, since writers
    swap it in. On Windows a slow writer's swap may then fail and be reported,
    but nothing is lost. A failed read is the caller's error, never swallowed.
    """
    try:
        stack.enter_context(_locked(path))
    except OSError:
        return False
    return True


_LOCK_WAIT_SECONDS = 5.0
_LOCK_RETRY_SECONDS = 0.01


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold the trust file's lock, a file beside it that is never removed.

    Every write holds it. Readers take it when they can (see ``_try_lock``),
    so they rarely hold the file open while a writer swaps it, which Windows
    refuses.
    """
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    with target.with_name(f".{target.name}.lock").open("a+b") as handle:
        while True:
            try:
                acquire_file_lock(handle)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise BlockingIOError(
                        f"{target} is locked by another Vibe process"
                    ) from exc
            time.sleep(_LOCK_RETRY_SECONDS)
        try:
            yield
        finally:
            release_file_lock(handle)


def _write_lists(path: Path, trusted: list[str], untrusted: list[str]) -> None:
    """Swap a complete new trust file in, so no reader ever sees half of one.

    A symlinked file is written through its link. The store is a
    security-decision record, so it is created owner-only; an existing file
    keeps its mode.
    """
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump({"trusted": trusted, "untrusted": untrusted}, f)
            # On disk before the swap, so a crash never leaves an empty store.
            f.flush()
            os.fsync(f.fileno())
        with suppress(FileNotFoundError):
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


trusted_folders_manager = TrustedFoldersManager()
